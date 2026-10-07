"""
notifications/services.py
═══════════════════════════════════════════════════════════════
Création des notifications (MVP §5).

- notify() : appelée par les autres apps (invitations, tickets, Stripe).
  Ne lève jamais d'exception : une notification ratée ne doit pas faire
  échouer l'action principale.
- refresh_scheduled() : rappels J-7 / J-1 / jour J et bilan du jour de
  l'organisateur. Générés à la consultation plutôt que par une tâche
  planifiée : aucun calcul pour les comptes inactifs (éco-conception),
  rien à configurer sur le serveur.
═══════════════════════════════════════════════════════════════
"""
from events.wording import pass_word
import logging
from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90          # au-delà, les notifications sont supprimées
REFRESH_EVERY = 300          # secondes entre deux calculs des rappels d'un compte
SUMMARY_LOOKBACK_DAYS = 7
SUMMARY_HOUR = 20            # « Bilan du jour · 20:00 »
# push : notifications sur le téléphone ; messages / guest_responses : par type
DEFAULT_PREFS = {'reminders': True, 'daily_summary': True, 'push': True, 'messages': True, 'guest_responses': True}


def prefs_of(user):
    return {**DEFAULT_PREFS, **(user.notification_prefs or {})}


def notify(user, type_, title, body='', *, actor=None, event=None, invitation=None, ticket=None,
           data=None, dedupe_key=None, created_at=None):
    """Crée une notification (idempotente si dedupe_key). Retourne l'objet ou None."""
    if user is None:
        return None
    fields = dict(
        type=type_, category=Notification.CATEGORY_OF[type_], title=title[:160], body=body[:255],
        actor=actor, invitation=invitation, ticket=ticket, data=data or {},
    )
    # event : objet Event ou simple identifiant
    if event is not None:
        fields['event' if hasattr(event, 'pk') else 'event_id'] = event
    if created_at:
        fields['created_at'] = created_at
    try:
        with transaction.atomic():
            if dedupe_key:
                obj, created = Notification.objects.get_or_create(user=user, dedupe_key=dedupe_key, defaults=fields)
                obj.just_created = created
            else:
                obj = Notification.objects.create(user=user, **fields)
                obj.just_created = True
        if obj.just_created:
            _push(obj)
        return obj
    except IntegrityError:
        return Notification.objects.filter(user=user, dedupe_key=dedupe_key).first()
    except Exception:
        logger.exception('Notification non créée (%s)', type_)
        return None


def _push(obj):
    try:
        from .push import push_notification
        push_notification(obj)
        # Cloche et badges à jour tout de suite dans l'application ouverte
        from messaging.realtime import broadcast_badge
        broadcast_badge(obj.user_id)
    except Exception:
        logger.exception('Push non envoyée (%s)', obj.type)


def notify_on_commit(*args, **kwargs):
    """Même chose, après la validation de la transaction en cours."""
    transaction.on_commit(lambda: notify(*args, **kwargs))


# ─────────────────────────────────────────────────────────────
# Notifications planifiées (calculées à la consultation)
# ─────────────────────────────────────────────────────────────
def _local_dt(day, hour):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)), timezone.get_current_timezone())


def _hhmm(dt):
    dt = timezone.localtime(dt)
    return f'{dt:%H}:{dt:%M}'


def _reminders(user, now):
    """Crée le rappel dû de chaque ticket. Retourne les notifications nouvelles."""
    from tickets.models import Ticket

    new = []
    tickets = (Ticket.objects.select_related('event')
               .filter(user=user, status=Ticket.Status.GENERATED,
                       event__start_date__gt=now, event__start_date__lte=now + timedelta(days=7, hours=1),
                       event__deleted_at__isnull=True))
    for t in tickets:
        start = t.event.start_date
        day_start = _local_dt(timezone.localtime(start).date(), 8)
        steps = [
            ('J-7', start - timedelta(days=7), 'Dans 7 jours', f'{t.event.title} · le {timezone.localtime(start):%d/%m} à {_hhmm(start)}'),
            ('J-1', start - timedelta(days=1), 'Demain', f'{t.event.title} à {_hhmm(start)}'),
            ('J0', min(day_start, start - timedelta(hours=2)), "Aujourd'hui", f'{t.event.title} à {_hhmm(start)}'),
        ]
        due = [s for s in steps if s[1] <= now]
        if not due:
            continue
        key, at, title, body = due[-1]           # seulement le rappel le plus récent
        n = notify(user, Notification.Type.REMINDER, title, body, event=t.event, ticket=t,
                   data={'step': key}, dedupe_key=f'reminder:{t.id}:{key}', created_at=at)
        if n is not None and getattr(n, 'just_created', False):
            new.append(n)

    # Ticket accepté mais pas encore validé / payé : rappel 3 jours avant
    pending = (Ticket.objects.select_related('event')
               .filter(user=user, status=Ticket.Status.PENDING, event__deleted_at__isnull=True,
                       event__start_date__gt=now, event__start_date__lte=now + timedelta(days=3)))
    for t in pending:
        w = pass_word(t.event)
        n = notify(user, Notification.Type.TICKET_TO_VALIDATE, f"{w['One']} en attente"
                   f"{t.event.title} approche : {'finalisez le paiement' if not t.is_free else 'validez-le'} "
                   f'pour recevoir votre QR code.', event=t.event, ticket=t,
                   dedupe_key=f'pending-reminder:{t.id}')
        if n is not None and getattr(n, 'just_created', False):
            new.append(n)
    return new


