"""
adminpanel/key_checks.py — éviter les erreurs de saisie des clés de service

1. Format (à l'enregistrement, sans appel réseau) : préfixe attendu, espaces ou
   guillemets collés par erreur, clé de test en production…
2. Test en direct (bouton « Tester » de l'admin, `manage.py keys check`) : un
   appel en lecture seule au service, qui dit si la clé est acceptée. Aucun
   paiement, aucun SMS, aucun email n'est envoyé.
"""
import re

import requests

TIMEOUT = 10

# nom → (expression attendue, explication en cas d'erreur)
FORMATS = {
    'STRIPE_SECRET_KEY': (r'^(sk|rk)_(live|test)_[A-Za-z0-9]{10,}$', 'commence par « sk_live_ » (ou « sk_test_ » pour les essais)'),
    'STRIPE_WEBHOOK_SECRET': (r'^whsec_[A-Za-z0-9+/=]{10,}$', 'commence par « whsec_ »'),
    'GEMINI_API_KEY': (r'^(AIza[0-9A-Za-z_\-]{30,}|AQ\.[A-Za-z0-9_.\-]{20,})$', 'commence par « AIza » ou « AQ. »'),
    'GOOGLE_MAPS_API_KEY': (r'^AIza[0-9A-Za-z_\-]{30,}$', 'commence par « AIza »'),
    'GROQ_API_KEY': (r'^gsk_[A-Za-z0-9]{20,}$', 'commence par « gsk_ »'),
    'OPENROUTER_API_KEY': (r'^sk-or-[A-Za-z0-9\-]{20,}$', 'commence par « sk-or- »'),
    'MISTRAL_API_KEY': (r'^[A-Za-z0-9]{20,}$', 'suite de lettres et de chiffres (32 caractères environ)'),
    'NOTCHPAY_PUBLIC_KEY': (r'^pk[._][A-Za-z0-9_.\-]{10,}$', 'clé publique Notch Pay (commence par « pk. » ou « pk_test. »)'),
    'NOTCHPAY_HASH_KEY': (r'^[A-Za-z0-9_.\-]{16,}$', 'clé de hachage du tableau de bord Notch Pay (« Webhooks »)'),
    'TWILIO_ACCOUNT_SID': (r'^AC[0-9a-f]{32}$', 'commence par « AC » suivi de 32 caractères'),
    'TWILIO_AUTH_TOKEN': (r'^[0-9a-f]{32}$', '32 caractères (chiffres et lettres a à f)'),
    'TWILIO_MESSAGING_SERVICE_SID': (r'^MG[0-9a-f]{32}$', 'commence par « MG » suivi de 32 caractères'),
    'TWILIO_FROM_NUMBER': (r'^\+[1-9][0-9]{6,14}$', 'numéro international, par exemple +33612345678'),
    'SENDGRID_API_KEY': (r'^SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}$', 'commence par « SG. »'),
    'CLOUDINARY_CLOUD_NAME': (r'^[a-z0-9][a-z0-9_\-]{1,60}$', 'nom du cloud en minuscules (tableau de bord Cloudinary)'),
    'CLOUDINARY_API_KEY': (r'^[0-9]{10,20}$', 'uniquement des chiffres (15 environ)'),
    'CLOUDINARY_API_SECRET': (r'^[A-Za-z0-9_\-]{20,}$', 'environ 27 caractères'),
    'EXPO_ACCESS_TOKEN': (r'^[A-Za-z0-9_\-]{20,}$', 'jeton d’accès Expo (expo.dev › Access tokens)'),
}


def clean(value):
    """Retire ce qu'on colle souvent par erreur : espaces, retours à la ligne, guillemets, « NOM= »."""
    value = str(value or '').strip().strip('"\'').strip()
    if '=' in value and re.match(r'^[A-Z_]{4,40}=', value):
        value = value.split('=', 1)[1].strip().strip('"\'')
    return value


# Formats stables : une erreur ici est forcément une confusion (clé publique Stripe au lieu de la
# secrète, secret du webhook inversé…) → refusée. Les autres services changent parfois le format
# de leurs clés (Google : « AIza… » puis « AQ.… ») → simple avertissement, le test en direct tranche.
STRICT = {'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'TWILIO_ACCOUNT_SID', 'TWILIO_MESSAGING_SERVICE_SID',
          'TWILIO_FROM_NUMBER', 'SENDGRID_API_KEY'}


def _format_issue(name, value):
    rule = FORMATS.get(name)
    if rule and not re.match(rule[0], value):
        return f'la clé attendue {rule[1]}'
    return ''


def check_format(name, value):
    """Erreur BLOQUANTE en français, ou '' si la clé peut être enregistrée."""
    if not value:
        return 'La valeur est vide.'
    if any(c.isspace() for c in value):
        return 'La clé contient un espace ou un retour à la ligne : recopiez-la d’un seul tenant.'
    issue = _format_issue(name, value)
    if issue and name in STRICT:
        return f'Ce n’est pas le bon format : {issue}. Vérifiez que vous avez copié la bonne clé.'
    return ''


