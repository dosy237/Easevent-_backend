"""
users/phone.py
═══════════════════════════════════════════════════════════════
Numéro de téléphone du compte : vérification par code SMS, puis
rattachement automatique des invitations reçues sur ce numéro.

C'est ce qui permet à une personne invitée par SMS, qui installe
l'application depuis le store (le lien d'origine est alors perdu),
de retrouver son invitation dès qu'elle a créé son compte.
Sans vérification, n'importe qui pourrait saisir le numéro d'un
autre et s'emparer de ses invitations : le code est obligatoire.
═══════════════════════════════════════════════════════════════
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5


class PhoneError(Exception):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def _code_hash(user, code):
    return hashlib.sha256(f'{user.pk}:{code}'.encode()).hexdigest()


def masked(user):
    from invitations.crypto import decrypt
    from invitations.services import mask_phone
    return mask_phone(decrypt(user.phone_number)) if user.phone_number else ''


def set_pending(user, e164):
    """Numéro saisi à l'inscription : gardé (chiffré) en attente de vérification."""
    from invitations.crypto import encrypt
    user.phone_number, user.phone_hash, user.phone_verified_at = encrypt(e164), None, None


def send_code(user, raw_phone):
    from invitations import sms
    from invitations.crypto import encrypt
    from invitations.services import normalize_phone
    from .models import PhoneVerification

    e164 = normalize_phone(raw_phone)
    if not e164:
        raise PhoneError('Numéro invalide : indiquez-le avec son indicatif (ex. +33 6 12 34 56 78).', 'invalid_phone')
    if not sms.is_configured():
        raise PhoneError("L'envoi de SMS n'est pas encore activé. Réessayez plus tard.", 'sms_unavailable', 503)
    code = f'{secrets.randbelow(10 ** 6):06d}'
    PhoneVerification.objects.update_or_create(user=user, defaults={
        'phone_number': encrypt(e164), 'code_hash': _code_hash(user, code), 'attempts': 0,
        'expires_at': timezone.now() + CODE_TTL,
    })
    state = sms.send_sms(e164, f'Easevent : votre code de vérification est {code}. Il expire dans 10 minutes. '
                               'Ne le communiquez à personne.')
    if state != 'sent':
        raise PhoneError("Le SMS n'a pas pu être envoyé. Vérifiez le numéro et réessayez.", 'sms_failed', 502)


def verify_code(user, code):
    """Vérifie le code ; enregistre le numéro et rattache les invitations. Retourne leur nombre."""
    from invitations.crypto import blind_index, decrypt
    from .models import PhoneVerification, User

    code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    wrong = False
    with transaction.atomic():
        pv = PhoneVerification.objects.select_for_update().filter(user=user).first()
        if pv is None or pv.expires_at < timezone.now():
            raise PhoneError('Ce code a expiré. Demandez un nouveau code.', 'expired')
        if pv.attempts >= MAX_ATTEMPTS:
            raise PhoneError('Trop d’essais. Demandez un nouveau code.', 'too_many_attempts', 429)
        if not hmac.compare_digest(pv.code_hash, _code_hash(user, code)):
            # Compté AVANT de lever l'erreur : sinon l'annulation de la
            # transaction effacerait l'essai et la limite ne jouerait jamais.
            pv.attempts += 1
            pv.save(update_fields=['attempts'])
            wrong = True
    if wrong:
        raise PhoneError('Code incorrect.', 'wrong_code')
    with transaction.atomic():
        pv = PhoneVerification.objects.select_for_update().filter(user=user).first()
        if pv is None:      # code déjà utilisé entre-temps (double appui)
            raise PhoneError('Ce code a expiré. Demandez un nouveau code.', 'expired')
        e164 = decrypt(pv.phone_number)
        phone_hash = blind_index(e164)
        if User.objects.filter(phone_hash=phone_hash, phone_verified_at__isnull=False).exclude(pk=user.pk).exists():
            pv.delete()
            raise PhoneError('Ce numéro est déjà associé à un autre compte Easevent.', 'phone_taken', 409)
        user.phone_number, user.phone_hash, user.phone_verified_at = pv.phone_number, phone_hash, timezone.now()
        user.save(update_fields=['phone_number', 'phone_hash', 'phone_verified_at', 'updated_at'])
        pv.delete()
    return claim_invitations(user, phone_hash=phone_hash)


def claim_invitations(user, phone_hash=None, email=None):
    """
    Rattache au compte les invitations envoyées à son numéro vérifié ou à son
    email vérifié, encore valables et sans compte. Retourne leur nombre.
    """
    from django.db.models import Q
    from invitations.models import Invitation
    from notifications.models import Notification
    from notifications.services import notify

    match = Q()
    if phone_hash:
        match |= Q(phone_hash=phone_hash)
    if email:
        match |= Q(email__iexact=email)
    if not match:
        return 0
    invitations = list(Invitation.objects.select_related('event', 'event__organizer')
                       .filter(match, invited_user__isnull=True, expires_at__gt=timezone.now(),
                               event__deleted_at__isnull=True)
                       .exclude(status__in=('revoked', 'expired')).exclude(event__organizer=user))
    # Billets offerts par un proche qui attendaient cette personne
    from tickets.gifts import claim_gifts
    try:
        claim_gifts(user, phone_hash=phone_hash, email=email)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Billets offerts non rattachés pour %s', user.id)
    claimed = 0
    for inv in invitations:
        # Déjà invité autrement à ce même événement : on garde une seule invitation
        if Invitation.objects.filter(event=inv.event, invited_user=user).exclude(status='revoked').exists():
            continue
        inv.invited_user = user
        inv.save(update_fields=['invited_user', 'updated_at'])
        claimed += 1
        if inv.status in ('sent', 'opened'):
            notify(user, Notification.Type.INVITATION_RECEIVED, inv.event.organizer.full_name,
                   f'vous invite à {inv.event.title}', actor=inv.event.organizer, event=inv.event,
                   invitation=inv, dedupe_key=f'invitation:{inv.id}')
    return claimed
