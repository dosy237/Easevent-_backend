"""
invitations/services.py
═══════════════════════════════════════════════════════════════
Logique des invitations (MVP §3 parcours D et G, écrans M12, M13, M29–M31).

- Envoi par lot : membres (recherche), emails, téléphones (+ CSV).
- Limite d'invités selon le plan de l'organisateur.
- Jeton 256 bits par invitation, stocké haché ; lien /i/<jeton>/.
- Email M30 (HTML + texte) et SMS Twilio.
- Relances : au plus une par invité et par jour, avec un nouveau lien.
═══════════════════════════════════════════════════════════════
"""
import hashlib
import logging
import re
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from easevent.media import absolute_url, public_url

from . import sms
from .crypto import blind_index
from .models import Invitation

logger = logging.getLogger(__name__)

# Invitation « à répondre » (compte dans les relances et « en attente »)
PENDING_STATUSES = ('sent', 'opened')

JOURS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']
MOIS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.']
PHONE_RE = re.compile(r'^\+[1-9]\d{7,14}$')


class InviteError(Exception):
    def __init__(self, message, code, status=400, extra=None):
        super().__init__(message)
        self.message, self.code, self.status, self.extra = message, code, status, extra or {}


# ─────────────────────────────────────────────────────────────
# Jetons
# ─────────────────────────────────────────────────────────────
def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token():
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def find_by_token(raw):
    raw = (raw or '').strip()
    if not raw or len(raw) > 64:
        return None
    return (Invitation.objects
            .select_related('event', 'event__organizer', 'invited_user')
            .filter(token=hash_token(raw), event__deleted_at__isnull=True)
            .first())


def invitation_link(raw, request=None):
    return absolute_url(f'/i/{raw}/', request)


# ─────────────────────────────────────────────────────────────
# Normalisation des contacts
# ─────────────────────────────────────────────────────────────
def normalize_email(value):
    value = (value or '').strip().lower()
    try:
        validate_email(value)
    except ValidationError:
        return None
    return value


def normalize_phone(value):
    """« +33 6 12-34.56 78 » ou « 0033… » → « +33612345678 » (E.164), sinon None."""
    value = re.sub(r'[\s.\-()]', '', str(value or ''))
    if value.startswith('00'):
        value = '+' + value[2:]
    return value if PHONE_RE.match(value) else None


# Indicatifs des pays proposés dans l'application (masquage lisible)
COUNTRY_CODES = {
    '1', '7', '20', '27', '30', '31', '32', '33', '34', '39', '40', '41', '44', '45', '46', '47', '48', '49',
    '90', '212', '213', '216', '221', '223', '224', '225', '226', '227', '228', '229', '230', '235', '236',
    '237', '240', '241', '242', '243', '250', '261', '351', '352', '377', '509', '594', '596', '262',
}


def mask_phone(phone):
    """+33612345678 → « +33 6 •• •• 56 78 » (liste des invités, M13)."""
    if not phone:
        return ''
    digits = phone.lstrip('+')
    cc = next((digits[:n] for n in (3, 2, 1) if digits[:n] in COUNTRY_CODES), digits[:2])
    national = digits[len(cc):]
    if len(national) < 6:
        return f"+{cc} ••••"
    return f"+{cc} {national[0]} •• •• {national[-4:-2]} {national[-2:]}"


def initials(*parts):
    letters = [p.strip()[0] for p in parts if p and p.strip()]
    return ''.join(letters[:2]).upper()


# ─────────────────────────────────────────────────────────────
# Limite du plan
# ─────────────────────────────────────────────────────────────
def guest_limit(user):
    limits = settings.PLAN_GUEST_LIMITS
    return limits.get(user.subscription_plan, limits['free'])


def guests_used(event):
    return event.invitations.exclude(status='revoked').count()


def plan_usage(event):
    return {
        'used':  guests_used(event),
        'limit': guest_limit(event.organizer),
        'plan':  event.organizer.subscription_plan,
    }


# ─────────────────────────────────────────────────────────────
# Formatage (email, SMS)
# ─────────────────────────────────────────────────────────────
def fr_datetime(dt):
    dt = timezone.localtime(dt)
    return f"{JOURS[dt.weekday()]} {dt.day} {MOIS[dt.month - 1]} {dt.year} · {dt:%H}h{dt:%M}"


def price_label(event):
    if not event.is_paid or event.price <= 0:
        return 'Gratuit'
    symbol = '€' if event.currency == 'EUR' else event.currency
    return f"{event.price:.2f}".replace('.', ',') + f" {symbol}"


def _single_line(text):
    return ' '.join(str(text or '').split())


# ─────────────────────────────────────────────────────────────
# Envoi
# ─────────────────────────────────────────────────────────────
def _recipient_email(inv):
    if inv.email:
        return inv.email
    return inv.invited_user.email if inv.invited_user_id else None


