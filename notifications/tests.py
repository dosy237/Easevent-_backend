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


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PUBLIC_BASE_URL='https://easevent.example.com')
class CeleryTasksTest(TestCase):
    """Tâches du planificateur et repli quand la file Redis est indisponible."""

    def setUp(self):
        cache.clear()
        self.orga = User.objects.create_user(email='lea@x.fr', password='x', first_name='Léa',
                                             last_name='Dubois', is_verified=True)
        self.guest = User.objects.create_user(email='claire@x.fr', password='x', first_name='Claire',
                                              last_name='Lemoine', is_verified=True)
        start = timezone.now() + timedelta(hours=20)
        self.event = Event.objects.create(
            organizer=self.orga, title='Soirée Jardin', event_type='anniversaire', start_date=start,
            end_date=start + timedelta(hours=5), status='published', visibility='public', dress_code='Chic')

    def test_rappel_veille_par_email_une_seule_fois(self):
        from django.core import mail
        from .tasks import send_event_reminders
        Ticket.objects.create(event=self.event, user=self.guest, status='generated', price='0')
        self.assertEqual(send_event_reminders(), {'created': 1, 'emailed': 1})
        self.assertEqual(send_event_reminders(), {'created': 0, 'emailed': 0})   # idempotent
        self.assertEqual(mail.outbox[0].subject, 'Demain : Soirée Jardin')
        self.assertIn('Chic', mail.outbox[0].alternatives[0][0])

    def test_rappels_desactives(self):
        from .tasks import send_event_reminders
        self.guest.notification_prefs = {'reminders': False}
        self.guest.save()
        Ticket.objects.create(event=self.event, user=self.guest, status='generated', price='0')
        self.assertEqual(send_event_reminders(), {'created': 0, 'emailed': 0})

    def test_bilan_du_soir(self):
        from .tasks import send_daily_summaries
        from .services import _local_dt
        now = timezone.now()
        today = timezone.localdate(now)
        last_day = today if now >= _local_dt(today, 20) else today - timedelta(days=1)
        Ticket.objects.create(event=self.event, user=self.guest, status='generated', price='0',
                              generated_at=_local_dt(last_day, 20) - timedelta(hours=1))
        send_daily_summaries()
        send_daily_summaries()
        self.assertEqual(Notification.objects.filter(user=self.orga, type='daily_summary').count(), 1)

    def test_menage_de_nuit(self):
        from .tasks import nightly_cleanup
        past = timezone.now() - timedelta(days=20)
        old_event = Event.objects.create(organizer=self.orga, title='Passé', event_type='autre',
                                         start_date=past, end_date=past + timedelta(hours=2), status='published')
        inv = Invitation.objects.create(event=old_event, invited_user=self.guest, token='a' * 64, status='sent',
                                        channel='platform_notification', expires_at=past + timedelta(days=7))
        t = Ticket.objects.create(event=old_event, user=self.guest, status='pending', price='0')
        Notification.objects.create(user=self.guest, type='reminder', category='events', title='vieux',
                                    created_at=timezone.now() - timedelta(days=100))
        result = nightly_cleanup()
        inv.refresh_from_db(); t.refresh_from_db()
        self.assertEqual((inv.status, t.status), ('expired', 'expired'))
        self.assertEqual(result['notifications_deleted'], 1)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PUBLIC_BASE_URL='https://easevent.example.com', CELERY_TASK_ALWAYS_EAGER=False)
class DispatchFallbackTest(TestCase):

    def setUp(self):
        cache.clear()
        self.orga = User.objects.create_user(email='lea@x.fr', password='x', first_name='Léa',
                                             last_name='Dubois', is_verified=True)
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(organizer=self.orga, title='Gala', event_type='gala', start_date=start,
                                          end_date=start + timedelta(hours=4), status='published')

    def test_redis_indisponible_envoi_direct(self):
        """La file ne répond pas : l'invitation part quand même, dans la requête."""
        from unittest import mock
        from django.core import mail
        from invitations.services import invite_batch
        with mock.patch('invitations.tasks.send_invitations.apply_async', side_effect=ConnectionError('redis down')), \
                self.captureOnCommitCallbacks(execute=True):
            invite_batch(self.event, self.orga, emails=['julien@x.fr'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Invitation.objects.get().delivery_status, 'sent')

    def test_file_disponible_envoi_differe_puis_rattrapage(self):
        from unittest import mock
        from django.core import mail
        from invitations.services import invite_batch
        from invitations.tasks import retry_stuck_deliveries
        with mock.patch('invitations.tasks.send_invitations.apply_async') as queued, \
                self.captureOnCommitCallbacks(execute=True):
            invite_batch(self.event, self.orga, emails=['julien@x.fr'])
        queued.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)                        # parti dans la file
        inv = Invitation.objects.get()
        self.assertEqual(inv.delivery_status, 'pending')
        # Worker arrêté : le rattrapage planifié envoie après 10 minutes
        Invitation.objects.filter(pk=inv.pk).update(updated_at=timezone.now() - timedelta(minutes=11))
        self.assertEqual(retry_stuck_deliveries(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://easevent.example.com/i/', mail.outbox[0].body)
