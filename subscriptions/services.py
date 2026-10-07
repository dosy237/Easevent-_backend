"""
subscriptions/services.py — abonnements Easevent (M20 / M21 / M22)
═══════════════════════════════════════════════════════════════
Paiement par Stripe Checkout en mode abonnement (carte, Apple Pay,
Google Pay) : aucune donnée bancaire ne passe par Easevent.

- Les prix (produit + prix mensuel / annuel) sont créés automatiquement
  dans Stripe au premier achat, retrouvés ensuite par leur « lookup_key ».
  On peut aussi imposer ses propres prix : STRIPE_PRICE_STANDARD_MONTHLY…
- Le plan de l'utilisateur (users.User.subscription_plan) n'est changé
  que par les webhooks Stripe signés, jamais par l'application.
- Changer de plan : modification de l'abonnement existant (au prorata).
- Résilier : à la fin de la période payée (accès conservé jusque-là).
═══════════════════════════════════════════════════════════════
"""
import logging
from datetime import datetime, timezone as dt_timezone

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Subscription

logger = logging.getLogger(__name__)

PAID_PLANS = ('standard', 'pro')
INTERVALS = {'monthly': 'month', 'annual': 'year'}

# Catalogue affiché dans l'application (M20). Prix en centimes d'euro.
PLANS = {
    'free': {
        'name': 'Gratuit', 'monthly': 0, 'annual': 0,
        'features': ['1 événement par mois', "Jusqu'à 50 invités par événement", 'Invitations par email, SMS et contacts',
                     'Billetterie et paiements en ligne', 'Messagerie avec vos invités', 'Questions RSVP'],
    },
    'standard': {
        'name': 'Standard', 'monthly': 999, 'annual': 9990, 'highlight': True,
        'features': ['Événements illimités', "Jusqu'à 500 invités par événement", 'Export de la liste des invités (Excel / CSV)',
                     'Tout le plan Gratuit', 'Support prioritaire par email'],
    },
    'pro': {
        'name': 'Pro', 'monthly': 2499, 'annual': 24990,
        'features': ['Événements illimités', 'Invités illimités', 'Export de la liste des invités (Excel / CSV)',
                     'Tout le plan Standard', 'Accompagnement personnalisé'],
    },
}


