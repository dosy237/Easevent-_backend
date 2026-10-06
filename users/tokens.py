"""
users/tokens.py
═══════════════════════════════════════════════════════════════
Jetons à usage unique envoyés par email (vérification d'adresse).

Le jeton en clair part uniquement dans l'email ; la base ne garde
que son empreinte SHA-256. Une fuite de la table ne permet donc pas
de valider un compte à la place de son propriétaire (OWASP A02).
═══════════════════════════════════════════════════════════════
"""

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

VERIFICATION_TTL = timedelta(hours=24)


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def create_email_verification(user):
    """Remplace le jeton éventuel de l'utilisateur et retourne le nouveau jeton en clair."""
    from .models import EmailVerification

    raw_token = secrets.token_urlsafe(32)
    EmailVerification.objects.filter(user=user).delete()
    EmailVerification.objects.create(
        user       = user,
        token      = hash_token(raw_token),
        expires_at = timezone.now() + VERIFICATION_TTL,
    )
    return raw_token


def find_email_verification(raw_token):
    """Retrouve la vérification correspondant au jeton (ou None)."""
    from .models import EmailVerification

    if not raw_token or len(raw_token) > 128:
        return None
    verification = (
        EmailVerification.objects.select_related('user')
        .filter(token=hash_token(raw_token))
        .first()
    )
    if verification is None and len(raw_token) <= 64:
        # Liens envoyés avant le hachage des jetons (stockés en clair)
        verification = (
            EmailVerification.objects.select_related('user')
            .filter(token=raw_token)
            .first()
        )
    return verification
