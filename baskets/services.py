"""
baskets/services.py — règles du panier

- Lancé par l'organisateur ou un co-organisateur, depuis le message à tous les
  invités (un seul panier ouvert par événement).
- Ouvert aux invités (invitation en cours ou acceptée, billet) et à l'équipe.
- Objet : ajouté tout de suite. Argent : payé par carte (Stripe, versé sur le
  compte de l'organisateur, sans commission) ou Orange Money / MTN MoMo
  (Notch Pay, références « bk-… », reversé par Easevent).
- Bilan visible de tous les participants ; un montant peut être masqué aux
  autres invités (l'organisateur le voit toujours).
- Événement annulé : les sommes payées sont remboursées.
"""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Basket, Contribution

logger = logging.getLogger(__name__)

TITLE_MAX = 80
LABEL_MAX = 120
MESSAGE_MAX = 200
QTY_MAX = 999
ZERO_DECIMAL = {'XAF', 'XOF', 'JPY', 'KRW'}
MIN_AMOUNT = {'XAF': Decimal(100), 'XOF': Decimal(100)}
MAX_AMOUNT = {'XAF': Decimal(5_000_000), 'XOF': Decimal(5_000_000)}
DEFAULT_MIN, DEFAULT_MAX = Decimal(1), Decimal(10_000)
PAID_STATES = (Contribution.Status.PAID,)


class BasketError(Exception):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def _text(value, limit, field=''):
    value = ' '.join(str(value or '').split())
    if any(ch in value for ch in '<>{}'):
        raise BasketError('Caractères non autorisés.', 'invalid')
    if len(value) > limit:
        raise BasketError(f'{limit} caractères au maximum.', 'too_long')
    return value


def _amount(value, currency):
    try:
        amount = Decimal(str(value).replace(',', '.').strip())
    except (InvalidOperation, AttributeError):
        raise BasketError('Indiquez un montant.', 'invalid_amount')
    if not amount.is_finite():
        raise BasketError('Indiquez un montant.', 'invalid_amount')
    step = Decimal('1') if currency in ZERO_DECIMAL else Decimal('0.01')
    amount = amount.quantize(step, rounding=ROUND_HALF_UP)
    low, high = MIN_AMOUNT.get(currency, DEFAULT_MIN), MAX_AMOUNT.get(currency, DEFAULT_MAX)
    if amount < low or amount > high:
        raise BasketError(f'Montant entre {low} et {high} {currency}.', 'invalid_amount')
    return amount


# ─────────────────────────────────────────────────────────────
# Droits
# ─────────────────────────────────────────────────────────────
def can_take_part(event, user):
    from events.memories import is_guest
    from events.team import role_of
    if event.deleted_at is not None:
        return False
    return bool(role_of(event, user)) or is_guest(event, user)


def open_basket(event):
    return Basket.objects.filter(event=event, status=Basket.Status.OPEN).first()


# ─────────────────────────────────────────────────────────────
# Lancer / fermer
# ─────────────────────────────────────────────────────────────
def launch(event, by, data):
    from events.team import is_manager
    from messaging.broadcast import send_broadcast
    from messaging.services import MessagingError

    if not is_manager(event, by):
        raise BasketError("Seuls les organisateurs lancent un panier.", 'forbidden', 403)
    if event.end_date and event.end_date < timezone.now() - timedelta(days=30):
        raise BasketError("Cet événement est terminé depuis plus d'un mois.", 'event_over')
    title = _text(data.get('title'), TITLE_MAX) or 'Le panier'
    description = _text(data.get('description'), 300)
    allow_items = data.get('allow_items', True) is not False
    allow_money = data.get('allow_money', True) is not False
    if not (allow_items or allow_money):
        raise BasketError('Autorisez au moins les objets ou l’argent.', 'nothing_allowed')
    currency = (event.currency or 'EUR').upper()
    goal = data.get('goal_amount')
    goal = _amount(goal, currency) if goal not in (None, '') and allow_money else None
    try:
        with transaction.atomic():
            basket = Basket.objects.create(event=event, created_by=by, title=title, description=description,
                                           goal_amount=goal, currency=currency,
                                           allow_items=allow_items, allow_money=allow_money)
    except IntegrityError:
        raise BasketError('Un panier est déjà ouvert pour cet événement.', 'already_open', 409)

    # Annonce dans la conversation de chaque invité (carte « Ouvrir le panier »)
    kinds = ' ou '.join([w for w, ok in (('un objet', allow_items), ('une participation', allow_money)) if ok])
    body = (f'🧺 {title} : chacun peut y ajouter {kinds}. ' + (description or '')).strip()
    try:
        result = send_broadcast(event, by, body[:1000], meta={'basket': str(basket.id)})
    except MessagingError as exc:
        if exc.code in ('no_audience',):
            result = {'recipients': 0, 'without_account': 0}
        else:
            raise BasketError(exc.message, exc.code, exc.status)
    return basket, result


