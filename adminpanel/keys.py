"""
adminpanel/keys.py — lecture des clés de service : base de données chiffrée d'abord, environnement ensuite
════════════════════════════════════════════════════════════════
    from adminpanel.keys import get_key
    get_key('GEMINI_API_KEY')
1. Une clé enregistrée dans l'administration (ServiceKey) est déchiffrée et utilisée.
2. Sinon, la variable d'environnement / le réglage Django de même nom.
Les valeurs lues en base sont gardées 60 s en mémoire du processus ; un enregistrement ou une
suppression dans l'administration les invalide aussitôt (signal), y compris pour Celery au
prochain accès (cache partagé Redis). Une clé n'est JAMAIS renvoyée par l'API ni affichée.
Chiffrement : KEYS_ENCRYPTION_KEY si elle est définie, sinon dérivée de SECRET_KEY.
Changer l'une ou l'autre rend les clés enregistrées illisibles : il faut alors les ressaisir.
════════════════════════════════════════════════════════════════
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)
TTL = 60
_ABSENT = '__absent__'


def _fernet():
    material = (getattr(settings, 'KEYS_ENCRYPTION_KEY', '') or settings.SECRET_KEY).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'easevent.service-keys|' + material).digest()))


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        logger.error('Clé de service illisible (clé de chiffrement changée ?) : ressaisissez-la dans l’administration.')
        return ''


def _cache_key(name):
    return f'servicekey:{name}'


def forget(name):
    cache.delete(_cache_key(name))


def stored_key(name):
    """Valeur saisie dans l'administration uniquement ('' sinon)."""
    token = cache.get(_cache_key(name))
    if token is None:
        try:
            from .models import ServiceKey
            row = ServiceKey.objects.filter(name=name).values_list('encrypted_value', flat=True).first()
        except Exception:                      # base indisponible (migration, démarrage) : environnement
            row = None
        token = row or _ABSENT
        cache.set(_cache_key(name), token, TTL)
    return decrypt(token) if token != _ABSENT else ''


# Noms souvent utilisés dans un .env pour la même clé : acceptés en plus du nom officiel
ALIASES = {
    'STRIPE_SECRET_KEY': ('STRIPE_API_KEY', 'STRIPE_SECRET', 'STRIPE_KEY', 'STRIPE_PRIVATE_KEY', 'STRIPE_SK',
                          'STRIPE_LIVE_SECRET_KEY', 'STRIPE_TEST_SECRET_KEY'),
    'STRIPE_WEBHOOK_SECRET': ('STRIPE_WEBHOOK_KEY', 'STRIPE_ENDPOINT_SECRET', 'STRIPE_WHSEC'),
    'TWILIO_ACCOUNT_SID': ('TWILIO_SID',),
    'TWILIO_AUTH_TOKEN': ('TWILIO_TOKEN',),
    'TWILIO_FROM_NUMBER': ('TWILIO_PHONE_NUMBER', 'TWILIO_NUMBER', 'TWILIO_FROM', 'TWILIO_SENDER'),
    'NOTCHPAY_PUBLIC_KEY': ('NOTCHPAY_API_KEY', 'NOTCHPAY_KEY', 'NOTCH_PAY_PUBLIC_KEY', 'NOTCH_PUBLIC_KEY'),
    'NOTCHPAY_HASH_KEY': ('NOTCHPAY_WEBHOOK_SECRET', 'NOTCHPAY_HASH', 'NOTCH_PAY_HASH_KEY'),
    'EMAIL_HOST_PASSWORD': ('EMAIL_PASSWORD', 'GMAIL_APP_PASSWORD', 'GMAIL_PASSWORD', 'SMTP_PASSWORD'),
}


def _clean(value):
    # Espaces ou guillemets collés par erreur dans le .env (« SENDGRID_API_KEY= SG.… ») : retirés
    return str(value or '').strip().strip('"\'').strip()


def env_value(name):
    """Valeur de l'environnement / du .env : nom officiel, sinon un alias. Retourne (valeur, nom lu)."""
    from decouple import config
    value = _clean(getattr(settings, name, ''))
    if value:
        return value, name
    for alias in ALIASES.get(name, ()):
        value = _clean(config(alias, default=''))
        if value:
            return value, alias
    return '', ''


def usable(name, value):
    """Clé publique Stripe (pk_) collée à la place de la secrète : inutilisable."""
    return not (name == 'STRIPE_SECRET_KEY' and value.startswith(('pk_', 'pk.')))


def get_key(name):
    value = _clean(stored_key(name)) or env_value(name)[0]
    return value if usable(name, value) else ''


def apply_cloudinary():
    """Avant chaque usage de Cloudinary : identifiants de l'administration, sinon de l'environnement."""
    import cloudinary
    values = {k: stored_key(f'CLOUDINARY_{k.upper()}') for k in ('cloud_name', 'api_key', 'api_secret')}
    values = {k: v for k, v in values.items() if v}
    if values:                                   # sinon : configuration de l'environnement, chargée au démarrage
        cloudinary.config(secure=True, **values)
    return cloudinary.config()
