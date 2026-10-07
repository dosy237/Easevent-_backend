"""
tickets/stripe_service.py
═══════════════════════════════════════════════════════════════
Paiements Stripe — option A : Stripe Connect.

- Chaque organisateur ouvre un compte Connect « Express » (IBAN,
  identité) depuis l'application. Stripe vire automatiquement l'argent
  de SES tickets sur SON compte bancaire.
- Easevent prélève une commission (PLATFORM_FEE_PERCENT) sur chaque
  ticket : « destination charge » avec application_fee_amount.
- Le paiement se fait sur la page Stripe Checkout (carte, Apple Pay,
  Google Pay, prélèvement SEPA… selon les moyens activés dans le
  tableau de bord Stripe). Aucune donnée bancaire ne transite par
  Easevent (conformité PCI-DSS assurée par Stripe).
- Le ticket passe en « généré » uniquement sur réception du webhook
  signé — jamais sur la seule parole de l'application.

Les clés sont lues dans les variables d'environnement du serveur.
═══════════════════════════════════════════════════════════════
"""
import json
import logging
from events.wording import pass_word
from decimal import Decimal, ROUND_HALF_UP

import stripe
from django.conf import settings

from adminpanel.keys import get_key

from .models import Ticket
from .services import TicketError, generate_ticket

logger = logging.getLogger(__name__)

ZERO_DECIMAL = {'XAF', 'XOF', 'JPY', 'KRW'}


class PaymentsUnavailable(TicketError):
    def __init__(self):
        super().__init__('Les paiements ne sont pas encore configurés sur le serveur.', 'payments_unavailable', 503)


def _configure():
    if not get_key('STRIPE_SECRET_KEY'):
        raise PaymentsUnavailable()
    stripe.api_key = get_key('STRIPE_SECRET_KEY')
    stripe.max_network_retries = 2