def close(basket, by):
    from events.team import is_manager
    if not is_manager(basket.event, by):
        raise BasketError("Seuls les organisateurs ferment le panier.", 'forbidden', 403)
    if basket.status == Basket.Status.CLOSED:
        return basket
    basket.status, basket.closed_at = Basket.Status.CLOSED, timezone.now()
    basket.save(update_fields=['status', 'closed_at'])
    # Paiements jamais commencés : annulés
    basket.contributions.filter(status=Contribution.Status.AWAITING_PAYMENT, stripe_checkout_session_id='',
                                mobile_money_reference='').update(status=Contribution.Status.CANCELLED)
    return basket


# ─────────────────────────────────────────────────────────────
# Contributions
# ─────────────────────────────────────────────────────────────
def _check_open(basket, user):
    if basket.status != Basket.Status.OPEN:
        raise BasketError('Ce panier est fermé.', 'closed', 409)
    if not can_take_part(basket.event, user):
        raise BasketError("Ce panier est réservé aux invités de l'événement.", 'forbidden', 403)


def add_item(basket, user, data):
    _check_open(basket, user)
    if not basket.allow_items:
        raise BasketError("Ce panier n'accepte que des participations en argent.", 'items_disabled')
    label = _text(data.get('label'), LABEL_MAX)
    if not label:
        raise BasketError('Dites ce que vous apportez.', 'empty')
    try:
        raw = data.get('quantity')
        qty = 1 if raw in (None, '') else int(raw)
    except (TypeError, ValueError):
        raise BasketError('Quantité invalide.', 'invalid_quantity')
    if not 1 <= qty <= QTY_MAX:
        raise BasketError('Quantité invalide.', 'invalid_quantity')
    c = Contribution.objects.create(basket=basket, user=user, kind=Contribution.Kind.ITEM, label=label, quantity=qty,
                                    message=_text(data.get('message'), MESSAGE_MAX), currency=basket.currency,
                                    status=Contribution.Status.CONFIRMED)
    _notify_organizer(basket, user, f'{qty} × {label}' if qty > 1 else label)
    return c


def add_money(basket, user, data):
    _check_open(basket, user)
    if not basket.allow_money:
        raise BasketError("Ce panier n'accepte que des objets.", 'money_disabled')
    amount = _amount(data.get('amount'), basket.currency)
    return Contribution.objects.create(
        basket=basket, user=user, kind=Contribution.Kind.MONEY, amount=amount, currency=basket.currency,
        message=_text(data.get('message'), MESSAGE_MAX), anonymous=bool(data.get('anonymous')),
        status=Contribution.Status.AWAITING_PAYMENT)


def remove(contribution, user):
    from events.team import is_manager
    mine = contribution.user_id == user.id
    if not (mine or is_manager(contribution.basket.event, user)):
        raise BasketError('Contribution introuvable.', 'not_found', 404)
    if contribution.kind == Contribution.Kind.MONEY and contribution.status not in (
            Contribution.Status.AWAITING_PAYMENT, Contribution.Status.FAILED):
        raise BasketError('Une participation payée ne peut pas être retirée.', 'paid')
    if contribution.kind == Contribution.Kind.ITEM:
        contribution.delete()
    else:
        Contribution.objects.filter(pk=contribution.pk).update(status=Contribution.Status.CANCELLED)


