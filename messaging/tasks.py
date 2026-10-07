"""messaging/tasks.py — réponses automatiques (hors de la requête : l'envoi du message reste instantané)"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=60, time_limit=90, ignore_result=True)
def answer_question(message_id):
    from .assistant import handle
    from .models import Message
    msg = Message.objects.select_related('conversation', 'conversation__event', 'conversation__event__organizer',
                                         'conversation__participant').filter(pk=message_id).first()
    if msg is None:
        return
    try:
        handle(msg)
    except Exception:
        logger.exception('Assistant : message %s non traité', message_id)
