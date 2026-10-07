"""
events/quota.py — nombre d'événements par mois selon le plan
════════════════════════════════════════════════════════════════
Règle (settings.PLAN_EVENT_LIMITS, ex. Gratuit = 1 par mois calendaire) :
- un événement compte pour le mois de sa création ;
- un brouillon supprimé sans avoir été publié rend sa place ;
- un événement publié puis supprimé reste compté (pas de contournement
  « supprimer puis recréer ») ;
- vérifié à la création ET à la publication (brouillons créés pendant un
  abonnement payant puis résilié).
════════════════════════════════════════════════════════════════
"""
from datetime import datetime

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

PLAN_NAMES = {'free': 'Gratuit', 'standard': 'Standard', 'pro': 'Pro'}


class QuotaError(Exception):
    def __init__(self, message, quota):
        super().__init__(message)
        self.message, self.quota = message, quota

    def payload(self):
        return {'detail': self.message, 'code': 'plan_event_limit', 'quota': self.quota}


def _month_bounds(moment):
    local = timezone.localtime(moment)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, nxt


def limit_for(user):
    limits = settings.PLAN_EVENT_LIMITS
    return limits.get(user.subscription_plan, limits['free'])


def _counted(user, start, end):
    from .models import Event
    return Event.objects.filter(organizer=user, created_at__gte=start, created_at__lt=end).filter(
        Q(deleted_at__isnull=True) | Q(published_at__isnull=False))


def usage(user, now=None):
    start, end = _month_bounds(now or timezone.now())
    limit = limit_for(user)
    used = _counted(user, start, end).count()
    return {
        'plan': user.subscription_plan,
        'limit': limit,
        'used': used,
        'remaining': None if limit is None else max(0, limit - used),
        'resets_on': end.date().isoformat(),
    }


def _message(quota):
    plan = PLAN_NAMES.get(quota['plan'], quota['plan'])
    n = quota['limit']
    reset = datetime.fromisoformat(quota['resets_on']).strftime('%d/%m')
    return (f"Le plan {plan} permet {n} événement{'s' if n > 1 else ''} par mois. "
            f"Passez au plan Standard pour en créer autant que vous voulez, ou attendez le {reset}.")


def check_create(user):
    """À appeler dans une transaction, utilisateur verrouillé (select_for_update)."""
    quota = usage(user)
    if quota['limit'] is not None and quota['used'] >= quota['limit']:
        raise QuotaError(_message(quota), quota)
    return quota


def check_publish(event):
    """Un brouillon ne peut être publié que s'il fait partie des N premiers de son mois de création."""
    if event.published_at:
        return
    user = event.organizer
    limit = limit_for(user)
    if limit is None:
        return
    start, end = _month_bounds(event.created_at)
    before = _counted(user, start, end).filter(created_at__lt=event.created_at).exclude(pk=event.pk).count()
    if before >= limit:
        quota = usage(user)
        raise QuotaError(
            f"Le plan {PLAN_NAMES.get(user.subscription_plan, user.subscription_plan)} permet {limit} "
            f"événement{'s' if limit > 1 else ''} par mois et celui-ci dépasse la limite du mois où il a été créé. "
            "Passez au plan Standard pour le publier.", quota)
