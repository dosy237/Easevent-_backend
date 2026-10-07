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

# Rôles où la rapidité prime (le modèle de secours rapide passe en premier)
FAST_FIRST = {'review', 'assistant'}     # modèle rapide d'abord (quota gratuit plus large)

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
    from adminpanel.keys import get_key
    return get_key(f'{provider.upper()}_API_KEY')


def _models(provider):
    """Modèles d'un fournisseur, dans l'ordre (réglage séparé par des virgules : qualité puis secours)."""
    return [m.strip() for m in str(settings.MINISITE_MODELS.get(provider, '')).split(',') if m.strip()]


def configured(provider):
    return bool(_key(provider) and _models(provider))


def _extract_json(text):
    """Premier objet JSON complet de la réponse (ignore les balises ``` et le texte parasite après)."""
    text = re.sub(r'^```(?:json)?|```$', '', (text or '').strip(), flags=re.M).strip()
    start = text.find('{')
    if start < 0:
        raise ProviderError('réponse non JSON')
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except ValueError:
        raise ProviderError('réponse non JSON')


# Rôles où une réflexion courte suffit (Gemini 3 : bien plus rapide, qualité équivalente)
LIGHT_THINKING = {'direction', 'copy', 'review'}


RETRY_429_MAX = 25          # secondes : au-delà, on passe directement au modèle de secours


def _retry_delay(r):
    """Délai conseillé par le fournisseur après un 429 (en-tête Retry-After, ou retryDelay de Gemini)."""
    try:
        return float((getattr(r, 'headers', None) or {}).get('Retry-After'))
    except (TypeError, ValueError):
        pass
    try:
        for d in r.json().get('error', {}).get('details', []):
            if str(d.get('retryDelay', '')).endswith('s'):
                return float(d['retryDelay'][:-1])
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def _exhausted_key(provider, model):
    return f'minisite:ai:exhausted:{provider}:{model}'


def exhausted(provider, model):
    from django.core.cache import cache
    return bool(cache.get(_exhausted_key(provider, model)))


def _post(url, quota_key=None, **kw):
    """
    Quota gratuit atteint (429) : une seule nouvelle tentative si l'attente conseillée est courte ;
    sinon (quota du jour épuisé), le modèle est écarté jusqu'à sa remise à zéro (6 h au plus).
    """
    r = requests.post(url, **kw)
    if r.status_code == 429:
        delay = _retry_delay(r)
        if delay is not None and delay <= RETRY_429_MAX:
            time.sleep(delay + 0.5)
            r = requests.post(url, **kw)
        elif quota_key and delay:
            from django.core.cache import cache
            cache.set(quota_key, 1, int(min(delay, 6 * 3600)))
    return r