def mark_paid(contribution, payment_intent=''):
    """Paiement confirmé (Stripe ou Notch Pay). Idempotent."""
    with transaction.atomic():
        c = Contribution.objects.select_for_update().select_related('basket', 'basket__event', 'user').get(pk=contribution.pk)
        if c.status == Contribution.Status.PAID:
            return c
        if c.status not in (Contribution.Status.AWAITING_PAYMENT, Contribution.Status.PROCESSING, Contribution.Status.FAILED,
                            Contribution.Status.CANCELLED):
            return c
        if payment_intent:
            c.stripe_payment_intent_id = payment_intent
        c.status, c.paid_at = Contribution.Status.PAID, timezone.now()
        c.save(update_fields=['status', 'paid_at', 'stripe_payment_intent_id', 'updated_at'])
    event = c.basket.event
    if event.deleted_at is not None:
        refund(c, 'event_cancelled')
        return c
    from notifications.models import Notification
    from notifications.services import notify
    notify(c.user, Notification.Type.PAYMENT_SUCCEEDED, 'Merci pour votre participation',
           f'{format_amount(c.amount, c.currency)} ajoutés au panier « {c.basket.title} ».',
           event=event, data={'basket_id': str(c.basket_id)}, dedupe_key=f'basket-paid:{c.id}')
    _notify_organizer(c.basket, c.user, format_amount(c.amount, c.currency))
    return c


def refund(c, reason):
    """Rembourse une participation payée (Stripe) ; Mobile Money : signalé à l'administration."""
    from notifications.models import Notification
    from notifications.services import notify
    if c.status != Contribution.Status.PAID:
        return True
    done = False
    if c.stripe_payment_intent_id:
        try:
            import stripe
            from tickets.stripe_service import _configure
            _configure()
            stripe.Refund.create(payment_intent=c.stripe_payment_intent_id, reverse_transfer=True,
                                 metadata={'basket_contribution_id': str(c.id), 'reason': reason},
                                 idempotency_key=f'basket-refund-{c.id}')
            done = True
        except Exception:
            logger.exception('Remboursement Stripe de la participation %s impossible', c.id)
    else:
        logger.error('Remboursement Mobile Money à faire : participation %s, référence %s, %s FCFA',
                     c.id, c.mobile_money_reference, c.mobile_money_amount)
    Contribution.objects.filter(pk=c.pk).update(
        status=Contribution.Status.REFUNDED if done else Contribution.Status.REFUND_NEEDED)
    notify(c.user, Notification.Type.PAYMENT_REFUNDED, 'Participation remboursée' if done else 'Remboursement en cours',
           f'{format_amount(c.amount, c.currency)} — panier « {c.basket.title} ».', event=c.basket.event,
           dedupe_key=f'basket-refund:{c.id}')
    return done


def refund_event(event, reason='event_cancelled'):
    """Événement annulé : toutes les participations payées sont remboursées. Renvoie le nombre d'échecs."""
    failed = 0
    for c in Contribution.objects.select_related('basket', 'basket__event', 'user').filter(
            basket__event=event, status=Contribution.Status.PAID):
        failed += 0 if refund(c, reason) else 1
    Basket.objects.filter(event=event, status=Basket.Status.OPEN).update(status=Basket.Status.CLOSED,
                                                                      closed_at=timezone.now())
    return failed


# ─────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────
def format_amount(amount, currency):
    if amount is None:
        return ''
    if currency in ZERO_DECIMAL:
        return f'{int(amount):,}'.replace(',', ' ') + (' FCFA' if currency in ('XAF', 'XOF') else f' {currency}')
    text = f'{Decimal(amount):.2f}'.replace('.', ',')
    return f'{text} €' if currency == 'EUR' else f'{text} {currency}'


def _notify_organizer(basket, user, what):
    """Une notification par panier tant qu'elle n'est pas lue (mise à jour à chaque ajout)."""
    from notifications.models import Notification
    from notifications.services import notify
    event = basket.event
    if user.id == event.organizer_id:
        return
    body = f'a ajouté au panier « {basket.title} » : {what}'[:255]
    existing = Notification.objects.filter(user=event.organizer, type=Notification.Type.BASKET_CONTRIBUTION,
                                           event=event, read_at__isnull=True).first()
    if existing:
        existing.title, existing.body, existing.actor, existing.created_at = user.full_name[:160], body, user, timezone.now()
        existing.save(update_fields=['title', 'body', 'actor', 'created_at'])
        return
    notify(event.organizer, Notification.Type.BASKET_CONTRIBUTION, user.full_name, body, actor=user, event=event,
           data={'basket_id': str(basket.id)})


