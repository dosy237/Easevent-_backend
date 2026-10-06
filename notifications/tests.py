"""notifications/tests.py — M17 : création, lecture, rappels, bilans, préférences."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event
from invitations.models import Invitation
from tickets.models import Ticket
from users.models import User

from .models import Notification
from .services import refresh_scheduled


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PUBLIC_BASE_URL='https://easevent.example.com')
class NotificationFlowTest(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.orga = User.objects.create_user(email='lea@x.fr', password='x', first_name='Léa',
                                             last_name='Dubois', is_verified=True)
        self.guest = User.objects.create_user(email='claire@x.fr', password='x', first_name='Claire',
                                              last_name='Lemoine', is_verified=True)
        start = timezone.now() + timedelta(days=10)
        self.event = Event.objects.create(
            organizer=self.orga, title='Soirée Jardin', event_type='anniversaire', start_date=start,
            end_date=start + timedelta(hours=5), status='published', visibility='private')

    def test_parcours_invitation_ticket(self):
        # L'organisatrice invite un membre → « Léa Dubois vous invite à … »
        auth(self.client, self.orga)
        self.client.post(f'/api/events/{self.event.id}/invite/', {'user_ids': [str(self.guest.id)]}, format='json')
        auth(self.client, self.guest)
        r = self.client.get('/api/notifications/')
        self.assertEqual(r.data['unread']['all'], 1)
        n = r.data['results'][0]
        self.assertEqual((n['type'], n['title'], n['body']), ('invitation_received', 'Léa Dubois', 'vous invite à Soirée Jardin'))
        self.assertTrue(n['invitation']['can_answer'])
        self.assertEqual(n['actor']['initials'], 'LD')

        # Accepter → « Invitation acceptée. Validez votre ticket… »
        inv = Invitation.objects.get()
        r = self.client.post(f'/api/invitations/{inv.id}/repondre/', {'status': 'confirmed'}, format='json')
        ticket_id = r.data['ticket_id']
        types = list(Notification.objects.filter(user=self.guest).values_list('type', flat=True))
        self.assertIn('ticket_to_validate', types)
        r = self.client.get('/api/notifications/')
        self.assertFalse(next(x for x in r.data['results'] if x['type'] == 'invitation_received')['invitation']['can_answer'])

        # Valider (gratuit) → « Ticket généré »
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/api/tickets/{ticket_id}/validate/')
        gen = Notification.objects.get(user=self.guest, type='ticket_generated')
        self.assertEqual(str(gen.ticket_id), ticket_id)

    def test_lecture_et_categories(self):
        Notification.objects.create(user=self.guest, type='reminder', category='events', title='Demain')
        Notification.objects.create(user=self.guest, type='payment_failed', category='system', title='Paiement')
        other = Notification.objects.create(user=self.orga, type='reminder', category='events', title='x')
        auth(self.client, self.guest)
        r = self.client.get('/api/notifications/', {'category': 'system'})
        self.assertEqual([n['title'] for n in r.data['results']], ['Paiement'])
        self.assertEqual(r.data['unread'], {'events': 1, 'messages': 0, 'system': 1, 'all': 2})

        first = r.data['results'][0]['id']
        self.assertEqual(self.client.post(f'/api/notifications/{first}/read/').data['unread']['all'], 1)
        # Notification d'un autre compte : introuvable (BOLA)
        self.assertEqual(self.client.post(f'/api/notifications/{other.id}/read/').status_code, 404)
        self.client.post('/api/notifications/read-all/')
        self.assertEqual(self.client.get('/api/notifications/unread-count/').data['unread'], 0)
        other.refresh_from_db()
        self.assertIsNone(other.read_at)

    def test_rappel_j_moins_1_unique(self):
        self.event.start_date = timezone.now() + timedelta(hours=20)
        self.event.save()
        Ticket.objects.create(event=self.event, user=self.guest, status='generated', price='0')
        refresh_scheduled(self.guest, force=True)
        refresh_scheduled(self.guest, force=True)
        reminders = Notification.objects.filter(user=self.guest, type='reminder')
        self.assertEqual(reminders.count(), 1)          # pas de J-7 en plus, pas de doublon
        self.assertEqual(reminders.get().title, 'Demain')

    def test_bilan_du_jour_organisateur(self):
        yesterday = timezone.now() - timedelta(days=1)
        for email in ('a@x.fr', 'b@x.fr'):
            u = User.objects.create_user(email=email, password='x', first_name='A', last_name='B')
            Ticket.objects.create(event=self.event, user=u, status='generated', price='0', generated_at=yesterday)
        refresh_scheduled(self.orga, force=True)
        s = Notification.objects.get(user=self.orga, type='daily_summary')
        self.assertEqual((s.title, s.body), ('2 nouvelles confirmations', 'pour Soirée Jardin'))
        self.assertEqual(s.event_id, self.event.id)

    def test_preferences(self):
        auth(self.client, self.guest)
        self.assertEqual(self.client.get('/api/notifications/preferences/').data, {'reminders': True, 'daily_summary': True})
        r = self.client.patch('/api/notifications/preferences/', {'reminders': False}, format='json')
        self.assertFalse(r.data['reminders'])
        self.assertEqual(self.client.patch('/api/notifications/preferences/', {'reminders': 'non'}, format='json').status_code, 400)

        self.event.start_date = timezone.now() + timedelta(hours=20)
        self.event.save()
        Ticket.objects.create(event=self.event, user=self.guest, status='generated', price='0')
        self.guest.refresh_from_db()
        refresh_scheduled(self.guest, force=True)
        self.assertFalse(Notification.objects.filter(user=self.guest, type='reminder').exists())

    def test_retention_90_jours(self):
        old = Notification.objects.create(user=self.guest, type='reminder', category='events', title='vieux',
                                          created_at=timezone.now() - timedelta(days=91))
        refresh_scheduled(self.guest, force=True)
        self.assertFalse(Notification.objects.filter(pk=old.pk).exists())