def call(provider, model, system, user, timeout=None, role=None):
    timeout = timeout or settings.MINISITE_AI_TIMEOUTS.get(role, settings.MINISITE_AI_TIMEOUT)
    if provider == 'gemini':
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        body = {
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': [{'text': user}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.9},
        }
        if model.startswith('gemini-3') and role in LIGHT_THINKING:
            body['generationConfig']['thinkingConfig'] = {'thinkingLevel': 'low'}
        r = _post(url, quota_key=_exhausted_key(provider, model), json=body, timeout=timeout,
                  headers={'x-goog-api-key': _key(provider)})
        if r.status_code != 200:
            raise ProviderError(f'HTTP {r.status_code}')
        try:
            text = r.json()['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError, ValueError):
            raise ProviderError('réponse vide')
        return _extract_json(text)
    if provider in OPENAI_STYLE:
        body = {
            'model': model, 'temperature': 0.9,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'response_format': {'type': 'json_object'},
        }
        headers = {'Authorization': f'Bearer {_key(provider)}'}
        if provider == 'openrouter':
            headers.update({'HTTP-Referer': settings.PUBLIC_BASE_URL, 'X-Title': 'Easevent'})
        r = _post(OPENAI_STYLE[provider], quota_key=_exhausted_key(provider, model), json=body, timeout=timeout, headers=headers)
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
        models = _models(provider)
        if role in FAST_FIRST:
            models = models[::-1]                    # relecture : le modèle rapide d'abord
        for model in models:
            if exhausted(provider, model):
                log.append({'role': role, 'provider': provider, 'model': model, 'ok': False, 'ms': 0, 'error': 'quota du jour épuisé'})
                continue
            started = time.monotonic()
            try:
                prompt = user(provider) if callable(user) else user
                result = validate(call(provider, model, system, prompt, role=role))
                if not result:
                    raise ProviderError('réponse invalide')
                log.append({'role': role, 'provider': provider, 'model': model, 'ok': True,
                            'ms': int((time.monotonic() - started) * 1000)})
                return result, log, provider
            except (ProviderError, requests.RequestException, ValueError, TypeError) as exc:
                log.append({'role': role, 'provider': provider, 'model': model, 'ok': False,
                            'ms': int((time.monotonic() - started) * 1000), 'error': str(exc)[:120]})
                logger.info('Mini-site : %s/%s indisponible pour %s (%s)', provider, model, role, exc)
    return None, log, None


from .prompts import (COPY_SYSTEM, CRITIC_SYSTEM, DIRECTION_SYSTEM, REVIEW_SYSTEM, copy_prompt,
                      critic_prompt, direction_prompt, review_prompt)


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
        concept = item.get('concept')
        if isinstance(concept, str):
            clean['concept'] = re.sub(r'[<>{}\[\]`*#|\\]', '', concept)[:160].strip()
        if {'font_pair', 'harmony', 'hero'} & set(clean):
            out[item['direction']] = clean
    return out if len(out) >= 3 else None


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
                    concept = item.get('concept')
                    if isinstance(concept, str) and concept.strip():
                        cleaned['_concept'] = re.sub(r'[<>{}\[\]`*#|\\]', '', concept)[:160].strip()
                    out[item['direction']] = cleaned
        return out if len(out) >= 3 else None
    return validate


def generate(f, brief, on_step=None):
    """Direction artistique et rédaction en parallèle, puis relecture. Renvoie (art, copies, journal)."""
    validate_copy = validate_copy_factory(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        # Mistral (offre gratuite : données réutilisées) ne reçoit pas le thème, seul texte saisi du brief
        art_job = pool.submit(run_role, 'direction', DIRECTION_SYSTEM,
                              lambda p: direction_prompt({k: v for k, v in brief.items() if p not in SENSITIVE_FORBIDDEN or k != 'theme'}),
                              validate_direction)
        copy_job = pool.submit(run_role, 'copy', COPY_SYSTEM, copy_prompt(f), validate_copy)
        art, art_log, _ = art_job.result()
        copies, copy_log, copy_provider = copy_job.result()
    journal = art_log + copy_log
    # Relecture séparée facultative : le directeur de création relit et réécrit déjà les textes
    if copies and settings.MINISITE_REVIEW:
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



# ── Critique (directeur de création) ────────────────────────────────────────
CRITERIA = ('hierarchy', 'color', 'typography', 'rhythm', 'copy', 'distinctiveness')
ACTION_TYPES = {'variant', 'tone', 'align', 'move', 'harmony', 'fonts', 'ornament', 'density', 'radius', 'copy'}
MAX_ACTIONS = 6


def validate_critic(data):
    if not isinstance(data, dict) or not isinstance(data.get('proposals'), list):
        return None
    out = {}
    for item in data['proposals']:
        if not isinstance(item, dict) or item.get('direction') not in catalog.DIRECTIONS:
            continue
        scores = item.get('scores') if isinstance(item.get('scores'), dict) else {}
        clean_scores = {}
        for c in CRITERIA:
            try:
                clean_scores[c] = max(0, min(10, float(scores.get(c))))
            except (TypeError, ValueError):
                pass
        actions = [a for a in (item.get('actions') or []) if isinstance(a, dict) and a.get('type') in ACTION_TYPES]
        verdict = item.get('verdict') if isinstance(item.get('verdict'), str) else ''
        out[item['direction']] = {'scores': clean_scores, 'verdict': re.sub(r'[<>{}`*#|\\]', '', verdict)[:200],
                                  'actions': actions[:MAX_ACTIONS]}
    return out if len(out) >= 3 else None


def critique(f, specs, on_step=None):
    """Revue critique des 6 propositions. Renvoie (critique par direction, journal)."""
    from . import colors
    if on_step:
        on_step('critique')
    measures = {}
    for spec in specs:
        c = spec['theme']['colors']
        measures[spec['direction']] = {
            'texte/fond': round(colors.contrast(c['text'], c['bg']), 1),
            'secondaire/fond': round(colors.contrast(c['muted'], c['bg']), 1),
            'bouton': round(colors.contrast(c['onPrimary'], c['primary']), 1),
            'inverse': round(colors.contrast(c['inverseText'], c['inverseBg']), 1),
        }
    result, log, _ = run_role('critic', CRITIC_SYSTEM, critic_prompt(f, specs, measures), validate_critic)
    return result or {}, log
