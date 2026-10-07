"""
minisite/ai.py — plusieurs modèles d'IA gratuits, chacun dans son rôle
════════════════════════════════════════════════════════════════
Rôles (settings.MINISITE_ROLES, ordre = priorité, repli automatique) :
  direction  direction artistique des 6 propositions — reçoit UNIQUEMENT des
             données non sensibles (type, ambiance, couleurs, saison…) : c'est le
             seul rôle permis à Mistral (offre gratuite = données réutilisées)
  copy       textes d'ambiance (titres, accroches) à partir du contenu saisi
  review     relecture : orthographe, ton, suppression de tout fait inventé
Fournisseurs : Gemini (Google AI Studio), Groq, OpenRouter, Mistral — clés dans
les variables d'environnement. Sans clé, ou si tous échouent, le compositeur
garantit quand même 6 propositions (textes de repli) : la fonction ne tombe jamais.
Chaque appel est journalisé (modèle, durée, succès) pour comparer les modèles.
════════════════════════════════════════════════════════════════
"""
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from django.conf import settings

from . import catalog

logger = logging.getLogger(__name__)

# Ne reçoivent jamais de données saisies par l'utilisateur (titres, descriptions…)
SENSITIVE_FORBIDDEN = {'mistral'}

OPENAI_STYLE = {
    'groq': 'https://api.groq.com/openai/v1/chat/completions',
    'openrouter': 'https://openrouter.ai/api/v1/chat/completions',
    'mistral': 'https://api.mistral.ai/v1/chat/completions',
}


class ProviderError(Exception):
    pass


def _key(provider):
    return getattr(settings, f'{provider.upper()}_API_KEY', '') or ''


def _model(provider):
    return settings.MINISITE_MODELS.get(provider, '')


def configured(provider):
    return bool(_key(provider) and _model(provider))


