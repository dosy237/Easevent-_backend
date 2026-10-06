"""
invitations/tasks.py — envois en arrière-plan (worker Celery)

Le jeton du lien est créé ici, au moment de l'envoi : il ne transite
jamais par la file Redis, seule son empreinte est enregistrée.
"""
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

STUCK_AFTER = timedelta(minutes=10)


@shared_task(name='invitations.tasks.send_invitations', autoretry_for=(ConnectionError,),
             retry_backoff=30, max_retries=3)
def send_invitations(invitation_ids, reminder=False):
    from .services import send_now
    return send_now(invitation_ids, reminder=reminder)


@shared_task(name='invitations.tasks.retry_stuck_deliveries')
def retry_stuck_deliveries():
    """Invitations restées « en cours d'envoi » plus de 10 minutes."""
    from .models import Invitation
    from .services import send_now
    ids = list(Invitation.objects.filter(
        delivery_status='pending', updated_at__lt=timezone.now() - STUCK_AFTER,
        status__in=('sent', 'opened'), expires_at__gt=timezone.now(),
    ).values_list('id', flat=True)[:200])
    if ids:
        send_now([str(i) for i in ids])
    return len(ids)
