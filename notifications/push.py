"""
notifications/push.py — notifications push sur le téléphone
═══════════════════════════════════════════════════════════════
Envoi par le service Expo Push (https://exp.host), qui relaie vers
Firebase Cloud Messaging (Android) et Apple Push (iOS). Les clés FCM /
APNs sont enregistrées une fois dans le compte Expo (voir
docs/A_REPRENDRE.md) : le serveur n'a besoin d'aucune clé, sauf
EXPO_ACCESS_TOKEN si la « sécurité renforcée des push » est activée.

- push_notification(n) : après la création d'une notification (M17).
- push_to_user(...)    : messages de la messagerie (une push par message).
Les préférences de l'utilisateur sont respectées ; les jetons des
appareils désinstallés (DeviceNotRegistered) sont supprimés.
═══════════════════════════════════════════════════════════════
"""
import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

EXPO_URL = 'https://exp.host/--/api/v2/push/send'
CHUNK = 100
STALE_AFTER = timedelta(hours=1)     # une notification rattrapée en retard n'est pas poussée

# Catégorie / type de notification → préférence qui l'autorise
PREF_OF_TYPE = {
    'reminder': 'reminders',
    'daily_summary': 'daily_summary',
    'guest_response': 'guest_responses',
    'message_received': 'messages',
}


def _allowed(user, type_):
    from .services import prefs_of
    prefs = prefs_of(user)
    if not prefs.get('push', True):
        return False
    key = PREF_OF_TYPE.get(type_)
    return prefs.get(key, True) if key else True


def _has_devices(user_id):
    from .models import DeviceToken
    return DeviceToken.objects.filter(user_id=user_id).exists()


def push_notification(n):
    """Pousse une notification fraîchement créée, si l'utilisateur a un appareil enregistré."""
    if not getattr(settings, 'PUSH_ENABLED', True) or n is None:
        return
    if n.created_at < timezone.now() - STALE_AFTER:
        return
    if not _has_devices(n.user_id) or not _allowed(n.user, n.type):
        return
    data = {'type': n.type, 'notification_id': str(n.id)}
    if n.event_id:
        data['event_id'] = str(n.event_id)
    if n.ticket_id:
        data['ticket_id'] = str(n.ticket_id)
    for key in ('conversation_id', 'friendship_id', 'tab'):
        if (n.data or {}).get(key):
            data[key] = str(n.data[key])
    _queue(n.user_id, n.title, n.body, data, thread=str(n.event_id or n.type))


def push_to_user(user, type_, title, body, data=None, thread=None):
    if not getattr(settings, 'PUSH_ENABLED', True) or user is None:
        return
    if not _has_devices(user.id) or not _allowed(user, type_):
        return
    _queue(user.id, title, body, {'type': type_, **(data or {})}, thread=thread)


def _queue(user_id, title, body, data, thread=None):
    from easevent.dispatch import dispatch
    from .tasks import send_push
    dispatch(send_push, str(user_id), title[:120], (body or '')[:240], data, thread)


def unread_badge(user_id):
    from .models import Notification
    return Notification.objects.filter(user_id=user_id, read_at__isnull=True).count()


def deliver(user_id, title, body, data, thread=None):
    """Envoi effectif (appelé par la tâche Celery). Retourne le nombre de messages acceptés."""
    from .models import DeviceToken
    tokens = list(DeviceToken.objects.filter(user_id=user_id).values_list('token', flat=True))
    if not tokens:
        return 0
    badge = unread_badge(user_id)
    messages = [{
        'to': t, 'title': title, 'body': body, 'data': data, 'sound': 'default',
        'channelId': 'default', 'priority': 'high', 'badge': badge,
        **({'threadId': thread} if thread else {}),
    } for t in tokens]
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    from adminpanel.keys import get_key
    if get_key('EXPO_ACCESS_TOKEN'):
        headers['Authorization'] = f"Bearer {get_key('EXPO_ACCESS_TOKEN')}"
    ok = 0
    for i in range(0, len(messages), CHUNK):
        chunk = messages[i:i + CHUNK]
        try:
            res = requests.post(EXPO_URL, json=chunk, headers=headers, timeout=10)
            res.raise_for_status()
            tickets = res.json().get('data', [])
        except Exception as exc:
            logger.warning('Push Expo indisponible : %s', exc)
            continue
        for msg, ticket in zip(chunk, tickets):
            if ticket.get('status') == 'ok':
                ok += 1
            elif (ticket.get('details') or {}).get('error') == 'DeviceNotRegistered':
                DeviceToken.objects.filter(token=msg['to']).delete()     # application désinstallée
            else:
                logger.info('Push refusée : %s', ticket.get('message'))
    return ok
