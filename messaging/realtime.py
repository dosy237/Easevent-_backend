"""
messaging/realtime.py — diffusion temps réel (WebSocket, Django Channels)
═══════════════════════════════════════════════════════════════
Chaque utilisateur connecté a un groupe « user.<id> ». Le serveur y
envoie, après validation de la transaction :
  message  : nouveau message d'une conversation (déjà sérialisé pour lui)
  typing   : l'interlocuteur écrit
  read     : l'interlocuteur a lu jusqu'à <read_at>
  badge    : les compteurs (notifications, messages, tickets) ont changé
Si Redis ou le service WebSocket est absent, rien ne casse : l'application
retombe sur l'interrogation périodique.
═══════════════════════════════════════════════════════════════
"""
import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def group_name(user_id):
    return f'user.{user_id}'


def _send_now(user_id, payload):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(group_name(user_id), {'type': 'push.event', 'payload': payload})
    except Exception as exc:      # Redis indisponible : le client rattrape par interrogation
        logger.info('Diffusion temps réel impossible (%s)', type(exc).__name__)


def send_to_user(user_id, payload):
    transaction.on_commit(lambda: _send_now(user_id, payload))


def broadcast_message(conv, msg):
    from .views import _message
    for user in (conv.organizer, conv.participant):
        send_to_user(user.id, {'type': 'message', 'conversation_id': str(conv.id),
                               'message': _message(msg, user, None, None)})
        send_to_user(user.id, {'type': 'badge'})


def broadcast_typing(conv, side):
    other = conv.participant_id if side == 'organizer' else conv.organizer_id
    send_to_user(other, {'type': 'typing', 'conversation_id': str(conv.id)})


def broadcast_read(conv, side, read_at):
    other = conv.participant_id if side == 'organizer' else conv.organizer_id
    send_to_user(other, {'type': 'read', 'conversation_id': str(conv.id), 'read_at': read_at.isoformat()})


def broadcast_badge(user_id):
    send_to_user(user_id, {'type': 'badge'})