def warnings(name, value):
    """Avertissements non bloquants (ex. clé de test)."""
    out = []
    issue = _format_issue(name, value)
    if issue and name not in STRICT:
        out.append(f'Format inhabituel ({issue}) : regardez le résultat du test en direct.')
    if name == 'STRIPE_SECRET_KEY' and '_test_' in value:
        out.append('Clé Stripe de TEST : aucun vrai paiement ne sera encaissé.')
    if name == 'STRIPE_SECRET_KEY' and value.startswith('rk_'):
        out.append('Clé Stripe restreinte : vérifiez qu’elle autorise Checkout, Connect et les remboursements.')
    if name == 'NOTCHPAY_PUBLIC_KEY' and 'test' in value.lower():
        out.append('Clé Notch Pay de TEST : aucun vrai paiement Mobile Money.')
    return out


# ─────────────────────────────────────────────────────────────
# Tests en direct (lecture seule)
# ─────────────────────────────────────────────────────────────
def _http(method, url, **kw):
    try:
        r = requests.request(method, url, timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        return None, f'service injoignable ({exc.__class__.__name__})'
    return r, ''


def _status(r, err, ok_msg='clé acceptée'):
    if r is None:
        return False, err
    if r.status_code in (401, 403):
        return False, 'clé refusée par le service (mauvaise clé, révoquée ou sans les droits)'
    if r.status_code >= 400:
        return False, f'réponse inattendue du service (HTTP {r.status_code})'
    return True, ok_msg


def live_check(name, get):
    """get(nom) → valeur. Renvoie (ok, message) ; (None, message) si rien à tester."""
    v = get(name)
    if not v:
        return None, 'non renseignée'
    if name == 'STRIPE_SECRET_KEY':
        r, err = _http('GET', 'https://api.stripe.com/v1/balance', auth=(v, ''))
        return _status(r, err, 'clé acceptée' + (' (mode TEST)' if '_test_' in v else ' (mode réel)'))
    if name == 'GEMINI_API_KEY':
        r, err = _http('GET', 'https://generativelanguage.googleapis.com/v1beta/models', params={'key': v, 'pageSize': 1})
        return _status(r, err)
    if name == 'GROQ_API_KEY':
        return _status(*_http('GET', 'https://api.groq.com/openai/v1/models', headers={'Authorization': f'Bearer {v}'}))
    if name == 'OPENROUTER_API_KEY':
        return _status(*_http('GET', 'https://openrouter.ai/api/v1/key', headers={'Authorization': f'Bearer {v}'}))
    if name == 'MISTRAL_API_KEY':
        return _status(*_http('GET', 'https://api.mistral.ai/v1/models', headers={'Authorization': f'Bearer {v}'}))
    if name == 'GOOGLE_MAPS_API_KEY':
        r, err = _http('GET', 'https://maps.googleapis.com/maps/api/geocode/json', params={'address': 'Douala', 'key': v})
        if r is None:
            return False, err
        state = (r.json() if r.ok else {}).get('status')
        return (True, 'clé acceptée') if state == 'OK' else (False, f'Google répond « {state or r.status_code} » (API Geocoding activée ?)')
    if name == 'NOTCHPAY_PUBLIC_KEY':
        from django.conf import settings
        return _status(*_http('GET', f'{settings.NOTCHPAY_API}/payments', params={'limit': 1},
                              headers={'Authorization': v, 'Accept': 'application/json'}))
    if name in ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN'):
        sid, token = get('TWILIO_ACCOUNT_SID'), get('TWILIO_AUTH_TOKEN')
        if not (sid and token):
            return None, 'à tester une fois le SID et le jeton renseignés'
        return _status(*_http('GET', f'https://api.twilio.com/2010-04-01/Accounts/{sid}.json', auth=(sid, token)))
    if name == 'SENDGRID_API_KEY':
        r, err = _http('GET', 'https://api.sendgrid.com/v3/scopes', headers={'Authorization': f'Bearer {v}'})
        ok, msg = _status(r, err)
        if ok and 'mail.send' not in (r.json().get('scopes') or []):
            return False, 'clé acceptée mais sans le droit « Mail Send »'
        return ok, msg
    if name.startswith('CLOUDINARY_'):
        cloud, key, secret = get('CLOUDINARY_CLOUD_NAME'), get('CLOUDINARY_API_KEY'), get('CLOUDINARY_API_SECRET')
        if not (cloud and key and secret):
            return None, 'à tester une fois les 3 valeurs Cloudinary renseignées'
        return _status(*_http('GET', f'https://api.cloudinary.com/v1_1/{cloud}/ping', auth=(key, secret)))
    # Secrets de signature (webhooks), numéro, jeton Expo : format seulement
    issue = check_format(name, v) or _format_issue(name, v)
    return (not issue), (issue or 'format correct (vérifié sans appel au service)')
