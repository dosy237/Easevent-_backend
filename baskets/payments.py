"""
baskets/payments.py — payer sa participation au panier

- Carte : Stripe Checkout, « destination charge » vers le compte de l'organisateur,
  SANS commission Easevent (pas d'application_fee_amount).
- Orange Money / MTN MoMo : Notch Pay, référence « bk-… » ; l'état est toujours
  redemandé à Notch Pay (jamais cru sur la seule foi du webhook).
"""
import logging
import secrets
import uuid

import requests
from django.conf import settings

from .models import Contribution
from .services import BasketError, mark_paid

logger = logging.getLogger(__name__)


def _check(c):
    if c.kind != Contribution.Kind.MONEY or c.status not in (Contribution.Status.AWAITING_PAYMENT,
                                                             Contribution.Status.FAILED):
        raise BasketError('Cette participation est déjà réglée ou annulée.', 'not_pending', 409)
    if c.basket.status != 'open':
        raise BasketError('Ce panier est fermé.', 'closed', 409)


def card_checkout(c, request):
    import stripe
    from tickets.stripe_service import _configure, _url, to_minor_units
    _check(c)
    organizer = c.basket.event.organizer
    if not (organizer.stripe_account_id and organizer.stripe_charges_enabled):
        raise BasketError("L'organisateur n'a pas encore activé les paiements par carte. Essayez Mobile Money.",
                          'organizer_not_ready', 409)
    _configure()
    amount = to_minor_units(c.amount, c.currency)
    title = f'Panier « {c.basket.title} » — {c.basket.event.title}'[:250]
    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=[{'quantity': 1, 'price_data': {'currency': c.currency.lower(), 'unit_amount': amount,
                                                   'product_data': {'name': title}}}],
        customer_email=c.user.email, client_reference_id=f'basket:{c.id}',
        metadata={'basket_contribution_id': str(c.id)},
        payment_intent_data={'transfer_data': {'destination': organizer.stripe_account_id},
                             'metadata': {'basket_contribution_id': str(c.id)}, 'description': title},
        success_url=_url(request, '/api/payments/return/?flow=basket&status=success'),
        cancel_url=_url(request, '/api/payments/return/?flow=basket&status=cancel'),
        locale='fr', idempotency_key=f'basket-checkout-{c.id}-{c.updated_at.timestamp()}',
    )
    Contribution.objects.filter(pk=c.pk).update(stripe_checkout_session_id=session['id'],
                                                status=Contribution.Status.AWAITING_PAYMENT)
    return session['url']


def handle_stripe_session(kind, obj):
    """Webhook Stripe d'une session de panier. Idempotent."""
    try:
        cid = uuid.UUID(str((obj.get('metadata') or {}).get('basket_contribution_id')))
    except ValueError:
        return
    c = Contribution.objects.select_related('basket', 'basket__event', 'user').filter(pk=cid).first()
    if c is None:
        logger.warning('Webhook Stripe : participation au panier introuvable (%s)', obj.get('id'))
        return
    paid = (kind == 'checkout.session.completed' and obj.get('payment_status') == 'paid') \
        or kind == 'checkout.session.async_payment_succeeded'
    if paid:
        mark_paid(c, obj.get('payment_intent') or '')
    elif kind == 'checkout.session.completed':
        Contribution.objects.filter(pk=c.pk, status=Contribution.Status.AWAITING_PAYMENT).update(
            status=Contribution.Status.PROCESSING)
    elif kind in ('checkout.session.async_payment_failed', 'checkout.session.expired'):
        Contribution.objects.filter(pk=c.pk, status__in=(Contribution.Status.AWAITING_PAYMENT,
                                                         Contribution.Status.PROCESSING)).update(
            status=Contribution.Status.FAILED)


def mobile_money(c, request, phone=''):
    from tickets import mobile_money as mm
    from tickets.stripe_service import _url
    _check(c)
    if not mm.available():
        raise BasketError('Mobile Money est momentanément indisponible.', 'mobile_money_unavailable', 503)
    amount = mm.amount_xaf(c.amount, c.currency)
    if not amount:
        raise BasketError('Mobile Money : disponible pour les montants en euros ou en francs CFA.', 'currency_unsupported')
    reference = f'bk-{str(c.id)[:8]}-{secrets.token_hex(4)}'
    body = {
        'amount': amount, 'currency': 'XAF', 'reference': reference,
        'description': f'Panier « {c.basket.title} » — {c.basket.event.title}'[:250],
        'callback': _url(request, f'/api/payments/mobile-money/return/?reference={reference}'),
        'customer': {'email': c.user.email, 'name': c.user.full_name},
    }
    phone = ''.join(ch for ch in str(phone or '') if ch.isdigit() or ch == '+')
    if 8 <= len(phone) <= 16:
        body['customer']['phone'] = phone
    try:
        r = requests.post(f'{settings.NOTCHPAY_API}/payments', json=body, headers=mm._headers(), timeout=mm.TIMEOUT)
        r.raise_for_status()
        data = r.json()
        url = data.get('authorization_url') or (data.get('transaction') or {}).get('authorization_url')
        if not url or not str(url).startswith('https://'):
            raise ValueError('authorization_url absente')
    except Exception:
        logger.exception('Notch Pay : création du paiement impossible (panier %s)', c.id)
        raise BasketError('Le paiement Mobile Money est momentanément indisponible. Réessayez.', 'mobile_money_error', 502)
    Contribution.objects.filter(pk=c.pk).update(mobile_money_reference=reference, mobile_money_amount=amount,
                                                status=Contribution.Status.AWAITING_PAYMENT)
    return {'url': url, 'reference': reference, 'amount': amount, 'currency': 'XAF'}


def sync_mobile_money(reference):
    """Référence « bk-… » : état redemandé à Notch Pay, puis appliqué. Idempotent."""
    from tickets import mobile_money as mm
    c = Contribution.objects.select_related('basket', 'basket__event', 'user').filter(
        mobile_money_reference=reference).first()
    if c is None:
        logger.warning('Notch Pay : participation inconnue %s', reference)
        return None
    tx = mm._fetch(reference)
    state = str(tx.get('status') or '').lower()
    if state == 'complete':
        paid = int(float(tx.get('amount') or 0))
        if str(tx.get('currency') or 'XAF').upper() != 'XAF' or paid < (c.mobile_money_amount or 0):
            logger.error('Notch Pay : montant incohérent pour %s', reference)
            return c
        return mark_paid(c)
    if state == 'processing':
        Contribution.objects.filter(pk=c.pk, status=Contribution.Status.AWAITING_PAYMENT).update(
            status=Contribution.Status.PROCESSING)
    elif state in mm.FAILED:
        Contribution.objects.filter(pk=c.pk, status__in=(Contribution.Status.AWAITING_PAYMENT,
                                                         Contribution.Status.PROCESSING)).update(
            status=Contribution.Status.FAILED)
    c.refresh_from_db()
    return c