def _daily_summaries(user, now):
    """
    « Bilan du jour · 20:00 » : confirmations de la veille 20:00 au jour 20:00.
    Le planificateur le crée à 20:00 ; à défaut, il est créé à la consultation.
    """
    from django.db.models import DateTimeField, ExpressionWrapper, F
    from tickets.models import Ticket

    tz = timezone.get_current_timezone()
    today = timezone.localdate(now)
    last_day = today if now >= _local_dt(today, SUMMARY_HOUR) else today - timedelta(days=1)
    start = _local_dt(last_day - timedelta(days=SUMMARY_LOOKBACK_DAYS), SUMMARY_HOUR)
    end = _local_dt(last_day, SUMMARY_HOUR)
    shift = timedelta(hours=24 - SUMMARY_HOUR)       # 20:00 → minuit du jour suivant
    rows = (Ticket.objects
            .filter(event__organizer=user, status=Ticket.Status.GENERATED, event__deleted_at__isnull=True,
                    generated_at__gte=start, generated_at__lt=end)
            .annotate(shifted=ExpressionWrapper(F('generated_at') + shift, output_field=DateTimeField()))
            .annotate(day=TruncDate('shifted', tzinfo=tz))
            .values('event_id', 'event__title', 'day')
            .annotate(n=Count('id')))
    new = []
    for r in rows:
        n = r['n']
        obj = notify(
            user, Notification.Type.DAILY_SUMMARY,
            f"{n} nouvelle{'s' if n > 1 else ''} confirmation{'s' if n > 1 else ''}",
            f"pour {r['event__title']}",
            event=r['event_id'],
            data={'day': r['day'].isoformat(), 'count': n},
            dedupe_key=f"summary:{r['event_id']}:{r['day'].isoformat()}",
            created_at=_local_dt(r['day'], SUMMARY_HOUR),
        )
        if obj is not None and getattr(obj, 'just_created', False):
            new.append(obj)
    return new


def refresh_scheduled(user, force=False):
    """Rappels, bilans et nettoyage — au plus toutes les 5 minutes par compte."""
    key = f'notif-refresh:{user.id}'
    if not force and cache.get(key):
        return
    cache.set(key, 1, REFRESH_EVERY)
    now = timezone.now()
    prefs = prefs_of(user)
    try:
        if prefs['reminders']:
            _reminders(user, now)
        if prefs['daily_summary']:
            _daily_summaries(user, now)
        Notification.objects.filter(user=user, created_at__lt=now - timedelta(days=RETENTION_DAYS)).delete()
        # (le ménage global est aussi fait chaque nuit par notifications.tasks.nightly_cleanup)
    except Exception:
        logger.exception('Calcul des notifications planifiées impossible')


# ─────────────────────────────────────────────────────────────
# Réponses des invités (organisateur) : une notification groupée par
# événement tant qu'elle n'est pas lue (« Claire a accepté · 3 autres »).
# ─────────────────────────────────────────────────────────────
GUEST_VERBS = {'accepted': 'a accepté votre invitation', 'declined': 'a décliné votre invitation',
               'joined': 'participe', 'paid': 'a payé sa place'}


def notify_guest_activity(event, guest, kind, name=None):
    """guest : compte de l'invité, ou None (invité sans compte : name = nom du contact)."""
    organizer = event.organizer
    if organizer is None or (guest is not None and organizer.id == guest.id):
        return None
    if not prefs_of(organizer).get('guest_responses', True):
        return None
    name = (guest.full_name or guest.first_name) if guest is not None else (name or 'Un invité')
    now = timezone.now()
    existing = Notification.objects.filter(user=organizer, type=Notification.Type.GUEST_RESPONSE, event=event,
                                           read_at__isnull=True).first()
    if existing is None:
        return notify(organizer, Notification.Type.GUEST_RESPONSE, name,
                      f'{GUEST_VERBS[kind]} · {event.title}'[:255], actor=guest, event=event,
                      data={'counts': {kind: 1}, 'total': 1})
    data = existing.data or {}
    counts = data.get('counts', {})
    counts[kind] = counts.get(kind, 0) + 1
    total = data.get('total', 1) + 1
    others = total - 1
    existing.title = name[:160]
    existing.body = (f"{GUEST_VERBS[kind]} · {others} autre{'s' if others > 1 else ''} réponse{'s' if others > 1 else ''}"
                     f' · {event.title}')[:255]
    existing.actor, existing.created_at = guest, now
    existing.data = {**data, 'counts': counts, 'total': total}
    existing.save(update_fields=['title', 'body', 'actor', 'created_at', 'data'])
    from .push import push_notification
    try:
        push_notification(existing)
    except Exception:
        logger.exception('Push non envoyée (guest_response)')
    return existing


def notify_event_full(event):
    return notify(event.organizer, Notification.Type.EVENT_FULL, 'Complet !',
                  f'{event.title} a atteint ses {event.max_guests} places.', event=event,
                  dedupe_key=f'event-full:{event.id}:{event.max_guests}')
