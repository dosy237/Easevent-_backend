"""Messagerie instantanée : WebSocket authentifié, message, écriture, lecture en direct."""
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from easevent.asgi import application
from events.models import Event
from invitations.models import Invitation
from users.models import User


def token(user):
    return str(RefreshToken.for_user(user).access_token)


class RealtimeTest(TransactionTestCase):

    def setUp(self):
        mk = lambda e, f: User.objects.create_user(email=e, password='x', first_name=f, last_name='T', is_verified=True)
        self.orga, self.claire, self.zoe = mk('lea@x.fr', 'Léa'), mk('claire@x.fr', 'Claire'), mk('zoe@x.fr', 'Zoé')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(organizer=self.orga, title='Summit', event_type='conference',
                                          start_date=start, end_date=start + timedelta(hours=8),
                                          status='published', visibility='private')
        Invitation.objects.create(event=self.event, invited_user=self.claire, token='t' * 64, status='sent',
                                  channel='platform_notification', expires_at=start + timedelta(days=8),
                                  sent_at=timezone.now())
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {token(self.orga)}')
        self.conv_id = c.post('/api/conversations/', {'event_id': str(self.event.id), 'participant_id': str(self.claire.id)},
                              format='json').data['id']
        self.orga_client = c
        self.tokens = {u.id: token(u) for u in (self.orga, self.claire, self.zoe)}

    def test_temps_reel(self):
        async_to_sync(self._scenario)()

    async def _connect(self, user):
        ws = WebsocketCommunicator(application, '/ws/')
        connected, _ = await ws.connect()
        assert connected
        await ws.send_json_to({'type': 'auth', 'token': self.tokens[user.id]})
        assert (await ws.receive_json_from(timeout=3))['type'] == 'ready'
        return ws

    async def _next(self, ws, kind):
        for _ in range(10):
            msg = await ws.receive_json_from(timeout=3)
            if msg['type'] == kind:
                return msg
        raise AssertionError(f'pas de {kind}')

    async def _scenario(self):
        from channels.db import database_sync_to_async
        claire = await self._connect(self.claire)
        orga = await self._connect(self.orga)

        # Message envoyé par l'API → reçu instantanément par Claire
        post = database_sync_to_async(lambda: self.orga_client.post(
            f'/api/conversations/{self.conv_id}/messages/', {'body': 'Bienvenue !'}, format='json'))
        r = await post()
        assert r.status_code == 201
        msg = await self._next(claire, 'message')
        assert msg['conversation_id'] == self.conv_id
        assert msg['message']['body'] == 'Bienvenue !' and msg['message']['from_me'] is False

        # « En train d'écrire » transmis à l'autre côté
        await claire.send_json_to({'type': 'typing', 'conversation_id': self.conv_id})
        assert (await self._next(orga, 'typing'))['conversation_id'] == self.conv_id

        # Lecture → accusé de lecture chez l'organisatrice
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {self.tokens[self.claire.id]}')
        await database_sync_to_async(lambda: c.get(f'/api/conversations/{self.conv_id}/messages/'))()
        assert (await self._next(orga, 'read'))['conversation_id'] == self.conv_id

        # Une personne étrangère ne peut pas signaler l'écriture dans la conversation
        zoe = await self._connect(self.zoe)
        await zoe.send_json_to({'type': 'typing', 'conversation_id': self.conv_id})
        await zoe.send_json_to({'type': 'ping'})
        assert (await zoe.receive_json_from(timeout=3))['type'] == 'pong'
        for ws in (claire, orga, zoe):
            await ws.disconnect()

    def test_jeton_invalide_ferme_la_connexion(self):
        async def run():
            ws = WebsocketCommunicator(application, '/ws/')
            await ws.connect()
            await ws.send_json_to({'type': 'auth', 'token': 'faux'})
            out = await ws.receive_output(timeout=3)
            assert out['type'] == 'websocket.close' and out['code'] == 4401
        async_to_sync(run)()
