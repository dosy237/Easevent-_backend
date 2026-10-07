"""
messaging/consumers.py — WebSocket /ws/

Protocole (JSON) :
  client → {"type": "auth", "token": "<access JWT>"}   (dans les 10 s, sinon fermeture 4401)
  serveur → {"type": "ready"}
  client → {"type": "typing", "conversation_id": "<uuid>"}
  client → {"type": "ping"}  → {"type": "pong"}
  serveur → message | typing | read | badge | likes  (voir messaging/realtime.py)
Le jeton n'est jamais placé dans l'URL (journaux des proxys).
"""
import asyncio
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .realtime import PUBLIC_GROUP, group_name

AUTH_TIMEOUT = 10


@database_sync_to_async
def _user_from_token(token):
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken
    from users.models import User
    try:
        access = AccessToken(str(token or ''))
    except TokenError:
        return None
    return User.objects.filter(pk=access.get('user_id'), is_active=True).first()


@database_sync_to_async
def _typing(user, conversation_id):
    from django.db.models import Q
    from . import services
    from .models import Conversation
    from .realtime import broadcast_typing
    try:
        conv_id = uuid.UUID(str(conversation_id))
    except ValueError:
        return
    conv = (Conversation.objects.filter(pk=conv_id, event__deleted_at__isnull=True)
            .filter(Q(organizer=user) | Q(participant=user)).first())
    if conv is None:
        return
    side = conv.side(user)
    services.set_typing(conv, side)
    broadcast_typing(conv, side)


class UserConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.user = None
        await self.accept()
        self._timeout = asyncio.get_running_loop().call_later(
            AUTH_TIMEOUT, lambda: asyncio.ensure_future(self.close(code=4401)))

    async def disconnect(self, code):
        if getattr(self, '_timeout', None):
            self._timeout.cancel()
        if self.user is not None:
            await self.channel_layer.group_discard(group_name(self.user.id), self.channel_name)
            await self.channel_layer.group_discard(PUBLIC_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return
        kind = content.get('type')
        if self.user is None:
            user = await _user_from_token(content.get('token')) if kind == 'auth' else None
            if user is None:
                await self.close(code=4401)
                return
            self.user = user
            self._timeout.cancel()
            await self.channel_layer.group_add(group_name(user.id), self.channel_name)
            # Fil public : compteurs de « J'aime » mis à jour en direct chez tout le monde
            await self.channel_layer.group_add(PUBLIC_GROUP, self.channel_name)
            await self.send_json({'type': 'ready'})
        elif kind == 'typing':
            await _typing(self.user, content.get('conversation_id'))
        elif kind == 'ping':
            await self.send_json({'type': 'pong'})

    async def push_event(self, event):
        await self.send_json(event['payload'])