def to_minor_units(amount, currency):
    amount = Decimal(amount)
    if currency.upper() in ZERO_DECIMAL:
        return int(amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def platform_fee(amount_minor):
    pct = Decimal(str(settings.PLATFORM_FEE_PERCENT or 0))
    return int((Decimal(amount_minor) * pct / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _url(request, path):
    base = getattr(settings, 'PUBLIC_BASE_URL', '') or ''
    if base.startswith('https://') or request is None:
        return f"{base.rstrip('/')}{path}"
    return request.build_absolute_uri(path)


# ─────────────────────────────────────────────────────────────
# Stripe Connect — compte de l'organisateur
# ─────────────────────────────────────────────────────────────
def sync_account(user, account=None):
    """Met à jour les drapeaux « peut encaisser / virements activés »."""
    if not user.stripe_account_id:
        return user
    if account is None:
        _configure()
        account = _plain(stripe.Account.retrieve(user.stripe_account_id))
    was_enabled = user.stripe_charges_enabled
    user.stripe_charges_enabled = bool(account.get('charges_enabled'))
    user.stripe_payouts_enabled = bool(account.get('payouts_enabled'))
    user.save(update_fields=['stripe_charges_enabled', 'stripe_payouts_enabled', 'updated_at'])
    if user.stripe_charges_enabled and not was_enabled:
        from notifications.models import Notification
        from notifications.services import notify
        notify(user, Notification.Type.PAYOUTS_READY, 'Paiements activés',
               'Vous pouvez maintenant vendre des billets : l’argent est versé sur votre compte bancaire.',
               dedupe_key=f'payouts-ready:{user.stripe_account_id}')
    return user


def onboarding_link(user, request):
    """Crée le compte Express si besoin et renvoie le lien d'inscription Stripe."""
    _configure()
    if not user.stripe_account_id:
        account = stripe.Account.create(
            type='express',
            country=settings.STRIPE_CONNECT_COUNTRY,
            email=user.email,
            capabilities={'card_payments': {'requested': True}, 'transfers': {'requested': True}},
            business_profile={'product_description': 'Billetterie d’événements via Easevent'},
            metadata={'easevent_user_id': str(user.id)},
        )
        user.stripe_account_id = account['id']
        user.save(update_fields=['stripe_account_id', 'updated_at'])
    link = stripe.AccountLink.create(
        account=user.stripe_account_id,
        refresh_url=_url(request, '/api/payments/return/?flow=connect&status=refresh'),
        return_url=_url(request, '/api/payments/return/?flow=connect&status=done'),
        type='account_onboarding',
    )
    return link['url']


def dashboard_link(user):
    """Lien vers le tableau de bord Express (virements, IBAN, historique)."""
    _configure()
    if not user.stripe_account_id:
        raise TicketError("Activez d'abord les paiements.", 'not_connected')
    return stripe.Account.create_login_link(user.stripe_account_id)['url']


# ─────────────────────────────────────────────────────────────
# Paiement d'un ticket
# ─────────────────────────────────────────────────────────────
def create_checkout(ticket, request):
    """Session Stripe Checkout pour un ticket en attente (destination charge)."""
    if ticket.status != Ticket.Status.PENDING:
        raise TicketError("Ce ticket n'est plus en attente de paiement.", 'not_pending')
    from .services import ensure_still_accessible
    ensure_still_accessible(ticket)
    if ticket.is_free:
        raise TicketError('Ce ticket est gratuit : validez-le directement.', 'free_ticket')
    if ticket.payment_status == Ticket.PaymentStatus.PROCESSING:
        raise TicketError('Votre paiement est en cours de confirmation.', 'payment_processing', 409)

    organizer = ticket.event.organizer
    if not (organizer.stripe_account_id and organizer.stripe_charges_enabled):
        raise TicketError(
            "L'organisateur n'a pas encore activé les paiements. Réessayez plus tard.",
            'organizer_not_ready', 409)

    _configure()
    amount = to_minor_units(ticket.price, ticket.currency)
    payment_intent_data = {
        'transfer_data': {'destination': organizer.stripe_account_id},
        'metadata': {'ticket_id': str(ticket.id), 'ticket_number': ticket.number},
        'description': f"{pass_word(ticket.event)['One']} {ticket.number} — {ticket.event.title}"[:255],
    }
    fee = platform_fee(amount)
    if fee:
        payment_intent_data['application_fee_amount'] = fee

    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=[{
            'quantity': 1,
            'price_data': {
                'currency': ticket.currency.lower(),
                'unit_amount': amount,
                'product_data': {'name': f"{pass_word(ticket.event)['One']} — {ticket.event.title}"[:250]},
            },
        }],
        customer_email=ticket.user.email,
        client_reference_id=str(ticket.id),
        metadata={'ticket_id': str(ticket.id)},
        payment_intent_data=payment_intent_data,
        success_url=_url(request, f'/api/payments/return/?flow=ticket&status=success&ticket={ticket.id}'),
        cancel_url=_url(request, f'/api/payments/return/?flow=ticket&status=cancel&ticket={ticket.id}'),
        locale='fr',
        idempotency_key=f"checkout-{ticket.id}-{ticket.updated_at.timestamp()}",
    )
    ticket.stripe_checkout_session_id = session['id']
    ticket.payment_status = Ticket.PaymentStatus.PENDING
    ticket.save(update_fields=['stripe_checkout_session_id', 'payment_status', 'updated_at'])
    return session['url']


def create_gift_checkout(gift, request):
    """Session Stripe Checkout pour un billet offert (même circuit que le billet : versement à l'organisateur)."""
    from .models import TicketGift
    if gift.status != TicketGift.Status.AWAITING_PAYMENT:
        raise TicketError('Ce cadeau est déjà réglé ou annulé.', 'not_pending')
    from .gifts import _check_event
    _check_event(gift.event, gift.buyer)
    organizer = gift.event.organizer
    if not (organizer.stripe_account_id and organizer.stripe_charges_enabled):
        raise TicketError("L'organisateur n'a pas encore activé les paiements. Réessayez plus tard.", 'organizer_not_ready', 409)
    _configure()
    amount = to_minor_units(gift.price, gift.currency)
    word = pass_word(gift.event)
    intent = {'transfer_data': {'destination': organizer.stripe_account_id},
              'metadata': {'gift_id': str(gift.id)},
              'description': f"{word['One']} offert{word['e']} à {gift.recipient_name} — {gift.event.title}"[:255]}
    fee = platform_fee(amount)
    if fee:
        intent['application_fee_amount'] = fee
    session = stripe.checkout.Session.create(
        mode='payment',
        line_items=[{'quantity': 1, 'price_data': {
            'currency': gift.currency.lower(), 'unit_amount': amount,
            'product_data': {'name': f"{word['One']} pour {gift.recipient_name} — {gift.event.title}"[:250]}}}],
        customer_email=gift.buyer.email, client_reference_id=f'gift:{gift.id}', metadata={'gift_id': str(gift.id)},
        payment_intent_data=intent,
        success_url=_url(request, '/api/payments/return/?flow=gift&status=success'),
        cancel_url=_url(request, '/api/payments/return/?flow=gift&status=cancel'),
        locale='fr', idempotency_key=f'gift-checkout-{gift.id}-{gift.updated_at.timestamp()}',
    )
    gift.stripe_checkout_session_id = session['id']
    gift.save(update_fields=['stripe_checkout_session_id', 'updated_at'])
    return session['url']


def _handle_gift_session(kind, obj):
    from .gifts import mark_paid
    from .models import TicketGift
    import uuid
    try:
        gift_id = uuid.UUID(str((obj.get('metadata') or {}).get('gift_id')))
    except ValueError:
        gift_id = None
    gift = TicketGift.objects.select_related('event', 'buyer').filter(pk=gift_id).first() if gift_id else None
    if gift is None:
        logger.warning('Webhook Stripe : cadeau introuvable (%s)', obj.get('id'))
        return
    paid = (kind == 'checkout.session.completed' and obj.get('payment_status') == 'paid') \
        or kind == 'checkout.session.async_payment_succeeded'
    if paid:
        mark_paid(gift, Ticket.PaymentStatus.PAID, obj.get('payment_intent') or '')
    elif kind == 'checkout.session.completed':
        TicketGift.objects.filter(pk=gift.pk).update(payment_status=Ticket.PaymentStatus.PROCESSING)
    elif kind in ('checkout.session.async_payment_failed', 'checkout.session.expired'):
        TicketGift.objects.filter(pk=gift.pk, status=TicketGift.Status.AWAITING_PAYMENT).update(
            payment_status=Ticket.PaymentStatus.FAILED)


# ─────────────────────────────────────────────────────────────
# Webhooks
# ─────────────────────────────────────────────────────────────
def _plain(obj):
    """Objet Stripe → dict Python (les objets Stripe ne sont plus des dict depuis la v15)."""
    return obj.to_dict() if hasattr(obj, 'to_dict') else obj


def parse_webhook(payload, signature):
    """
    Vérifie la signature Stripe (rejette toute requête falsifiée), puis
    renvoie l'événement sous forme de dict.
    """
    if not get_key('STRIPE_WEBHOOK_SECRET'):
        raise PaymentsUnavailable()
    stripe.Webhook.construct_event(payload, signature, get_key('STRIPE_WEBHOOK_SECRET'))
    raw = payload.decode('utf-8') if isinstance(payload, bytes) else payload
    return json.loads(raw)


def _ticket_from_session(session):
    import uuid
    ticket_id = (session.get('metadata') or {}).get('ticket_id') or session.get('client_reference_id')
    try:
        ticket_id = uuid.UUID(str(ticket_id))
    except ValueError:
        return None
    return Ticket.objects.select_related('event').filter(pk=ticket_id).first()


def refund_ticket(ticket, reason='requested_by_customer'):
    """
    Rembourse un ticket payé (annulation de l'événement, invitation retirée).
    Charge de destination : le virement à l'organisateur et la commission
    Easevent sont repris, le participant est remboursé intégralement.
    Retourne True si le remboursement est fait (ou déjà fait).
    """
    from notifications.models import Notification
    from notifications.services import notify
    if ticket.payment_status == Ticket.PaymentStatus.REFUNDED:
        return True
    if ticket.mobile_money_reference and not ticket.stripe_payment_intent_id:
        # Mobile Money : remboursement à faire depuis le tableau de bord Notch Pay (signalé à l'administration)
        logger.error('Remboursement Mobile Money à faire : billet %s, référence %s, %s FCFA',
                     ticket.id, ticket.mobile_money_reference, ticket.mobile_money_amount)
        return False
    if not ticket.stripe_payment_intent_id:
        logger.error('Remboursement impossible : ticket %s sans paiement Stripe', ticket.id)
        return False
    try:
        _configure()
        stripe.Refund.create(
            payment_intent=ticket.stripe_payment_intent_id,
            reverse_transfer=True, refund_application_fee=True,
            metadata={'ticket_id': str(ticket.id), 'reason': reason},
            idempotency_key=f'refund-{ticket.id}',
        )
    except Exception:
        logger.exception('Remboursement Stripe échoué pour le ticket %s', ticket.id)
        return False
    Ticket.objects.filter(pk=ticket.pk).update(payment_status=Ticket.PaymentStatus.REFUNDED)
    notify(ticket.user, Notification.Type.PAYMENT_REFUNDED, 'Remboursement en cours',
           f'{ticket.price} {ticket.currency} pour {ticket.event.title}. Il apparaît sur votre compte sous 5 à 10 jours.',
           event=ticket.event, ticket=ticket, dedupe_key=f'refund:{ticket.id}')
    return True


def _notify_paid(ticket):
    """Paiement reçu : le participant (reçu) et l'organisateur (vente)."""
    from notifications.models import Notification
    from notifications.services import notify
    notify(ticket.user, Notification.Type.PAYMENT_SUCCEEDED, 'Paiement reçu',
           f"{ticket.price} {ticket.currency} pour {ticket.event.title}. {pass_word(ticket.event)['One']} prêt{pass_word(ticket.event)['e']}.",
           event=ticket.event, ticket=ticket, dedupe_key=f'paid:{ticket.id}')


def _notify_payment_failed(ticket):
    from notifications.models import Notification
    from notifications.services import notify
    notify(ticket.user, Notification.Type.PAYMENT_FAILED, 'Paiement non abouti',
           f"pour {ticket.event.title}. {pass_word(ticket.event)['your'].capitalize()} reste en attente : vous pouvez réessayer.",
           event=ticket.event, ticket=ticket, dedupe_key=f'payment-failed:{ticket.id}')


def handle_event(event):
    """Traite un événement Stripe déjà vérifié. Idempotent."""
    kind = event['type']
    obj = event['data']['object']

    if kind.startswith('checkout.session.') and obj.get('mode') == 'subscription':
        from subscriptions.services import handle_stripe_event
        handle_stripe_event(kind, obj)
        return

    if kind.startswith('checkout.session.') and (obj.get('metadata') or {}).get('gift_id'):
        _handle_gift_session(kind, obj)
        return

    if kind in ('checkout.session.completed', 'checkout.session.async_payment_succeeded',
                'checkout.session.async_payment_failed', 'checkout.session.expired'):
        ticket = _ticket_from_session(obj)
        if ticket is None:
            logger.warning('Webhook Stripe sans ticket correspondant : %s', obj.get('id'))
            return
        if obj.get('payment_intent'):
            ticket.stripe_payment_intent_id = obj['payment_intent']
            ticket.save(update_fields=['stripe_payment_intent_id', 'updated_at'])

        paid_now = kind == 'checkout.session.completed' and obj.get('payment_status') == 'paid'
        if paid_now or kind == 'checkout.session.async_payment_succeeded':
            if ticket.status not in (Ticket.Status.PENDING, Ticket.Status.GENERATED):
                # Payé après l'annulation de l'événement / du ticket : on rembourse
                Ticket.objects.filter(pk=ticket.pk).update(payment_status=Ticket.PaymentStatus.PAID)
                ticket.payment_status = Ticket.PaymentStatus.PAID
                refund_ticket(ticket, 'cancelled_before_payment')
                return
            generate_ticket(ticket, Ticket.PaymentStatus.PAID)
            _notify_paid(ticket)
        elif kind == 'checkout.session.completed':
            # Prélèvement SEPA / virement : confirmation dans quelques jours
            Ticket.objects.filter(pk=ticket.pk, status=Ticket.Status.PENDING).update(
                payment_status=Ticket.PaymentStatus.PROCESSING)
        elif kind == 'checkout.session.async_payment_failed':
            if Ticket.objects.filter(pk=ticket.pk, status=Ticket.Status.PENDING).update(
                    payment_status=Ticket.PaymentStatus.FAILED):
                _notify_payment_failed(ticket)
        elif kind == 'checkout.session.expired':
            Ticket.objects.filter(pk=ticket.pk, status=Ticket.Status.PENDING,
                                  payment_status=Ticket.PaymentStatus.PENDING).update(
                payment_status=Ticket.PaymentStatus.FAILED)

    elif kind == 'account.updated':
        from users.models import User
        user = User.objects.filter(stripe_account_id=obj.get('id')).first()
        if user:
            sync_account(user, obj)

    elif kind == 'charge.refunded':
        # Remboursement fait depuis le tableau de bord Stripe par l'organisateur / Easevent
        intent = obj.get('payment_intent')
        if intent:
            for ticket in Ticket.objects.select_related('event', 'user').filter(stripe_payment_intent_id=intent) \
                    .exclude(payment_status=Ticket.PaymentStatus.REFUNDED):
                Ticket.objects.filter(pk=ticket.pk).update(
                    payment_status=Ticket.PaymentStatus.REFUNDED, status=Ticket.Status.CANCELLED)
                from notifications.models import Notification
                from notifications.services import notify
                notify(ticket.user, Notification.Type.PAYMENT_REFUNDED, 'Paiement remboursé',
                       f'pour {ticket.event.title}. Le montant apparaît sur votre compte sous 5 à 10 jours.',
                       event=ticket.event, ticket=ticket, dedupe_key=f'refund:{ticket.id}')

    elif kind.startswith('customer.subscription.') or kind.startswith('invoice.'):
        from subscriptions.services import handle_stripe_event
        handle_stripe_event(kind, obj)
