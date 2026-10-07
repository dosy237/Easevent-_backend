"""messaging/tests.py — M15 / M16 : accès, messages, non lus, système, notifications."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event
from invitations.models import Invitation
from notifications.models import Notification
from users.models import User

from .models import Conversation, Message


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')


class MessagingTest(TestCase):

    def setUp(self):
        cache.clear()
        self.c = APIClient()
        mk = lambda e, f, l: User.objects.create_user(email=e, password='x', first_name=f, last_name=l, is_verified=True)
        self.orga, self.claire, self.intrus = mk('lea@x.fr', 'Léa', 'Dubois'), mk('claire@x.fr', 'Claire', 'Lemoine'), mk('z@x.fr', 'Zoé', 'Z')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(organizer=self.orga, title='Summit', event_type='conference',
                                          start_date=start, end_date=start + timedelta(hours=8),
                                          status='published', visibility='private')
        self.inv = Invitation.objects.create(event=self.event, invited_user=self.claire, token='t' * 64,
                                             status='sent', channel='platform_notification',
                                             expires_at=start + timedelta(days=8),
                                             sent_at=timezone.now() - timedelta(days=2))

    def open_as_orga(self):
        auth(self.c, self.orga)
        return self.c.post('/api/conversations/', {'event_id': str(self.event.id), 'participant_id': str(self.claire.id)}, format='json')

    def test_echange_complet(self):
        r = self.open_as_orga()
        self.assertEqual(r.status_code, 201, r.data)
        conv_id = r.data['id']
        self.assertEqual(r.data['other']['name'], 'Claire Lemoine')
        self.assertEqual(r.data['last_message']['system_type'], 'invitation_sent')      # historique rappelé
        self.assertEqual(self.open_as_orga().data['id'], conv_id)                       # crée ou retrouve

        r = self.c.post(f'/api/conversations/{conv_id}/messages/', {'body': '  Ravie que tu viennes !  '}, format='json')
        self.assertEqual(r.data['body'], 'Ravie que tu viennes !')

        # Côté invitée : 1 non lu, notification « Nouveau message »
        auth(self.c, self.claire)
        self.assertEqual(self.c.get('/api/conversations/unread-count/').data['unread'], 1)
        notif = Notification.objects.get(user=self.claire, type='message_received')
        self.assertIn('Ravie que tu viennes', notif.body)
        r = self.c.get(f'/api/conversations/{conv_id}/messages/')
        self.assertEqual([m['kind'] for m in r.data['results']], ['system', 'text'])
        self.assertEqual(self.c.get('/api/conversations/unread-count/').data['unread'], 0)
        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)                                            # lu en ouvrant la conversation

        # Réponse + saisie en cours visibles par l'organisatrice, accusé de lecture
        self.c.post(f'/api/conversations/{conv_id}/typing/')
        self.c.post(f'/api/conversations/{conv_id}/messages/', {'body': 'Il y a un parking ?'}, format='json')
        auth(self.c, self.orga)
        r = self.c.get(f'/api/conversations/{conv_id}/messages/')
        mine = next(m for m in r.data['results'] if m['from_me'])
        self.assertTrue(mine['read'])
        self.assertTrue(r.data['other_online'])
        last = r.data['results'][-1]['created_at']
        self.assertEqual(self.c.get(f'/api/conversations/{conv_id}/messages/', {'after': last}).data['results'], [])

    def test_acceptation_apparait_dans_la_messagerie(self):
        auth(self.c, self.claire)
        self.c.post(f'/api/invitations/{self.inv.id}/repondre/', {'status': 'confirmed'}, format='json')
        auth(self.c, self.orga)
        r = self.c.get('/api/conversations/')
        row = r.data['results'][0]
        self.assertEqual(row['last_message']['text'], 'A accepté votre invitation')
        self.assertEqual(row['unread'], 2)                     # invitation envoyée + acceptée
        self.assertEqual(r.data['events'], [{'id': str(self.event.id), 'title': 'Summit'}])

    def test_acces_refuse(self):
        conv_id = self.open_as_orga().data['id']
        auth(self.c, self.intrus)
        self.assertEqual(self.c.get(f'/api/conversations/{conv_id}/messages/').status_code, 404)
        self.assertEqual(self.c.post(f'/api/conversations/{conv_id}/messages/', {'body': 'x'}, format='json').status_code, 404)
        # Événement privé sans invitation : impossible d'ouvrir une conversation
        r = self.c.post('/api/conversations/', {'event_id': str(self.event.id)}, format='json')
        self.assertEqual(r.status_code, 403)
        # L'organisatrice ne peut pas écrire à un non-invité
        auth(self.c, self.orga)
        r = self.c.post('/api/conversations/', {'event_id': str(self.event.id), 'participant_id': str(self.intrus.id)}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_contacter_organisateur_evenement_public(self):
        self.event.visibility = 'public'
        self.event.save()
        auth(self.c, self.intrus)
        r = self.c.post('/api/conversations/', {'event_id': str(self.event.id), 'participant_id': str(self.claire.id)}, format='json')
        self.assertEqual(r.status_code, 201)
        conv = Conversation.objects.get(pk=r.data['id'])
        self.assertEqual(conv.participant, self.intrus)        # participant_id ignoré : on ne parle qu'en son nom
        self.assertEqual(r.data['other']['name'], 'Léa Dubois')

    def test_message_vide_ou_trop_long(self):
        conv_id = self.open_as_orga().data['id']
        url = f'/api/conversations/{conv_id}/messages/'
        self.assertEqual(self.c.post(url, {'body': '   '}, format='json').data['code'], 'empty')
        self.assertEqual(self.c.post(url, {'body': 'x' * 2001}, format='json').data['code'], 'too_long')

    def test_une_notification_par_conversation(self):
        conv_id = self.open_as_orga().data['id']
        for text in ('Bonjour', 'Programme en ligne', 'À bientôt'):
            self.c.post(f'/api/conversations/{conv_id}/messages/', {'body': text}, format='json')
        notifs = Notification.objects.filter(user=self.claire, type='message_received')
        self.assertEqual(notifs.count(), 1)
        self.assertIn('À bientôt', notifs.get().body)
        self.assertEqual(Message.objects.filter(kind='text').count(), 3)


class MediaMessagesTest(MessagingTest):
    """Photos (sans métadonnées, lien signé) et itinéraire."""

    def _png(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (2400, 1200), (200, 30, 30)).save(buf, 'PNG')
        buf.seek(0)
        buf.name = 'capture.png'
        return buf

    def test_photo_reduite_et_lien_signe(self):
        import tempfile
        conv_id = self.open_as_orga().data['id']
        with tempfile.TemporaryDirectory() as tmp, self.settings(PRIVATE_MEDIA_ROOT=tmp):
            r = self.c.post(f'/api/conversations/{conv_id}/messages/', {'image': self._png(), 'body': 'Le plan'}, format='multipart')
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual((r.data['kind'], r.data['image']['width']), ('image', 1600))
            url = r.data['image']['url']
            path = url[url.index('/api/'):]
            self.c.credentials()
            img = self.c.get(path)
            self.assertEqual(img['Content-Type'], 'image/jpeg')
            self.assertEqual(self.c.get('/api/conversations/attachments/faux/').status_code, 404)
            # Suppression de la conversation → fichier effacé
            from pathlib import Path
            self.assertEqual(len(list(Path(tmp).rglob('*.jpg'))), 1)
            Message.objects.filter(kind='image').delete()
            self.assertEqual(len(list(Path(tmp).rglob('*.jpg'))), 0)

    def test_fichier_qui_n_est_pas_une_image(self):
        import io
        conv_id = self.open_as_orga().data['id']
        fake = io.BytesIO(b'%PDF-1.4 pas une image')
        fake.name = 'x.png'
        r = self.c.post(f'/api/conversations/{conv_id}/messages/', {'image': fake}, format='multipart')
        self.assertEqual(r.data['code'], 'invalid_image')

    def test_itineraire(self):
        self.event.location_address = 'Station F, 5 Parvis Alan Turing, Paris'
        self.event.latitude, self.event.longitude = 48.834, 2.371
        self.event.save()
        conv_id = self.open_as_orga().data['id']
        r = self.c.post(f'/api/conversations/{conv_id}/messages/', {'kind': 'location'}, format='json')
        loc = r.data['location']
        self.assertIn('google.com/maps/dir/?api=1&destination=48.834', loc['directions'])
        self.assertIn('/api/geo/static-map/?lat=48.83400&lng=2.37100', loc['map_image'])
        auth(self.c, self.claire)
        self.assertEqual(self.c.get('/api/conversations/').data['results'][0]['last_message']['text'], 'Itinéraire')