def _build_email(inv, raw, request, reminder, connection):
    event, organizer = inv.event, inv.event.organizer
    org_name = _single_line(organizer.full_name)
    title = _single_line(event.title)
    context = {
        'organizer_name': org_name,
        'organizer_first_name': _single_line(organizer.first_name) or org_name,
        'event_title': title,
        'cover_url': public_url(event.cover_image, request) if event.cover_image else '',
        'date': fr_datetime(event.start_date),
        'location': 'En ligne' if event.is_online else (event.location_address or ''),
        'price': price_label(event),
        'dress_code': event.dress_code or '',
        'message': inv.message,
        'url': invitation_link(raw, request),
        'privacy_url': absolute_url('/confidentialite/', request),
        'is_member': bool(inv.invited_user_id),
        'greeting_name': inv.invited_user.first_name if inv.invited_user_id else (inv.contact_name or ''),
        'reminder': reminder,
    }
    prefix = 'Rappel : ' if reminder else ''
    subject = f"{prefix}{context['organizer_first_name']} vous invite à {title}"[:180]
    text = render_to_string('invitations/emails/invitation.txt', context)
    html = render_to_string('invitations/emails/invitation.html', context)
    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [_recipient_email(inv)],
                                 connection=connection)
    msg.attach_alternative(html, 'text/html')
    return msg


def sms_body(inv, raw, request=None):
    event = inv.event
    org = _single_line(inv.event.organizer.first_name) or 'Un organisateur'
    date = timezone.localtime(event.start_date).strftime('%d/%m/%Y')
    parts = [f"{org} vous invite à « {_single_line(event.title)[:60]} » le {date}."]
    if inv.message:
        parts.append(_single_line(inv.message))
    parts.append(invitation_link(raw, request))
    return ' '.join(parts)


def deliver(pairs, request=None, reminder=False):
    """
    pairs : [(invitation, jeton_en_clair)]. Envoie l'email et/ou le SMS
    et enregistre le résultat dans delivery_status. Jamais d'exception :
    l'invitation existe même si l'envoi échoue.
    """
    email_pairs = [(inv, raw) for inv, raw in pairs if _recipient_email(inv)]
    sms_pairs = [(inv, raw) for inv, raw in pairs if inv.phone_number]
    results = {}

    if email_pairs:
        try:
            connection = get_connection()
            connection.open()
        except Exception:
            logger.exception("Connexion au serveur d'email impossible")
            connection = None
        for inv, raw in email_pairs:
            try:
                _build_email(inv, raw, request, reminder, connection).send(fail_silently=False)
                results[inv.pk] = 'sent'
            except Exception:
                logger.exception("Email d'invitation non envoyé (invitation %s)", inv.pk)
                results[inv.pk] = 'failed'
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    if sms_pairs:
        bodies = [(inv, inv.phone, sms_body(inv, raw, request)) for inv, raw in sms_pairs]
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda item: sms.send_sms(item[1], item[2]), bodies))
        for (inv, _, _), state in zip(bodies, statuses):
            results[inv.pk] = state

    for inv, _ in pairs:
        state = results.get(inv.pk, 'in_app')
        if inv.delivery_status != state:
            inv.delivery_status = state
            Invitation.objects.filter(pk=inv.pk).update(delivery_status=state)
    return results


# ─────────────────────────────────────────────────────────────
# Invitation par lot (POST /api/events/:id/invite/)
# ─────────────────────────────────────────────────────────────
def _phone_items(phones):
    for item in phones or []:
        if isinstance(item, dict):
            yield str(item.get('phone') or item.get('phone_number') or ''), str(item.get('name') or '')
        else:
            yield str(item), ''


