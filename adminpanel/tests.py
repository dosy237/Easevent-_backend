from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from events.models import Event
from events.tests_lifecycle import client_for
from events.video import folder_for
from notifications.models import Notification
from tickets.models import Ticket
from users.models import User

from .models import AdminAction, Announcement


class AdminPanelTest(TestCase):
    def setUp(self):
        cache.clear()
        mk = lambda e, **kw: User.objects.create_user(email=e, password='Jardin-Emeraude-26', first_name=e[:3], last_name='T',
                                                      is_verified=True, **kw)
        self.admin = mk('admin@x.fr', is_staff=True)
        self.root = mk('root@x.fr', is_staff=True, is_superuser=True)
        self.lea, self.paul = mk('lea@x.fr'), mk('paul@x.fr')
        start = timezone.now() + timedelta(days=10)
        self.event = Event.objects.create(organizer=self.lea, title='Concert', event_type='concert', status='published',
                                          visibility='public', start_date=start, end_date=start + timedelta(hours=3),
                                          location_address='Paris')
        Ticket.objects.create(event=self.event, user=self.paul, status='generated', price='0')
        self.a = client_for(self.admin)

    def test_reserve_aux_administrateurs(self):
        for url in ('/api/admin/stats/', '/api/admin/users/', '/api/admin/events/', '/api/admin/announcements/'):
            self.assertEqual(client_for(self.lea).get(url).status_code, 403, url)
            self.assertEqual(client_for().get(url).status_code, 401, url)
            self.assertEqual(self.a.get(url).status_code, 200, url)
        self.assertEqual(client_for(self.lea).delete(f'/api/admin/events/{self.event.id}/').status_code, 403)

    def test_statistiques(self):
        d = self.a.get('/api/admin/stats/').data
        self.assertEqual((d['users'], d['events'], d['tickets']), (4, 1, 1))

    def test_comptes(self):
        r = self.a.get('/api/admin/users/?q=lea')
        self.assertEqual([u['email'] for u in r.data['results']], ['lea@x.fr'])
        self.assertEqual(r.data['results'][0]['events'], 1)
        # Création : lien pour choisir le mot de passe
        r = self.a.post('/api/admin/users/', {'email': 'Nina@X.fr', 'first_name': 'Nina', 'last_name': 'B'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(User.objects.get(email='nina@x.fr').has_usable_password())
        self.assertEqual(self.a.post('/api/admin/users/', {'email': 'nina@x.fr', 'first_name': 'N', 'last_name': 'B'}, format='json').status_code, 400)
        # Plan, suspension
        url = f'/api/admin/users/{self.lea.id}/'
        r = self.a.patch(url, {'plan': 'pro', 'is_active': False}, format='json')
        self.assertEqual((r.data['plan'], r.data['is_active']), ('pro', False))
        self.assertEqual(self.a.patch(url, {'plan': 'gold'}, format='json').status_code, 400)
        # Nommer un administrateur : super-administrateur seulement
        self.assertEqual(self.a.patch(url, {'is_staff': True}, format='json').status_code, 403)
        self.assertEqual(client_for(self.root).patch(url, {'is_staff': True}, format='json').status_code, 200)
        # Ni soi-même, ni un super-administrateur
        self.assertEqual(self.a.patch(f'/api/admin/users/{self.admin.id}/', {'plan': 'pro'}, format='json').status_code, 400)
        self.assertEqual(self.a.delete(f'/api/admin/users/{self.root.id}/').status_code, 403)
        # Suppression RGPD : événements annulés, participants prévenus
        self.assertEqual(self.a.delete(url).status_code, 204)
        self.lea.refresh_from_db()
        self.assertIsNotNone(self.lea.deleted_at)
        self.assertTrue(self.lea.email.startswith('deleted_'))
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.deleted_at)
        self.assertTrue(Notification.objects.filter(user=self.paul, type='event_cancelled').exists())
        self.assertEqual(AdminAction.objects.filter(actor=self.admin).count(), 3)

    def test_evenements(self):
        url = f'/api/admin/events/{self.event.id}/'
        r = self.a.get('/api/admin/events/?q=conc')
        self.assertEqual(r.data['results'][0]['participants'], 1)
        r = self.a.patch(url, {'status': 'draft', 'title': 'Concert (modéré)'}, format='json')
        self.assertEqual((r.data['status'], r.data['title']), ('draft', 'Concert (modéré)'))
        self.assertEqual(client_for().get('/api/events/publics/').data['count'], 0)    # retiré du fil
        self.assertEqual(self.a.patch(url, {'status': 'live'}, format='json').status_code, 400)
        self.assertEqual(self.a.patch(url, {'title': '<script>'}, format='json').status_code, 400)
        r = self.a.delete(url)
        self.assertEqual(r.data['notified'], 1)
        self.assertEqual(self.a.get('/api/admin/events/').data['total'], 0)

    def test_annonces(self):
        r = self.a.post('/api/admin/announcements/', {
            'title': 'Nouveau : la conversion de devises', 'body': 'Les prix s’affichent dans votre monnaie.',
            'link_url': 'https://easevent.app/nouveautes', 'priority': 90,
            'ends_at': (timezone.now() + timedelta(days=7)).isoformat()}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['link_label'], 'En savoir plus')
        Announcement.objects.create(title='Ancienne', ends_at=timezone.now() - timedelta(days=1), starts_at=timezone.now() - timedelta(days=3))
        Announcement.objects.create(title='Normale')
        Announcement.objects.create(title='Inactive', is_active=False)
        live = client_for().get('/api/announcements/').data['results']
        self.assertEqual([a['title'] for a in live], ['Nouveau : la conversion de devises', 'Normale'])   # urgente d'abord
        self.assertNotIn('is_active', live[0])
        # Validation
        for bad in ({'title': ''}, {'title': 'x', 'link_url': 'http://insecure.fr'}, {'title': 'x', 'priority': 3},
                    {'title': 'x', 'starts_at': '2030-01-02T00:00:00Z', 'ends_at': '2030-01-01T00:00:00Z'}):
            self.assertEqual(self.a.post('/api/admin/announcements/', bad, format='json').status_code, 400, bad)
        # Vidéo : même contrôle que pour un événement (dossier de l'administrateur, 45 s)
        pid = f'{folder_for(self.admin)}/abc'
        info = {'duration': 30, 'bytes': 1000, 'format': 'mp4', 'width': 1080, 'height': 1920}
        with mock.patch('cloudinary.api.resource', return_value=info):
            r = self.a.patch(f"/api/admin/announcements/{r.data['id']}/", {'video': {'public_id': pid, 'caption': 'En 30 s'}}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['video']['caption'], 'En 30 s')
        with mock.patch('cloudinary.api.resource', return_value={**info, 'duration': 90}), mock.patch('cloudinary.uploader.destroy'):
            r2 = self.a.patch(f"/api/admin/announcements/{r.data['id']}/", {'video': {'public_id': f'{folder_for(self.admin)}/long'}}, format='json')
        self.assertEqual(r2.status_code, 400)
        with mock.patch('cloudinary.uploader.destroy') as destroy:
            self.assertEqual(self.a.delete(f"/api/admin/announcements/{r.data['id']}/").status_code, 204)
        destroy.assert_called_once()

    def test_profil_indique_l_administrateur(self):
        self.assertTrue(client_for(self.admin).get('/api/auth/me/').data.get('is_staff'))
        self.assertFalse(client_for(self.lea).get('/api/auth/me/').data.get('is_staff'))