class SubscriptionError(Exception):
    def __init__(self, message, code='invalid', status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def _configure():
    if not settings.STRIPE_SECRET_KEY:
        raise SubscriptionError('Les paiements ne sont pas encore configurés sur le serveur.', 'payments_unavailable', 503)
    stripe.api_key = settings.STRIPE_SECRET_KEY
    stripe.max_network_retries = 2


def _plain(obj):
    return obj.to_dict() if hasattr(obj, 'to_dict') else obj


def _ts(value):
    return datetime.fromtimestamp(value, tz=dt_timezone.utc) if value else None


def current(user):
    """Abonnement Stripe le plus récent de l'utilisateur (actif ou non)."""
    return user.subscriptions.exclude(stripe_sub_id__isnull=True).order_by('-created_at').first()


def state(user):
    sub = current(user)
    active = sub if sub and sub.status in ('active', 'past_due') else None
    return {
        'plan': user.subscription_plan,
        'status': active.status if active else None,
        'interval': active.billing_interval if active else None,
        'current_period_end': active.current_period_end.isoformat() if active and active.current_period_end else None,
        'cancel_at': active.cancel_at.isoformat() if active and active.cancel_at else None,
        'can_manage': bool(user.stripe_customer_id),
        'payments_available': bool(settings.STRIPE_SECRET_KEY),
    }


def catalog():
    return [{'id': key, 'name': p['name'], 'monthly': p['monthly'], 'annual': p['annual'],
             'highlight': p.get('highlight', False), 'features': p['features'],
             'guest_limit': settings.PLAN_GUEST_LIMITS.get(key),
             'event_limit': settings.PLAN_EVENT_LIMITS.get(key)} for key, p in PLANS.items()]


# ─────────────────────────────────────────────────────────────
# Stripe : client, prix
# ─────────────────────────────────────────────────────────────
def _customer(user):
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(
        email=user.email, name=user.full_name, metadata={'user_id': str(user.id)},
        idempotency_key=f'customer-{user.id}')
    user.stripe_customer_id = customer['id']
    user.save(update_fields=['stripe_customer_id', 'updated_at'])
    return user.stripe_customer_id


_PRICE_CACHE = {}


def price_id(plan, interval):
    """Prix Stripe du plan : variable d'environnement, sinon retrouvé / créé par lookup_key."""
    override = getattr(settings, f'STRIPE_PRICE_{plan.upper()}_{interval.upper()}', '')
    if override:
        return override
    key = f'easevent_{plan}_{interval}'
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    found = stripe.Price.list(lookup_keys=[key], active=True, limit=1)
    data = _plain(found).get('data') or []
    if data:
        _PRICE_CACHE[key] = data[0]['id']
        return data[0]['id']
    product = stripe.Product.create(name=f"Easevent {PLANS[plan]['name']}",
                                    metadata={'plan': plan}, idempotency_key=f'product-{plan}')
    price = stripe.Price.create(
        product=product['id'], currency='eur', unit_amount=PLANS[plan][interval],
        recurring={'interval': INTERVALS[interval]}, lookup_key=key, metadata={'plan': plan, 'interval': interval},
        idempotency_key=f'price-{key}-{PLANS[plan][interval]}')
    _PRICE_CACHE[key] = price['id']
    return price['id']


def _return_url(path):
    base = (settings.PUBLIC_BASE_URL or '').rstrip('/')
    return f'{base}{path}'


# ─────────────────────────────────────────────────────────────
# Actions de l'utilisateur
# ─────────────────────────────────────────────────────────────
def start(user, plan, interval):
    """
    Nouveau plan payant → URL Stripe Checkout ; déjà abonné → changement
    immédiat du plan (au prorata), sans repasser par la page de paiement.
    """
    if plan not in PAID_PLANS:
        raise SubscriptionError('Plan inconnu.', 'invalid_plan')
    if interval not in INTERVALS:
        raise SubscriptionError('Fréquence inconnue (monthly ou annual).', 'invalid_interval')
    _configure()
    sub = current(user)
    if sub and sub.status in ('active', 'past_due'):
        if sub.plan == plan and sub.billing_interval == interval:
            raise SubscriptionError('Vous avez déjà ce plan.', 'same_plan')
        remote = _plain(stripe.Subscription.retrieve(sub.stripe_sub_id))
        item = remote['items']['data'][0]['id']
        updated = stripe.Subscription.modify(
            sub.stripe_sub_id, items=[{'id': item, 'price': price_id(plan, interval)}],
            proration_behavior='create_prorations', cancel_at_period_end=False,
            metadata={'user_id': str(user.id), 'plan': plan, 'interval': interval})
        sync(_plain(updated))
        return {'changed': True, 'state': state(user)}

    session = stripe.checkout.Session.create(
        mode='subscription',
        customer=_customer(user),
        line_items=[{'price': price_id(plan, interval), 'quantity': 1}],
        client_reference_id=str(user.id),
        metadata={'user_id': str(user.id), 'plan': plan, 'interval': interval},
        subscription_data={'metadata': {'user_id': str(user.id), 'plan': plan, 'interval': interval}},
        allow_promotion_codes=True,
        locale='fr',
        success_url=_return_url('/api/payments/return/?flow=subscription&status=success'),
        cancel_url=_return_url('/api/payments/return/?flow=subscription&status=cancel'),
    )
    return {'checkout_url': session['url']}


def set_cancel(user, cancel):
    sub = current(user)
    if not sub or sub.status not in ('active', 'past_due'):
        raise SubscriptionError("Vous n'avez pas d'abonnement en cours.", 'no_subscription', 404)
    _configure()
    updated = stripe.Subscription.modify(sub.stripe_sub_id, cancel_at_period_end=bool(cancel))
    sync(_plain(updated))
    return state(user)


def portal(user):
    """Portail Stripe : factures, carte bancaire, résiliation."""
    if not user.stripe_customer_id:
        raise SubscriptionError("Vous n'avez pas encore d'abonnement.", 'no_customer', 404)
    _configure()
    try:
        session = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id, return_url=_return_url('/api/payments/return/?flow=subscription&status=portal'),
            locale='fr')
    except stripe.error.InvalidRequestError:
        raise SubscriptionError("Le portail de facturation n'est pas encore activé.", 'portal_unavailable', 503)
    return session['url']


def cancel_now_for_deleted_account(user):
    sub = current(user)
    if not sub or sub.status not in ('active', 'past_due') or not settings.STRIPE_SECRET_KEY:
        return
    _configure()
    stripe.Subscription.cancel(sub.stripe_sub_id)


# ─────────────────────────────────────────────────────────────
# Webhooks (appelés par tickets.stripe_service.handle_event)
# ─────────────────────────────────────────────────────────────
STATUS_MAP = {
    'active': 'active', 'trialing': 'active', 'past_due': 'past_due', 'unpaid': 'past_due',
    'canceled': 'canceled', 'incomplete_expired': 'canceled', 'paused': 'paused', 'incomplete': 'inactive',
}