def announce(event, user):
    """Nouvel invité (invitation acceptée, billet) : on lui dit qu'un panier est en cours."""
    basket = open_basket(event)
    if basket is None or user is None or user.id == event.organizer_id:
        return None
    from notifications.models import Notification
    from notifications.services import notify
    n = basket.contributions.filter(status__in=(Contribution.Status.CONFIRMED, Contribution.Status.PAID)).count()
    body = (f'Un panier est ouvert : {n} contribution{"s" if n > 1 else ""} déjà. Ajoutez la vôtre !' if n
            else 'Un panier est ouvert : soyez le premier à y contribuer !')
    return notify(user, Notification.Type.BASKET_OPEN, basket.title, body, event=event,
                  data={'basket_id': str(basket.id)}, dedupe_key=f'basket-open:{basket.id}:{user.id}')


# ─────────────────────────────────────────────────────────────
# Bilan
# ─────────────────────────────────────────────────────────────
def summary(basket, viewer):
    from events.team import is_manager
    from invitations.services import initials
    manager = is_manager(basket.event, viewer)
    visible = basket.contributions.select_related('user').filter(
        status__in=(Contribution.Status.CONFIRMED, Contribution.Status.PAID))
    rows, items, money = [], 0, Decimal(0)
    people = set()
    for c in visible:
        people.add(c.user_id)
        hide = c.kind == 'money' and c.anonymous and not manager and c.user_id != viewer.id
        if c.kind == 'item':
            items += c.quantity
        else:
            money += c.amount or 0
        rows.append({
            'id': str(c.id), 'kind': c.kind, 'label': c.label, 'quantity': c.quantity,
            'amount': None if hide else (float(c.amount) if c.amount is not None else None),
            'amount_text': 'Montant masqué' if hide else format_amount(c.amount, c.currency),
            'anonymous': c.anonymous, 'message': c.message, 'created_at': c.created_at.isoformat(),
            'user': {'id': str(c.user_id), 'name': c.user.full_name,
                     'initials': initials(c.user.first_name, c.user.last_name), 'avatar_url': c.user.avatar_url},
            'mine': c.user_id == viewer.id,
            'can_remove': c.kind == 'item' and (c.user_id == viewer.id or manager),
        })
    mine_pending = [{'id': str(c.id), 'amount_text': format_amount(c.amount, c.currency), 'status': c.status}
                    for c in basket.contributions.filter(user=viewer, kind='money',
                                                         status__in=('awaiting_payment', 'processing', 'failed'))]
    organizer = basket.event.organizer
    return {
        'id': str(basket.id), 'event_id': str(basket.event_id), 'event_title': basket.event.title,
        'title': basket.title, 'description': basket.description, 'status': basket.status,
        'currency': basket.currency, 'allow_items': basket.allow_items, 'allow_money': basket.allow_money,
        'goal_amount': float(basket.goal_amount) if basket.goal_amount is not None else None,
        'goal_text': format_amount(basket.goal_amount, basket.currency) if basket.goal_amount is not None else '',
        'total_money': float(money), 'total_money_text': format_amount(money, basket.currency),
        'progress': min(100, round(float(money) * 100 / float(basket.goal_amount))) if basket.goal_amount else None,
        'items_count': items, 'contributors': len(people), 'contributions': rows, 'my_pending': mine_pending,
        'can_manage': manager, 'created_by': basket.created_by.full_name if basket.created_by else '',
        'created_at': basket.created_at.isoformat(),
        'payment': {
            'card': bool(organizer.stripe_account_id and organizer.stripe_charges_enabled),
            'mobile_money': _mobile_money_ok(basket.currency),
        },
    }


def _mobile_money_ok(currency):
    from tickets import mobile_money
    return mobile_money.available() and currency in ('EUR', 'XAF')


def total_paid(event):
    agg = Contribution.objects.filter(basket__event=event, status=Contribution.Status.PAID).aggregate(s=Sum('amount'))
    return agg['s'] or Decimal(0)
