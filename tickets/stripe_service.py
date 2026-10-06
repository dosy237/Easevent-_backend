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
import logging
from decimal import Decimal, ROUND_HALF_UP

import stripe
from django.conf import settings

from .models import Ticket
from .services import TicketError, generate_ticket

logger = logging.getLogger(__name__)

ZERO_DECIMAL = {'XAF', 'XOF', 'JPY', 'KRW'}


class PaymentsUnavailable(TicketError):
    def __init__(self):
        super().__init__('Les paiements ne sont pas encore configurés sur le serveur.', 'payments_unavailable', 503)


def _configure():
    if not settings.STRIPE_SECRET_KEY:
        raise PaymentsUnavailable()
    stripe.api_key = settings.STRIPE_SECRET_KEY
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
        account = stripe.Account.retrieve(user.stripe_account_id)
    user.stripe_charges_enabled = bool(account.get('charges_enabled'))
    user.stripe_payouts_enabled = bool(account.get('payouts_enabled'))
    user.save(update_fields=['stripe_charges_enabled', 'stripe_payouts_enabled', 'updated_at'])
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
        'description': f"Ticket {ticket.number} — {ticket.event.title}"[:255],
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
                'product_data': {'name': f"Ticket — {ticket.event.title}"[:250]},
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


# ─────────────────────────────────────────────────────────────
# Webhooks
# ─────────────────────────────────────────────────────────────
def parse_webhook(payload, signature):
    """Vérifie la signature Stripe (rejette toute requête falsifiée)."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise PaymentsUnavailable()
    return stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)


def _ticket_from_session(session):
    ticket_id = (session.get('metadata') or {}).get('ticket_id') or session.get('client_reference_id')
    if not ticket_id:
        return None
    return Ticket.objects.select_related('event').filter(pk=ticket_id).first()


def handle_event(event):
    """Traite un événement Stripe déjà vérifié. Idempotent."""
    kind = event['type']
    obj = event['data']['object']

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
            generate_ticket(ticket, Ticket.PaymentStatus.PAID)
        elif kind == 'checkout.session.completed':
            # Prélèvement SEPA / virement : confirmation dans quelques jours
            Ticket.objects.filter(pk=ticket.pk, status=Ticket.Status.PENDING).update(
                payment_status=Ticket.PaymentStatus.PROCESSING)
        elif kind == 'checkout.session.async_payment_failed':
            Ticket.objects.filter(pk=ticket.pk, status=Ticket.Status.PENDING).update(
                payment_status=Ticket.PaymentStatus.FAILED)
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
        intent = obj.get('payment_intent')
        if intent:
            Ticket.objects.filter(stripe_payment_intent_id=intent).update(
                payment_status=Ticket.PaymentStatus.REFUNDED, status=Ticket.Status.CANCELLED)