def _user_for(obj):
    from users.models import User
    meta = obj.get('metadata') or {}
    user = None
    if meta.get('user_id'):
        import uuid
        try:
            user = User.objects.filter(pk=uuid.UUID(str(meta['user_id']))).first()
        except ValueError:
            user = None
    if user is None and obj.get('customer'):
        user = User.objects.filter(stripe_customer_id=obj['customer']).first()
    return user


def _plan_of(obj):
    meta = obj.get('metadata') or {}
    if meta.get('plan') in PAID_PLANS:
        return meta['plan'], meta.get('interval', 'monthly')
    try:
        price = obj['items']['data'][0]['price']
        lookup = price.get('lookup_key') or ''
        plan = (price.get('metadata') or {}).get('plan') or lookup.split('_')[1]
        interval = 'annual' if price.get('recurring', {}).get('interval') == 'year' else 'monthly'
        return (plan if plan in PAID_PLANS else 'standard'), interval
    except (KeyError, IndexError, TypeError):
        return 'standard', 'monthly'


def sync(obj):
    """Abonnement Stripe → table subscriptions + plan de l'utilisateur."""
    from notifications.models import Notification
    from notifications.services import notify

    user = _user_for(obj)
    if user is None:
        logger.warning('Abonnement Stripe sans utilisateur : %s', obj.get('id'))
        return None
    plan, interval = _plan_of(obj)
    status = STATUS_MAP.get(obj.get('status'), 'inactive')
    item = (obj.get('items') or {}).get('data', [{}])[0] if obj.get('items') else {}
    period_start = obj.get('current_period_start') or item.get('current_period_start')
    period_end = obj.get('current_period_end') or item.get('current_period_end')
    with transaction.atomic():
        sub, created = Subscription.objects.select_for_update().get_or_create(
            stripe_sub_id=obj['id'], defaults={'user': user, 'plan': plan})
        old_status, old_plan = sub.status, sub.plan
        sub.user, sub.plan, sub.status, sub.billing_interval = user, plan, status, interval
        sub.current_period_start, sub.current_period_end = _ts(period_start), _ts(period_end)
        sub.cancel_at = _ts(obj.get('cancel_at')) if obj.get('cancel_at_period_end') or obj.get('cancel_at') else None
        sub.canceled_at = _ts(obj.get('canceled_at')) if status == 'canceled' else None
        sub.save()
        # Accès : plan payant tant que l'abonnement est actif (ou en relance de paiement)
        new_plan = plan if status in ('active', 'past_due') else 'free'
        if user.subscription_plan != new_plan:
            # Un autre abonnement actif plus récent garde la priorité
            other = user.subscriptions.filter(status__in=('active', 'past_due')).exclude(pk=sub.pk).first()
            if new_plan == 'free' and other:
                new_plan = other.plan
            user.subscription_plan = new_plan
            user.save(update_fields=['subscription_plan', 'updated_at'])

    name = PLANS[plan]['name']
    if status == 'active' and (old_status != 'active' or old_plan != plan):
        notify(user, Notification.Type.SUBSCRIPTION, f'Bienvenue dans le plan {name}',
               'Vos nouvelles fonctionnalités sont activées.', data={'plan': plan},
               dedupe_key=f'sub-active:{sub.stripe_sub_id}:{plan}')
    elif status == 'canceled' and old_status != 'canceled':
        notify(user, Notification.Type.SUBSCRIPTION, 'Abonnement terminé',
               f'Votre plan {name} a pris fin. Vous êtes revenu au plan Gratuit.', data={'plan': 'free'},
               dedupe_key=f'sub-ended:{sub.stripe_sub_id}')
    return sub


def handle_stripe_event(kind, obj):
    from notifications.models import Notification
    from notifications.services import notify
    if kind.startswith('checkout.session.'):
        if kind == 'checkout.session.completed' and obj.get('subscription'):
            _configure()
            sync(_plain(stripe.Subscription.retrieve(obj['subscription'])))
        return
    if kind.startswith('customer.subscription.'):
        sync(obj)
        return
    if kind == 'invoice.payment_failed':
        user = _user_for(obj)
        if user:
            notify(user, Notification.Type.SUBSCRIPTION, 'Paiement de votre abonnement refusé',
                   'Mettez à jour votre carte dans Profil › Abonnement pour garder vos avantages.',
                   data={'action': 'manage'}, dedupe_key=f"invoice-failed:{obj.get('id')}")
