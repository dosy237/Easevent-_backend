"""social/tests.py — demandes d'amitié, liste d'amis, notifications."""
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from notifications.models import Notification
from users.models import User

from .models import Friendship


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')


class FriendsTest(TestCase):

    def setUp(self):
        cache.clear()
        self.c = APIClient()
        mk = lambda e, f: User.objects.create_user(email=e, password='x', first_name=f, last_name='T', is_verified=True)
        self.lea, self.marc, self.zoe = mk('lea@x.fr', 'Léa'), mk('marc@x.fr', 'Marc'), mk('zoe@x.fr', 'Zoé')

    def test_demande_puis_acceptation(self):
        auth(self.c, self.lea)
        r = self.c.post('/api/friends/requests/', {'user_id': str(self.marc.id)}, format='json')
        self.assertEqual(r.status_code, 201)
        fid = r.data['id']
        self.assertEqual(self.c.get('/api/friends/').data['outgoing'][0]['user']['first_name'], 'Marc')
        n = Notification.objects.get(user=self.marc, type='friend_request')
        self.assertEqual((n.category, n.title), ('social', 'Léa T'))

        auth(self.c, self.marc)
        notif = self.c.get('/api/notifications/', {'category': 'social'}).data['results'][0]
        self.assertTrue(notif['friendship']['can_answer'])
        self.assertEqual(self.c.post(f'/api/friends/requests/{fid}/accept/').data['status'], 'accepted')
        self.assertEqual([f['user']['first_name'] for f in self.c.get('/api/friends/').data['friends']], ['Léa'])
        self.assertTrue(Notification.objects.filter(user=self.lea, type='friend_accepted').exists())

        # La recherche de membres indique « ami »
        auth(self.c, self.lea)
        res = self.c.get('/api/users/search/', {'q': 'marc'}).data['results']
        self.assertEqual(res[0]['friend_status'], 'friend')

    def test_demande_croisee_accepte_automatiquement(self):
        auth(self.c, self.lea)
        self.c.post('/api/friends/requests/', {'user_id': str(self.marc.id)}, format='json')
        auth(self.c, self.marc)
        r = self.c.post('/api/friends/requests/', {'user_id': str(self.lea.id)}, format='json')
        self.assertEqual(r.data['status'], 'accepted')
        self.assertEqual(Friendship.objects.count(), 1)

    def test_seul_le_destinataire_accepte_et_refus(self):
        auth(self.c, self.lea)
        fid = self.c.post('/api/friends/requests/', {'user_id': str(self.marc.id)}, format='json').data['id']
        self.assertEqual(self.c.post(f'/api/friends/requests/{fid}/accept/').status_code, 400)   # demandeur
        auth(self.c, self.zoe)
        self.assertEqual(self.c.post(f'/api/friends/requests/{fid}/accept/').status_code, 404)   # étranger
        self.assertEqual(self.c.delete(f'/api/friends/requests/{fid}/').status_code, 404)
        auth(self.c, self.marc)
        self.assertEqual(self.c.delete(f'/api/friends/requests/{fid}/').status_code, 204)        # refus
        self.assertFalse(Friendship.objects.exists())

    def test_pas_soi_meme(self):
        auth(self.c, self.lea)
        self.assertEqual(self.c.post('/api/friends/requests/', {'user_id': str(self.lea.id)}, format='json').data['code'], 'self')