def invite_batch(event, organizer, *, emails=(), phones=(), user_ids=(), message='', request=None):
    from users.models import User

    message = _single_line(message)
    if len(message) > 100:
        raise InviteError('Le message personnalisé est limité à 100 caractères.', 'message_too_long')

    emails, phones, user_ids = list(emails or []), list(phones or []), list(user_ids or [])
    if len(emails) + len(phones) + len(user_ids) == 0:
        raise InviteError('Ajoutez au moins un invité.', 'empty')
    if len(emails) + len(phones) + len(user_ids) > settings.INVITE_BATCH_MAX:
        raise InviteError(f'{settings.INVITE_BATCH_MAX} invités maximum par envoi.', 'batch_too_large')

    skipped, candidates, seen = [], [], set()

    def skip(value, reason):
        skipped.append({'value': value, 'reason': reason})

    # Membres choisis dans la recherche
    clean_ids = []
    for uid in user_ids:
        try:
            clean_ids.append(str(uuid.UUID(str(uid))))
        except (TypeError, ValueError, AttributeError):
            skip(str(uid)[:40], 'unknown_user')
    members = {str(u.id): u for u in User.objects.filter(
        id__in=clean_ids, is_active=True, is_verified=True, deleted_at__isnull=True)} if clean_ids else {}
    for uid in clean_ids:
        user = members.get(uid)
        if user is None:
            skip(uid, 'unknown_user')
        elif user.id == organizer.id:
            skip(user.full_name, 'self')
        elif ('u', user.id) not in seen:
            seen.add(('u', user.id))
            candidates.append({'kind': 'member', 'user': user, 'email': None, 'phone': None, 'name': ''})

    # Emails : compte existant → invitation de membre
    normalized = []
    for raw_email in emails:
        email = normalize_email(raw_email)
        if email is None:
            skip(str(raw_email)[:254], 'invalid_email')
        else:
            normalized.append(email)
    # Compte vérifié uniquement : sinon l'adresse reçoit l'email M30 classique
    accounts = {u.email.lower(): u for u in User.objects.filter(
        email__in=normalized, is_active=True, is_verified=True, deleted_at__isnull=True)} if normalized else {}
    for email in normalized:
        if email == organizer.email.lower():
            skip(email, 'self')
            continue
        user = accounts.get(email)
        key = ('u', user.id) if user else ('e', email)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({'kind': 'member' if user else 'email', 'user': user, 'email': email,
                           'phone': None, 'name': ''})

    # Téléphones (saisie ou import CSV)
    for raw_phone, name in _phone_items(phones):
        phone = normalize_phone(raw_phone)
        if phone is None:
            skip(raw_phone[:30], 'invalid_phone')
            continue
        if ('p', phone) in seen:
            continue
        seen.add(('p', phone))
        candidates.append({'kind': 'phone', 'user': None, 'email': None, 'phone': phone,
                           'name': _single_line(name)[:80]})

    # Déjà invités (hors révoqués)
    existing = event.invitations.exclude(status='revoked')
    taken_users = set(existing.filter(invited_user__isnull=False).values_list('invited_user_id', flat=True))
    taken_emails = {e.lower() for e in existing.filter(email__isnull=False).values_list('email', flat=True)}
    taken_phones = set(existing.filter(phone_hash__isnull=False).values_list('phone_hash', flat=True))
    fresh = []
    for c in candidates:
        label = c['user'].full_name if c['user'] and not c['email'] else (c['email'] or c['phone'])
        if (c['user'] and c['user'].id in taken_users) or (c['email'] and c['email'] in taken_emails) \
                or (c['phone'] and blind_index(c['phone']) in taken_phones):
            skip(label, 'already_invited')
        else:
            fresh.append(c)

    usage = plan_usage(event)
    if usage['limit'] is not None and usage['used'] + len(fresh) > usage['limit']:
        remaining = max(0, usage['limit'] - usage['used'])
        raise InviteError(
            f"Votre plan permet {usage['limit']} invités par événement. Il vous reste {remaining} place(s).",
            'plan_limit', 403, {'usage': usage, 'remaining': remaining},
        )

    expires_at = (event.end_date or event.start_date) + timedelta(days=7)
    pairs = []
    with transaction.atomic():
        for c in fresh:
            raw, hashed = new_token()
            inv = Invitation(
                event=event,
                invited_user=c['user'],
                email=c['email'],
                contact_name=c['name'],
                message=message,
                token=hashed,
                status='sent',
                channel={'member': 'platform_notification', 'email': 'email', 'phone': 'sms'}[c['kind']],
                expires_at=expires_at,
            )
            inv.set_phone(c['phone'])
            inv.save()
            pairs.append((inv, raw))

    deliver(pairs, request)

    return {
        'created': [{'id': str(inv.id), 'kind': {'platform_notification': 'member', 'email': 'email', 'sms': 'phone'}[inv.channel],
                     'label': display_name(inv), 'delivery_status': inv.delivery_status} for inv, _ in pairs],
        'skipped': skipped,
        'usage':   plan_usage(event),
    }


# ─────────────────────────────────────────────────────────────
# Relances (M13)
# ─────────────────────────────────────────────────────────────
def deliverable(inv):
    """Un canal d'envoi existe : email connu, ou numéro avec les SMS configurés."""
    return bool(_recipient_email(inv)) or (bool(inv.phone_number) and sms.is_configured())


def can_remind(inv, now=None):
    now = now or timezone.now()
    if inv.status not in PENDING_STATUSES or inv.expires_at <= now:
        return False
    if inv.reminded_at and now - inv.reminded_at < timedelta(hours=settings.INVITE_REMIND_DELAY_HOURS):
        return False
    return deliverable(inv)


def remind(invitations, request=None):
    """
    Relance les invitations données avec un nouveau lien.
    Retourne {'reminded': n, 'failed': n} — une invitation sans canal
    d'envoi n'est pas relancée (son lien actuel reste valable).
    """
    now = timezone.now()
    pairs = []
    for inv in invitations:
        if not can_remind(inv, now):
            continue
        raw, hashed = new_token()
        inv.token, inv.reminded_at, inv.remind_count = hashed, now, inv.remind_count + 1
        inv.save(update_fields=['token', 'reminded_at', 'remind_count', 'updated_at'])
        pairs.append((inv, raw))
    results = deliver(pairs, request, reminder=True)
    failed = sum(1 for state in results.values() if state != 'sent')
    return {'reminded': len(pairs) - failed, 'failed': failed}


# ─────────────────────────────────────────────────────────────
# Affichage
# ─────────────────────────────────────────────────────────────
def display_name(inv):
    if inv.invited_user_id:
        return inv.invited_user.full_name
    if inv.contact_name:
        return inv.contact_name
    return inv.email or mask_phone(inv.phone)
