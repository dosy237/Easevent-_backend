"""
notifications/tasks.py — tâches planifiées (service celery-beat)

  send_event_reminders   toutes les heures : rappels J-7 / J-1 / jour J,
                         email la veille de l'événement
  send_daily_summaries   20:00 : bilan des confirmations pour les organisateurs
  nightly_cleanup        03:30 : tickets et invitations expirés,
                         notifications de plus de 90 jours
Chaque tâche est idempotente (dedupe_key) : la relancer ne crée aucun doublon.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='notifications.send_push', ignore_result=True)
def send_push(user_id, title, body, data, thread=None):
    from .push import deliver
    return deliver(user_id, title, body, data, thread)


def _send_reminder_email(notification):
    from easevent.media import public_url
    from invitations.services import fr_datetime

    ticket, event, user = notification.ticket, notification.event, notification.user
    context = {
        'first_name': user.first_name,
        'event_title': event.title,
        'date': fr_datetime(event.start_date, event),
        'location': 'En ligne' if event.is_online else (event.location_address or ''),
        'online_link': event.online_link if event.is_online else '',
        'dress_code': event.dress_code or '',
        'ticket_number': ticket.number if ticket else '',
        'cover_url': public_url(event.cover_image) if event.cover_image else '',
    }
    msg = EmailMultiAlternatives(
        f'Demain : {event.title}'[:180],
        render_to_string('notifications/emails/reminder.txt', context),
        settings.DEFAULT_FROM_EMAIL, [user.email],
    )
    msg.attach_alternative(render_to_string('notifications/emails/reminder.html', context), 'text/html')
    msg.send(fail_silently=False)


@shared_task(name='notifications.tasks.send_event_reminders')
def send_event_reminders():
    from tickets.models import Ticket
    from users.models import User
    from .services import _reminders, prefs_of

    now = timezone.now()
    user_ids = (Ticket.objects.filter(
        status=Ticket.Status.GENERATED, event__deleted_at__isnull=True,
        event__start_date__gt=now, event__start_date__lte=now + timedelta(days=7, hours=1),
    ).values_list('user_id', flat=True).distinct())
    created = emailed = 0
    for user in User.objects.filter(id__in=list(user_ids), is_active=True):
        if not prefs_of(user)['reminders']:
            continue
        for n in _reminders(user, now):
            created += 1
            if n.data.get('step') == 'J-1':
                try:
                    _send_reminder_email(n)
                    emailed += 1
                except Exception:
                    logger.exception('Email de rappel non envoyé (notification %s)', n.pk)
    return {'created': created, 'emailed': emailed}


@shared_task(name='notifications.tasks.send_daily_summaries')
def send_daily_summaries():
    from tickets.models import Ticket
    from users.models import User
    from .services import _daily_summaries, prefs_of

    now = timezone.now()
    organizer_ids = (Ticket.objects.filter(
        status=Ticket.Status.GENERATED, generated_at__gte=now - timedelta(days=1, hours=1),
    ).values_list('event__organizer_id', flat=True).distinct())
    created = 0
    for user in User.objects.filter(id__in=list(organizer_ids), is_active=True):
        if prefs_of(user)['daily_summary']:
            created += len(_daily_summaries(user, now))
    return {'created': created}


@shared_task(name='notifications.tasks.nightly_cleanup')
def nightly_cleanup():
    from invitations.models import Invitation
    from tickets.models import Ticket
    from tickets.services import expire_old_tickets
    from .models import Notification
    from .services import RETENTION_DAYS

    now = timezone.now()
    expire_old_tickets(Ticket.objects.all())
    invitations = Invitation.objects.filter(status__in=('sent', 'opened'), expires_at__lt=now) \
        .update(status='expired', updated_at=now)
    deleted, _ = Notification.objects.filter(created_at__lt=now - timedelta(days=RETENTION_DAYS)).delete()
    return {'invitations_expired': invitations, 'notifications_deleted': deleted}
