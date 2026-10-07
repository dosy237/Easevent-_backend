"""
tickets/mobile_money.py — payer un billet par Orange Money ou MTN MoMo (Cameroun), via Notch Pay
════════════════════════════════════════════════════════════════
1. L'application demande un paiement : le serveur crée la transaction chez Notch Pay
   (montant en FCFA ; un prix en euros est converti à la parité FIXE 1 € = 655,957 FCFA,
   arrondi au multiple de 5 supérieur) et renvoie la page de paiement Notch Pay, où le
   participant choisit Orange Money ou MTN MoMo et valide sur son téléphone.
2. Notch Pay prévient le serveur (webhook signé HMAC-SHA256 avec la clé de hachage).
   Le contenu du webhook n'est jamais cru tel quel : le serveur redemande l'état du
   paiement à Notch Pay, vérifie montant et devise, puis génère le billet.
3. L'argent arrive sur le compte Notch Pay d'Easevent, qui reverse sa part à
   l'organisateur (commission Easevent déduite). Un remboursement Mobile Money se fait
   depuis le tableau de bord Notch Pay : il est signalé à l'administration.
════════════════════════════════════════════════════════════════
"""
import hashlib
import hmac
import json
import logging
import math
import secrets

import requests
from django.conf import settings

from events.wording import pass_word

from .models import Ticket
from .services import TicketError, generate_ticket

logger = logging.getLogger(__name__)
CFA_PER_EUR = 655.957
TIMEOUT = 15
FAILED = {'failed', 'canceled', 'cancelled', 'expired', 'rejected'}


class MobileMoneyUnavailable(TicketError):
    def __init__(self):
        super().__init__("Le paiement Mobile Money n'est pas encore disponible.", 'mobile_money_unavailable', 503)


def available():
    return bool(settings.NOTCHPAY_PUBLIC_KEY)


def amount_xaf(price, currency):
    """Montant en FCFA (entier, multiple de 5). None si la devise ne se convertit pas à taux fixe."""
    price = float(price)
    if currency in ('XAF', 'XOF'):
        value = price
    elif currency == 'EUR':
        value = price * CFA_PER_EUR
    else:
        return None
    return int(math.ceil(value / 5.0) * 5)


def _headers():
    return {'Authorization': settings.NOTCHPAY_PUBLIC_KEY, 'Accept': 'application/json', 'Content-Type': 'application/json'}


def create_payment(ticket, request, phone=''):
    if not available():
        raise MobileMoneyUnavailable()
    if ticket.status != Ticket.Status.PENDING:
        raise TicketError("Ce billet n'est plus en attente de paiement.", 'not_pending')
    from .services import ensure_still_accessible
    ensure_still_accessible(ticket)
    if ticket.is_free:
        raise TicketError('Ce billet est gratuit : validez-le directement.', 'free_ticket')
    if ticket.payment_status == Ticket.PaymentStatus.PROCESSING:
        raise TicketError('Votre paiement est en cours de confirmation.', 'payment_processing', 409)
    amount = amount_xaf(ticket.price, ticket.currency)
    if not amount:
        raise TicketError('Mobile Money : disponible pour les prix en euros ou en francs CFA.', 'currency_unsupported')

    from .stripe_service import _url
    reference = f'ev-{ticket.number}-{secrets.token_hex(4)}'
    word = pass_word(ticket.event)
    body = {
        'amount': amount, 'currency': 'XAF', 'reference': reference,
        'description': f"{word['One']} {ticket.number} — {ticket.event.title}"[:250],
        'callback': _url(request, f'/api/payments/mobile-money/return/?reference={reference}'),
        'customer': {'email': ticket.user.email, 'name': f'{ticket.user.first_name} {ticket.user.last_name}'.strip()},
    }
    phone = ''.join(ch for ch in str(phone or '') if ch.isdigit() or ch == '+')
    if 8 <= len(phone) <= 16:
        body['customer']['phone'] = phone
    try:
        r = requests.post(f'{settings.NOTCHPAY_API}/payments', json=body, headers=_headers(), timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        url = data.get('authorization_url') or (data.get('transaction') or {}).get('authorization_url')
        if not url or not str(url).startswith('https://'):
            raise ValueError('authorization_url absente')
    except Exception:
        logger.exception('Notch Pay : création du paiement impossible (billet %s)', ticket.id)
        raise TicketError('Le paiement Mobile Money est momentanément indisponible. Réessayez.', 'mobile_money_error', 502)
    ticket.mobile_money_reference = reference
    ticket.mobile_money_amount = amount
    ticket.payment_status = Ticket.PaymentStatus.PENDING
    ticket.save(update_fields=['mobile_money_reference', 'mobile_money_amount', 'payment_status', 'updated_at'])
    return {'url': url, 'reference': reference, 'amount': amount, 'currency': 'XAF'}


def verify_signature(raw, signature):
    if not settings.NOTCHPAY_HASH_KEY or not signature:
        return False
    expected = hmac.new(settings.NOTCHPAY_HASH_KEY.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def reference_from_webhook(payload):
    data = payload.get('data') or payload.get('transaction') or {}
    return str(data.get('reference') or payload.get('reference') or '')[:64]


def sync(reference):
    """Demande l'état du paiement à Notch Pay et l'applique au billet. Idempotent."""
    from .stripe_service import _notify_paid, _notify_payment_failed
    if not reference:
        return None
    ticket = Ticket.objects.select_related('event', 'user').filter(mobile_money_reference=reference).first()
    if ticket is None:
        logger.warning('Notch Pay : référence inconnue %s', reference)
        return None
    try:
        r = requests.get(f'{settings.NOTCHPAY_API}/payments/{reference}', headers=_headers(), timeout=TIMEOUT)
        r.raise_for_status()
        tx = r.json().get('transaction') or {}
    except Exception:
        logger.exception('Notch Pay : vérification impossible (%s)', reference)
        raise
    state = str(tx.get('status') or '').lower()
    if state == 'complete':
        paid = int(float(tx.get('amount') or 0))
        if str(tx.get('currency') or 'XAF').upper() != 'XAF' or paid < (ticket.mobile_money_amount or 0):
            logger.error('Notch Pay : montant incohérent pour %s (%s %s)', reference, tx.get('amount'), tx.get('currency'))
            return ticket
        if ticket.status == Ticket.Status.GENERATED and ticket.payment_status == Ticket.PaymentStatus.PAID:
            return ticket
        if ticket.status != Ticket.Status.PENDING:
            # Payé après l'annulation : à rembourser depuis Notch Pay
            Ticket.objects.filter(pk=ticket.pk).update(payment_status=Ticket.PaymentStatus.PAID)
            logger.error('Notch Pay : billet %s payé après annulation — remboursement manuel requis', ticket.id)
            return ticket
        generate_ticket(ticket, Ticket.PaymentStatus.PAID)
        _notify_paid(ticket)
    elif state == 'processing':
        Ticket.objects.filter(pk=ticket.pk, status=Ticket.Status.PENDING).update(payment_status=Ticket.PaymentStatus.PROCESSING)
    elif state in FAILED:
        if Ticket.objects.filter(pk=ticket.pk, status=Ticket.Status.PENDING).exclude(
                payment_status=Ticket.PaymentStatus.FAILED).update(payment_status=Ticket.PaymentStatus.FAILED):
            _notify_payment_failed(ticket)
    ticket.refresh_from_db()
    return ticket


def parse(raw):
    return json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
