"""
minisite/services.py — génération, choix, retouches et affichage des mini-sites
"""
import copy as deepcopy_mod
import logging
import random
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import ai, catalog, composer, dataset
from .copy import CTRL
from .facts import art_brief, facts
from .models import MiniSiteGeneration, MiniSiteProposal

logger = logging.getLogger(__name__)
STALE = timedelta(minutes=6)
PLAN_NAMES = {'free': 'Gratuit', 'standard': 'Standard', 'pro': 'Pro'}


class MiniSiteError(Exception):
    def __init__(self, message, code='invalid', status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def limit_for(user):
    limits = settings.MINISITE_GENERATION_LIMITS
    return limits.get(user.subscription_plan, limits['free'])


def usage(event):
    used = event.minisite_generations.exclude(status=MiniSiteGeneration.Status.FAILED).count()
    limit = limit_for(event.organizer)
    return {'used': used, 'limit': limit, 'remaining': None if limit is None else max(0, limit - used)}


# ── Génération ──────────────────────────────────────────────────────────────
def start(event, user):
    if event.deleted_at is not None:
        raise MiniSiteError('Événement introuvable.', 'not_found', 404)
    if not (event.theme or '').strip():
        # Le thème est le fil conducteur de toute la rédaction : sans lui, le texte serait générique
        raise MiniSiteError("Indiquez d'abord le thème de l'événement : tout le contenu du mini-site en découle.",
                            'theme_required', 409)
    running = event.minisite_generations.filter(
        status__in=(MiniSiteGeneration.Status.PENDING, MiniSiteGeneration.Status.RUNNING),
        created_at__gte=timezone.now() - STALE).first()
    if running:
        return running
    quota = usage(event)
    if quota['limit'] is not None and quota['used'] >= quota['limit']:
        plan = PLAN_NAMES.get(user.subscription_plan, user.subscription_plan)
        raise MiniSiteError(
            f"Le plan {plan} permet {quota['limit']} générations de mini-site par événement. "
            "Passez au plan supérieur pour en obtenir davantage, ou retouchez la proposition choisie.",
            'plan_limit', 403)
    last = event.minisite_generations.filter(status=MiniSiteGeneration.Status.DONE).first()
    if last and not last.chosen_at:
        dataset.write('regenerate', {'generation': str(last.id), 'event_type': event.event_type})
    gen = MiniSiteGeneration.objects.create(event=event, requested_by=user, step='queued')
    transaction.on_commit(lambda: dispatch(gen.id))
    return gen


def dispatch(gen_id):
    if settings.MINISITE_ASYNC:
        try:
            from .tasks import generate_minisite
            generate_minisite.delay(str(gen_id))
            return
        except Exception:                                   # file de tâches indisponible : on génère ici
            logger.exception('Mini-site : Celery indisponible, génération immédiate')
    run(gen_id)


def _step(gen_id, step):
    MiniSiteGeneration.objects.filter(pk=gen_id).update(step=step)


def run(gen_id):
    with transaction.atomic():
        gen = MiniSiteGeneration.objects.select_for_update().select_related('event', 'event__organizer') \
            .filter(pk=gen_id).first()
        if not gen or gen.status not in (MiniSiteGeneration.Status.PENDING, MiniSiteGeneration.Status.RUNNING):
            return
        gen.status, gen.step = MiniSiteGeneration.Status.RUNNING, 'direction'
        gen.save(update_fields=['status', 'step'])
    event = gen.event
    try:
        f = facts(event)
        art, copies, journal = ai.generate(f, art_brief(f), on_step=lambda s: _step(gen.id, s))
        _step(gen.id, 'composition')
        seed = uuid.UUID(str(gen.id)).int % (2 ** 31)
        batch = composer.compose_batch(f, seed, art, copies)
        # Revue du directeur de création : notes, verdict, corrections validées une à une
        review, review_log = ai.critique(f, batch, on_step=lambda s: _step(gen.id, s))
        journal += review_log
        for spec in batch:
            r = review.get(spec['direction'])
            if r:
                others = {o['theme']['fonts'] for o in batch if o is not spec}
                spec['critique'] = {'scores': r['scores'], 'verdict': r['verdict'],
                                    'applied': composer.apply_critique(spec, r, f, others)}
        _step(gen.id, 'composition')
        rng = random.Random(seed)
        taken = set()
        for _ in range(6):
            composer.finalize(batch, rng, frozenset(taken))
            clash = set(MiniSiteProposal.objects.filter(fingerprint__in=[s['fingerprint'] for s in batch])
                        .values_list('fingerprint', flat=True))
            if not clash:
                break
            taken |= clash
        with transaction.atomic():
            MiniSiteProposal.objects.bulk_create([
                MiniSiteProposal(generation=gen, index=i, direction=s['direction'], spec=s, fingerprint=s['fingerprint'])
                for i, s in enumerate(batch)])
            gen.status, gen.step, gen.finished_at = MiniSiteGeneration.Status.DONE, 'done', timezone.now()
            gen.engine = {'calls': journal, 'ai_direction': bool(art), 'ai_copy': bool(copies), 'ai_critic': bool(review)}
            gen.save(update_fields=['status', 'step', 'finished_at', 'engine'])
        dataset.write('generation', {
            'generation': str(gen.id), 'organizer': dataset.pseudonym(event.organizer_id),
            'plan': event.organizer.subscription_plan, 'brief': art_brief(f),
            'content': {k: f[k] for k in ('type_label', 'title', 'theme', 'description', 'ambiance_label', 'dress_code', 'city')},
            'ai': {'direction': art, 'copy': copies}, 'calls': journal,
            'proposals': [{'direction': s['direction'], 'fingerprint': s['fingerprint'], 'concept': s.get('concept'),
                           'critique': s.get('critique'), 'theme': s['theme'], 'sections': s['sections'],
                           'copy': s['copy']} for s in batch],
        })
        from notifications.services import notify
        notify(event.organizer, 'minisite_ready', 'Votre mini-site est prêt',
               f'6 propositions pour {event.title} vous attendent : choisissez votre préférée.',
               event=event, data={'generation_id': str(gen.id)}, dedupe_key=f'minisite:{gen.id}')
    except Exception:
        logger.exception('Mini-site : génération %s échouée', gen.id)
        MiniSiteGeneration.objects.filter(pk=gen.id).update(
            status=MiniSiteGeneration.Status.FAILED, step='failed', finished_at=timezone.now(),
            error='Erreur pendant la génération.')


def serialize(gen):
    data = {
        'id': str(gen.id), 'status': gen.status, 'step': gen.step,
        'created_at': gen.created_at.isoformat(), 'quota': usage(gen.event),
        'proposals': [],
    }
    if gen.status == MiniSiteGeneration.Status.DONE:
        data['proposals'] = [{'id': str(p.id), 'index': p.index, 'direction': p.direction,
                              'label': p.spec.get('label'), 'mood': p.spec.get('mood', ''), 'spec': p.spec,
                              'chosen': p.chosen} for p in gen.proposals.all()]
    if gen.status == MiniSiteGeneration.Status.FAILED:
        data['error'] = 'La génération a échoué. Réessayez : cette tentative n’est pas décomptée.'
    return data


# ── Choix ───────────────────────────────────────────────────────────────────
def choose(event, proposal_id):
    try:
        proposal = MiniSiteProposal.objects.select_related('generation').get(
            pk=proposal_id, generation__event=event, generation__status=MiniSiteGeneration.Status.DONE)
    except (MiniSiteProposal.DoesNotExist, ValueError):
        raise MiniSiteError('Proposition introuvable.', 'not_found', 404)
    gen, now = proposal.generation, timezone.now()
    with transaction.atomic():
        MiniSiteProposal.objects.filter(generation__event=event, chosen=True).update(chosen=False)
        proposal.chosen = True
        proposal.save(update_fields=['chosen'])
        if not gen.chosen_at:
            gen.chosen_at = now
            gen.save(update_fields=['chosen_at'])
        event.minisite_config = {'spec': proposal.spec, 'proposal': str(proposal.id), 'generation': str(gen.id),
                                 'chosen_at': now.isoformat(), 'edited': False}
        event.save(update_fields=['minisite_config', 'updated_at'])
    dataset.write('choice', {
        'generation': str(gen.id), 'index': proposal.index, 'direction': proposal.direction,
        'fingerprint': proposal.fingerprint,
        'seconds_to_choose': int((now - gen.finished_at).total_seconds()) if gen.finished_at else None,
    })
    return event.minisite_config


# ── Retouches de l'organisateur ─────────────────────────────────────────────
def _user_text(value, limit):
    if not isinstance(value, str):
        raise MiniSiteError('Texte invalide.')
    text = ' '.join(CTRL.sub('', value).split())
    if len(text) > limit:
        raise MiniSiteError(f'Ce texte fait {limit} caractères au plus.')
    return text


def _own_upload(event, url):
    """Une photo envoyée par l'organisateur sur notre Cloudinary (jamais une adresse extérieure)."""
    import cloudinary
    from adminpanel.keys import apply_cloudinary
    cloud = apply_cloudinary().cloud_name or ''
    prefix = f'https://res.cloudinary.com/{cloud}/image/upload/'
    from events.team import manager_ids
    owners = any(f'/easevent/events/{uid}/' in url for uid in manager_ids(event))
    return bool(cloud) and url.startswith(prefix) and owners \
        and len(url) <= 500 and not any(ch in url for ch in ' "\'<>')


def _banner_edit(event, hero, banner):
    if not isinstance(banner, dict):
        raise MiniSiteError('Bannière invalide.')
    done = {}
    if 'image' in banner:
        url = banner['image']
        if url in (None, ''):
            hero.pop('image', None)
        elif not isinstance(url, str) or not _own_upload(event, url):
            raise MiniSiteError('Photo invalide : choisissez-la depuis votre téléphone.')
        else:
            hero['image'] = url
        done['image'] = bool(url)
    if 'filter' in banner:
        if banner['filter'] not in catalog.HERO_FILTERS:
            raise MiniSiteError('Filtre indisponible.')
        hero['filter'] = done['filter'] = banner['filter']
    if 'tint' in banner:
        if banner['tint'] not in catalog.HERO_TINTS:
            raise MiniSiteError('Teinte indisponible.')
        hero['tint'] = done['tint'] = banner['tint']
    if 'variant' in banner:
        if banner['variant'] not in catalog.SECTIONS['hero']['variants']:
            raise MiniSiteError("Mise en page d'accueil indisponible.")
        hero['variant'] = done['variant'] = banner['variant']
    if not done:
        raise MiniSiteError('Bannière invalide.')
    return done


def edit(event, data):
    cfg = event.minisite_config or {}
    if not cfg.get('spec'):
        raise MiniSiteError("Choisissez d'abord un modèle de mini-site.", 'no_minisite', 409)
    spec = deepcopy_mod.deepcopy(cfg['spec'])
    theme, changes = spec['theme'], {}

    harmony = data.get('harmony')
    if harmony is not None and harmony != theme['harmony']:
        alts = theme.get('alternatives') or {}
        if harmony not in alts:
            raise MiniSiteError('Palette indisponible.')
        alts[theme['harmony']] = theme['colors']
        theme['colors'] = alts.pop(harmony)
        theme['harmony'] = harmony
        changes['harmony'] = harmony

    fonts = data.get('fonts')
    if fonts is not None and fonts != theme['fonts']:
        if fonts not in catalog.FONT_PAIRS:
            raise MiniSiteError('Police indisponible.')
        theme['fonts'] = changes['fonts'] = fonts

    kinds = [s['kind'] for s in spec['sections']]
    order = data.get('order')
    if order is not None:
        if not isinstance(order, list) or sorted(order) != sorted(kinds):
            raise MiniSiteError("L'ordre des sections est invalide.")
        if order[0] != catalog.FIXED_FIRST or order[-1] != catalog.FIXED_LAST:
            raise MiniSiteError("L'accueil reste en haut et le pied de page en bas.")
        by_kind = {s['kind']: s for s in spec['sections']}
        spec['sections'] = [by_kind[k] for k in order]
        changes['order'] = order

    hidden = data.get('hidden')
    if hidden is not None:
        if not isinstance(hidden, list) or any(k not in kinds for k in hidden):
            raise MiniSiteError('Sections invalides.')
        if any(k in ('hero', 'cta', 'footer') for k in hidden):
            raise MiniSiteError("L'accueil, le bouton de participation et le pied de page restent visibles.")
        spec['hidden'] = changes['hidden'] = sorted(set(hidden))

    texts = data.get('copy')
    if texts is not None:
        if not isinstance(texts, dict):
            raise MiniSiteError('Textes invalides.')
        for kind, fields in texts.items():
            if kind not in spec['copy'] or not isinstance(fields, dict):
                raise MiniSiteError('Section inconnue.')
            for field, value in fields.items():
                limit = catalog.SECTIONS[kind]['copy'].get(field)
                if not limit:
                    raise MiniSiteError('Champ inconnu.')
                spec['copy'][kind][field] = _user_text(value, limit)
        changes['copy'] = texts

    banner = data.get('banner')
    if banner is not None:
        changes['banner'] = _banner_edit(event, spec['sections'][0], banner)

    if not changes:
        return cfg
    cfg = {**cfg, 'spec': spec, 'edited': True, 'edited_at': timezone.now().isoformat()}
    event.minisite_config = cfg
    event.save(update_fields=['minisite_config', 'updated_at'])
    dataset.write('edit', {'generation': cfg.get('generation'), 'direction': spec.get('direction'), 'changes': changes})
    return cfg
