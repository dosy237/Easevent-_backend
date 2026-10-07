"""Partage (amis, lien avec aperçu) et « J'aime » en direct."""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventLike
from events.tests_lifecycle import client_for
from messaging.models import Conversation, Message
from notifications.models import Notification
from social.models import Friendship
from users.models import User


class EngagementTest(TestCase):

    def setUp(self):
        cache.clear()
        mk = lambda e, f: User.objects.create_user(email=e, password='x', first_name=f, last_name='T', is_verified=True)
        self.lea, self.paul, self.zoe, self.orga = mk('lea@x.fr', 'Léa'), mk('paul@x.fr', 'Paul'), mk('zoe@x.fr', 'Zoé'), mk('o@x.fr', 'Orga')
        Friendship.objects.create(requester=self.lea, addressee=self.paul, status='accepted')
        start = timezone.now() + timedelta(days=10)
        common = dict(organizer=self.orga, event_type='concert', start_date=start, end_date=start + timedelta(hours=3),
                      status='published', location_address='Le Trabendo, Paris')
        self.public = Event.objects.create(title='Nuit Électro', visibility='public', **common)
        self.private = Event.objects.create(title='Mariage secret', visibility='private', **common)
        self.c = client_for(self.lea)

    # ── J'aime ──────────────────────────────────────────────────
    def test_aimer_retirer_idempotent(self):
        url = f'/api/events/{self.public.id}/like/'
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.post(url)
        self.assertEqual((r.data['liked'], r.data['likes_count']), (True, 1))
        self.assertEqual(self.c.post(url).data['likes_count'], 1)                 # deux fois : un seul
        self.assertEqual(client_for(self.paul).post(url).data['likes_count'], 2)
        self.assertEqual(self.c.delete(url).data, {'liked': False, 'likes_count': 1})
        self.assertEqual(client_for().post(url).status_code, 401)
        self.assertEqual(self.c.post(f'/api/events/{self.private.id}/like/').status_code, 404)

    def test_compteur_diffuse_a_tous(self):
        with mock.patch('events.tasks.broadcast_likes.apply_async', side_effect=RuntimeError), \
                mock.patch('messaging.realtime.broadcast_public') as bp:
            with self.captureOnCommitCallbacks(execute=True):
                self.c.post(f'/api/events/{self.public.id}/like/')
        bp.assert_called_once_with({'type': 'likes', 'event_id': str(self.public.id), 'count': 1})

    def test_fil_public_indique_compteur_et_mon_like(self):
        EventLike.objects.create(event=self.public, user=self.paul)
        self.c.post(f'/api/events/{self.public.id}/like/')
        feed = {e['id']: e for e in self.c.get('/api/events/publics/').data['events']}
        e = feed[str(self.public.id)]
        self.assertEqual((e['likes_count'], e['liked']), (2, True))
        self.assertTrue(e['share_url'].endswith(f'/e/{self.public.id}/'))
        anon = {x['id']: x for x in client_for().get('/api/events/publics/').data['events']}
        self.assertFalse(anon[str(self.public.id)]['liked'])

    # ── Partage à des amis ─────────────────────────────────────
    def test_partage_a_un_ami_arrive_dans_sa_messagerie(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.post(f'/api/events/{self.public.id}/share/',
                            {'user_ids': [str(self.paul.id), str(self.zoe.id)], 'message': 'On y va ?'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data['sent'], r.data['skipped']), (1, 1))                # Zoé n'est pas son amie
        conv = Conversation.objects.get(event__isnull=True)
        msg = Message.objects.get(conversation=conv)
        self.assertEqual((msg.kind, msg.body, msg.meta['title']), ('event', 'On y va ?', 'Nuit Électro'))
        self.assertTrue(Notification.objects.filter(user=self.paul, type='message_received').exists())
        paul = client_for(self.paul)
        convs = paul.get('/api/conversations/').data['results']
        self.assertEqual(len(convs), 1)
        self.assertTrue(convs[0]['direct'])
        self.assertIsNone(convs[0]['event'])
        self.assertEqual(convs[0]['last_message']['text'], 'On y va ?')
        messages = paul.get(f"/api/conversations/{convs[0]['id']}/messages/").data['results']
        self.assertEqual(messages[0]['event']['event_id'], str(self.public.id))
        # Il répond dans la même conversation
        self.assertEqual(paul.post(f"/api/conversations/{convs[0]['id']}/messages/", {'body': 'Carrément !'}, format='json').status_code, 201)

    def test_partage_refuse(self):
        url = f'/api/events/{self.private.id}/share/'
        self.assertEqual(self.c.post(url, {'user_ids': [str(self.paul.id)]}, format='json').status_code, 404)
        r = self.c.post(f'/api/events/{self.public.id}/share/', {'user_ids': [str(self.zoe.id)]}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.post(f'/api/events/{self.public.id}/share/', {'user_ids': []}, format='json').status_code, 400)

    def test_conversation_directe_entre_amis_seulement(self):
        r = self.c.post('/api/conversations/', {'friend_id': str(self.paul.id)}, format='json')
        self.assertEqual(r.status_code, 201)
        again = self.c.post('/api/conversations/', {'friend_id': str(self.paul.id)}, format='json')
        self.assertEqual(again.data['id'], r.data['id'])                           # une seule par paire
        self.assertEqual(client_for(self.paul).post('/api/conversations/', {'friend_id': str(self.lea.id)},
                                                    format='json').data['id'], r.data['id'])
        self.assertEqual(self.c.post('/api/conversations/', {'friend_id': str(self.zoe.id)}, format='json').status_code, 403)
        Friendship.objects.all().delete()                                           # plus amis : lecture seule
        w = self.c.post(f"/api/conversations/{r.data['id']}/messages/", {'body': 'Salut'}, format='json')
        self.assertEqual(w.status_code, 403)
        self.assertEqual(client_for(self.zoe).get(f"/api/conversations/{r.data['id']}/").status_code, 404)

    # ── Lien avec aperçu ───────────────────────────────────────
    def test_page_de_partage_avec_apercu(self):
        r = client_for().get(f'/e/{self.public.id}/')
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn('<meta property="og:title" content="Nuit Électro">', html)
        self.assertIn('og:image" content="http', html)                              # image absolue
        self.assertIn('easevent://evenement/', html)
        private = client_for().get(f'/e/{self.private.id}/')
        self.assertEqual(private.status_code, 404)
        self.assertNotIn('Mariage secret', private.content.decode())                 # rien ne fuit
