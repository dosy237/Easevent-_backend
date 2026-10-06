"""
invitations/crypto.py
═══════════════════════════════════════════════════════════════
Chiffrement des numéros de téléphone des invités (RGPD, art. 32).

- Le numéro est stocké chiffré (Fernet : AES-128-CBC + HMAC-SHA256).
- Une empreinte HMAC (« index aveugle ») permet de détecter un numéro
  déjà invité sans jamais le déchiffrer en base.

Clé : PHONE_ENCRYPTION_KEY, sinon dérivée de SECRET_KEY.
IMPORTANT : changer cette clé rend les numéros existants illisibles
(à conserver lors d'un changement d'hébergement).
═══════════════════════════════════════════════════════════════
"""
import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _material():
    return (getattr(settings, 'PHONE_ENCRYPTION_KEY', '') or settings.SECRET_KEY).encode()


def _fernet():
    key = hashlib.sha256(b'easevent.phone.enc|' + _material()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode() if value else None


def decrypt(value):
    if not value:
        return ''
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ''


def blind_index(value):
    key = hashlib.sha256(b'easevent.phone.idx|' + _material()).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