def _extract_json(text):
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?|```$', '', text, flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ProviderError('réponse non JSON')


def call(provider, system, user, timeout=None):
    timeout = timeout or settings.MINISITE_AI_TIMEOUT
    if provider == 'gemini':
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{_model(provider)}:generateContent'
        body = {
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': [{'text': user}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.9},
        }
        r = requests.post(url, json=body, timeout=timeout, headers={'x-goog-api-key': _key(provider)})
        if r.status_code != 200:
            raise ProviderError(f'HTTP {r.status_code}')
        try:
            text = r.json()['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError, ValueError):
            raise ProviderError('réponse vide')
        return _extract_json(text)
    if provider in OPENAI_STYLE:
        body = {
            'model': _model(provider), 'temperature': 0.9,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'response_format': {'type': 'json_object'},
        }
        headers = {'Authorization': f'Bearer {_key(provider)}'}
        if provider == 'openrouter':
            headers.update({'HTTP-Referer': settings.PUBLIC_BASE_URL, 'X-Title': 'Easevent'})
        r = requests.post(OPENAI_STYLE[provider], json=body, timeout=timeout, headers=headers)
        if r.status_code != 200:
            raise ProviderError(f'HTTP {r.status_code}')
        try:
            text = r.json()['choices'][0]['message']['content']
        except (KeyError, IndexError, ValueError):
            raise ProviderError('réponse vide')
        return _extract_json(text)
    raise ProviderError('fournisseur inconnu')


def run_role(role, system, user, validate, providers=None):
    """Essaie les fournisseurs du rôle dans l'ordre ; renvoie (résultat validé ou None, journal, fournisseur)."""
    log = []
    for provider in (providers if providers is not None else settings.MINISITE_ROLES.get(role, ())):
        if role != 'direction' and provider in SENSITIVE_FORBIDDEN:
            continue
        if not configured(provider):
            continue
        started = time.monotonic()
        try:
            result = validate(call(provider, system, user))
            if not result:
                raise ProviderError('réponse invalide')
            log.append({'role': role, 'provider': provider, 'model': _model(provider), 'ok': True,
                        'ms': int((time.monotonic() - started) * 1000)})
            return result, log, provider
        except (ProviderError, requests.RequestException, ValueError, TypeError) as exc:
            log.append({'role': role, 'provider': provider, 'model': _model(provider), 'ok': False,
                        'ms': int((time.monotonic() - started) * 1000), 'error': str(exc)[:120]})
            logger.info('Mini-site : %s indisponible pour %s (%s)', provider, role, exc)
    return None, log, None


# ── Direction artistique ─────────────────────────────────────────────────────
DIRECTION_SYSTEM = (
    "Tu es directeur artistique senior, spécialiste des invitations et sites d'événements haut de gamme. "
    "Tu choisis, pour 6 directions artistiques imposées, les réglages d'un système de design. "
    "Chaque proposition doit être nettement différente des autres et cohérente avec l'événement, ses couleurs, "
    "sa saison et son ambiance. Réponds uniquement en JSON valide."
)


def direction_prompt(brief):
    allowed = {
        'directions': list(catalog.DIRECTION_ORDER),
        'font_pair': list(catalog.FONT_PAIRS), 'radius': list(catalog.RADII), 'density': list(catalog.DENSITIES),
        'ornament': list(catalog.ORNAMENTS), 'harmony': list(catalog.HARMONIES),
        'hero': list(catalog.SECTIONS['hero']['variants']),
    }
    return (
        f"Événement (données non sensibles) : {json.dumps(brief, ensure_ascii=False)}\n"
        f"Valeurs autorisées : {json.dumps(allowed, ensure_ascii=False)}\n"
        'Réponds : {"proposals": [{"direction": "...", "font_pair": "...", "radius": "...", "density": "...", '
        '"ornament": "...", "harmony": "...", "hero": "...", "mood": "trois mots en français"}]} '
        "— exactement une entrée par direction, toutes les paires de polices différentes, tous les « hero » différents."
    )


def validate_direction(data):
    if not isinstance(data, dict) or not isinstance(data.get('proposals'), list):
        return None
    out = {}
    enums = {'font_pair': catalog.FONT_PAIRS, 'radius': catalog.RADII, 'density': catalog.DENSITIES,
             'ornament': catalog.ORNAMENTS, 'harmony': catalog.HARMONIES, 'hero': catalog.SECTIONS['hero']['variants']}
    for item in data['proposals']:
        if not isinstance(item, dict) or item.get('direction') not in catalog.DIRECTIONS:
            continue
        clean = {k: item[k] for k, allowed in enums.items() if item.get(k) in allowed}
        mood = item.get('mood')
        if isinstance(mood, str):
            clean['mood'] = re.sub(r'[^\w\s,’\'-]', '', mood)[:40].strip()
        out[item['direction']] = clean
    return out if len(out) >= 3 else None


# ── Rédaction ────────────────────────────────────────────────────────────────
TONES = {
    'editorial': 'raffiné et littéraire, phrases élégantes',
    'immersive': 'cinématographique, court et percutant',
    'minimal': 'sobre, précis, très peu de mots',
    'festive': 'joyeux, énergique, chaleureux',
    'luxe': 'solennel, distingué, précieux',
    'playful': 'ludique, complice, plein d’humour bienveillant',
}
COPY_SYSTEM = (
    "Tu es concepteur-rédacteur francophone pour des invitations d'événements. Tu écris en français impeccable, "
    "au vouvoiement, sans emoji, sans markdown. RÈGLE ABSOLUE : n'invente AUCUNE information factuelle "
    "(heure, prix, lieu, nom, programme, nombre) — les informations pratiques sont affichées ailleurs. "
    "Tu écris uniquement des textes d'ambiance. Réponds uniquement en JSON valide."
)


def copy_prompt(f):
    fields = {k: {field: f'≤{n} caractères' for field, n in v['copy'].items()} for k, v in catalog.SECTIONS.items()}
    event = {k: f[k] for k in ('type_label', 'title', 'description', 'ambiance', 'ambiance_label', 'season',
                               'is_paid', 'is_online', 'dress_code', 'city')}
    return (
        f"Événement : {json.dumps(event, ensure_ascii=False)}\n"
        f"Tons à respecter par direction : {json.dumps(TONES, ensure_ascii=False)}\n"
        f"Champs à rédiger pour chaque direction : {json.dumps(fields, ensure_ascii=False)}\n"
        'Réponds : {"proposals": [{"direction": "editorial", "hero": {"kicker": "...", "subtitle": "..."}, '
        '"intro": {"title": "...", "body": "..."}, ...}]} — une entrée par direction '
        f"({', '.join(catalog.DIRECTION_ORDER)}), chacune avec son propre ton."
    )


REVIEW_SYSTEM = (
    "Tu es correcteur et directeur éditorial francophone. Tu relis des textes d'invitation : orthographe, "
    "typographie française, cohérence du ton. Tu SUPPRIMES toute information factuelle qui n'est pas dans "
    "les données de l'événement (heure, prix, lieu, nom, programme). Tu renvoies la même structure JSON, corrigée."
)


def review_prompt(f, proposals):
    event = {k: f[k] for k in ('type_label', 'title', 'description', 'date_text', 'time_text', 'city',
                               'price_text', 'dress_code')}
    return (f"Données de l'événement : {json.dumps(event, ensure_ascii=False)}\n"
            f'Textes à relire : {json.dumps({"proposals": proposals}, ensure_ascii=False)}')


def validate_copy_factory(f):
    from .copy import clean

    def validate(data):
        if not isinstance(data, dict) or not isinstance(data.get('proposals'), list):
            return None
        out = {}
        for item in data['proposals']:
            if isinstance(item, dict) and item.get('direction') in catalog.DIRECTIONS:
                cleaned = clean(item, f)
                if cleaned:
                    out[item['direction']] = cleaned
        return out if len(out) >= 3 else None
    return validate


def generate(f, brief, on_step=None):
    """Direction artistique et rédaction en parallèle, puis relecture. Renvoie (art, copies, journal)."""
    validate_copy = validate_copy_factory(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        art_job = pool.submit(run_role, 'direction', DIRECTION_SYSTEM, direction_prompt(brief), validate_direction)
        copy_job = pool.submit(run_role, 'copy', COPY_SYSTEM, copy_prompt(f), validate_copy)
        art, art_log, _ = art_job.result()
        copies, copy_log, copy_provider = copy_job.result()
    journal = art_log + copy_log
    if copies:
        if on_step:
            on_step('review')
        # Relecture par un AUTRE modèle que le rédacteur (regard neuf)
        original = settings.MINISITE_ROLES.get('review', ())
        others = [p for p in original if p != copy_provider] + [p for p in original if p == copy_provider]
        payload = [{'direction': d, **c} for d, c in copies.items()]
        reviewed, review_log, _ = run_role('review', REVIEW_SYSTEM, review_prompt(f, payload), validate_copy, others)
        journal += review_log
        if reviewed:
            for d, c in reviewed.items():
                copies.setdefault(d, {}).update(c)
    return art or {}, copies or {}, journal

