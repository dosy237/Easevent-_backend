"""
notifications/services.py
═══════════════════════════════════════════════════════════════
Création des notifications (MVP §5).

- notify() : appelée par les autres apps (invitations, tickets, Stripe).
  Ne lève jamais d'exception : une notification ratée ne doit pas faire
  échouer l'action principale.
- refresh_scheduled() : rappels J-7 / J-1 / jour J et bilan du jour de
  l'organisateur. Générés à la consultation plutôt que par une tâche
  planifiée : aucun calcul pour les comptes inactifs (éco-conception),
  rien à configurer sur le serveur.
═══════════════════════════════════════════════════════════════
"""
import logging
from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90          # au-delà, les notifications sont supprimées
REFRESH_EVERY = 300          # secondes entre deux calculs des rappels d'un compte
SUMMARY_LOOKBACK_DAYS = 7
SUMMARY_HOUR = 20            # « Bilan du jour · 20:00 »
DEFAULT_PREFS = {'reminders': True, 'daily_summary': True}


def prefs_of(user):
    return {**DEFAULT_PREFS, **(user.notification_prefs or {})}


def notify(user, type_, title, body='', *, actor=None, event=None, invitation=None, ticket=None,
           data=None, dedupe_key=None, created_at=None):
    """Crée une notification (idempotente si dedupe_key). Retourne l'objet ou None."""
    if user is None:
        return None
    fields = dict(
        type=type_, category=Notification.CATEGORY_OF[type_], title=title[:160], body=body[:255],
        actor=actor, invitation=invitation, ticket=ticket, data=data or {},
    )
    # event : objet Event ou simple identifiant
    if event is not None:
        fields['event' if hasattr(event, 'pk') else 'event_id'] = event
    if created_at:
        fields['created_at'] = created_at
    try:
        with transaction.atomic():
            if dedupe_key:
                obj, _ = Notification.objects.get_or_create(user=user, dedupe_key=dedupe_key, defaults=fields)
                return obj
            return Notification.objects.create(user=user, **fields)
    except IntegrityError:
        return Notification.objects.filter(user=user, dedupe_key=dedupe_key).first()
    except Exception:
        logger.exception('Notification non créée (%s)', type_)
        return None


def notify_on_commit(*args, **kwargs):
    """Même chose, après la validation de la transaction en cours."""
    transaction.on_commit(lambda: notify(*args, **kwargs))


# ─────────────────────────────────────────────────────────────
# Notifications planifiées (calculées à la consultation)
# ─────────────────────────────────────────────────────────────
def _local_dt(day, hour):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)), timezone.get_current_timezone())


def _hhmm(dt):
    dt = timezone.localtime(dt)
    return f'{dt:%H}:{dt:%M}'


def _reminders(user, now):
    from tickets.models import Ticket

    tickets = (Ticket.objects.select_related('event')
               .filter(user=user, status=Ticket.Status.GENERATED,
                       event__start_date__gt=now, event__start_date__lte=now + timedelta(days=7, hours=1),
                       event__deleted_at__isnull=True))
    for t in tickets:
        start = t.event.start_date
        day_start = _local_dt(timezone.localtime(start).date(), 8)
        steps = [
            ('J-7', start - timedelta(days=7), 'Dans 7 jours', f'{t.event.title} · le {timezone.localtime(start):%d/%m} à {_hhmm(start)}'),
            ('J-1', start - timedelta(days=1), 'Demain', f'{t.event.title} à {_hhmm(start)}'),
            ('J0', min(day_start, start - timedelta(hours=2)), "Aujourd'hui", f'{t.event.title} à {_hhmm(start)}'),
        ]
        due = [s for s in steps if s[1] <= now]
        if not due:
            continue
        key, at, title, body = due[-1]           # seulement le rappel le plus récent
        notify(user, Notification.Type.REMINDER, title, body, event=t.event, ticket=t,
               data={'step': key}, dedupe_key=f'reminder:{t.id}:{key}', created_at=at)


def _daily_summaries(user, now):
    from tickets.models import Ticket

    today = timezone.localdate(now)
    start = _local_dt(today - timedelta(days=SUMMARY_LOOKBACK_DAYS), 0)
    rows = (Ticket.objects
            .filter(event__organizer=user, status=Ticket.Status.GENERATED,
                    generated_at__gte=start, generated_at__lt=_local_dt(today, 0))
            .annotate(day=TruncDate('generated_at', tzinfo=timezone.get_current_timezone()))
            .values('event_id', 'event__title', 'day')
            .annotate(n=Count('id')))
    for r in rows:
        n = r['n']
        notify(
            user, Notification.Type.DAILY_SUMMARY,
            f"{n} nouvelle{'s' if n > 1 else ''} confirmation{'s' if n > 1 else ''}",
            f"pour {r['event__title']}",
            event=r['event_id'],
            data={'day': r['day'].isoformat(), 'count': n},
            dedupe_key=f"summary:{r['event_id']}:{r['day'].isoformat()}",
            created_at=_local_dt(r['day'], SUMMARY_HOUR),
        )


def refresh_scheduled(user, force=False):
    """Rappels, bilans et nettoyage — au plus toutes les 5 minutes par compte."""
    key = f'notif-refresh:{user.id}'
    if not force and cache.get(key):
        return
    cache.set(key, 1, REFRESH_EVERY)
    now = timezone.now()
    prefs = prefs_of(user)
    try:
        if prefs['reminders']:
            _reminders(user, now)
        if prefs['daily_summary']:
            _daily_summaries(user, now)
        Notification.objects.filter(user=user, created_at__lt=now - timedelta(days=RETENTION_DAYS)).delete()
    except Exception:
        logger.exception('Calcul des notifications planifiées impossible')
