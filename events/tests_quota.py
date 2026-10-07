"""
Quota d'événements par plan (events/quota.py) :
Gratuit = 1 événement par mois ; Standard et Pro = illimité ; 50 invités en Gratuit.
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from events.models import Event
from events.tests_lifecycle import client_for
from users.models import User


class EventQuotaTest(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email='orga@x.fr', password='x', first_name='Léa', last_name='T',
                                             is_verified=True)
        self.c = client_for(self.user)
        start = timezone.now() + timedelta(days=20)
        self.body = {'title': 'Soirée', 'event_type': 'soiree', 'visibility': 'public',
                     'start_date': start.isoformat(), 'end_date': (start + timedelta(hours=4)).isoformat(),
                     'location_address': 'Paris'}

    def create(self, **extra):
        return self.c.post('/api/events/create/', {**self.body, **extra}, format='json')

    def set_plan(self, plan):
        self.user.subscription_plan = plan
        self.user.save(update_fields=['subscription_plan'])

    def test_gratuit_un_seul_evenement_par_mois(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['quota']['remaining'], 0)
        r2 = self.create(title='Deuxième')
        self.assertEqual(r2.status_code, 403)
        self.assertEqual(r2.data['code'], 'plan_event_limit')
        self.assertIn('Passez au plan Standard', r2.data['detail'])
        self.assertEqual(Event.objects.filter(organizer=self.user).count(), 1)

    def test_quota_expose_au_client(self):
        q = self.c.get('/api/events/quota/').data
        self.assertEqual((q['limit'], q['used'], q['remaining']), (1, 0, 1))
        overview = self.c.get('/api/subscriptions/').data
        self.assertEqual(overview['event_quota']['remaining'], 1)
        free = next(p for p in overview['plans'] if p['id'] == 'free')
        self.assertEqual(free['event_limit'], 1)
        self.assertIn('1 événement à organiser par mois', free['features'])

    def test_plans_payants_illimites(self):
        for plan in ('standard', 'pro'):
            self.set_plan(plan)
            for i in range(3):
                self.assertEqual(self.create(title=f'{plan} {i}').status_code, 201)
        self.assertIsNone(self.c.get('/api/events/quota/').data['limit'])

    def test_brouillon_supprime_rend_la_place(self):
        event_id = self.create().data['event']['id']
        self.assertEqual(self.c.delete(f'/api/events/{event_id}/delete/').status_code, 200)
        self.assertEqual(self.create(title='Nouvelle').status_code, 201)

    def test_publie_puis_supprime_reste_compte(self):
        event_id = self.create().data['event']['id']
        self.assertEqual(self.c.post(f'/api/events/{event_id}/publish/', {}, format='json').status_code, 200)
        self.assertIsNotNone(Event.objects.get(pk=event_id).published_at)
        self.c.delete(f'/api/events/{event_id}/delete/')
        self.assertEqual(self.create(title='Contournement').status_code, 403)

    def test_nouveau_mois_nouvelle_place(self):
        self.assertEqual(self.create().status_code, 201)
        next_month = timezone.now() + timedelta(days=32)
        with mock.patch('events.quota.timezone.now', return_value=next_month):
            self.assertEqual(self.create(title='Le mois suivant').status_code, 201)

    def test_brouillons_en_trop_apres_resiliation_non_publiables(self):
        self.set_plan('standard')
        first = self.create(title='A').data['event']['id']
        second = self.create(title='B').data['event']['id']
        self.set_plan('free')                                     # abonnement terminé
        self.assertEqual(self.c.post(f'/api/events/{first}/publish/', {}, format='json').status_code, 200)
        r = self.c.post(f'/api/events/{second}/publish/', {}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'plan_event_limit')
        self.assertEqual(Event.objects.get(pk=second).status, 'draft')
        self.set_plan('standard')                                 # repasse en payant : publiable
        self.assertEqual(self.c.post(f'/api/events/{second}/publish/', {}, format='json').status_code, 200)

    def test_republier_apres_depublication_ne_recompte_pas(self):
        event_id = self.create().data['event']['id']
        self.c.post(f'/api/events/{event_id}/publish/', {}, format='json')   # publier
        self.c.post(f'/api/events/{event_id}/publish/', {}, format='json')   # dépublier
        self.assertEqual(self.c.post(f'/api/events/{event_id}/publish/', {}, format='json').status_code, 200)

    def test_gratuit_limite_a_50_invites(self):
        event_id = self.create().data['event']['id']
        self.c.post(f'/api/events/{event_id}/publish/', {}, format='json')
        emails = [f'invite{i}@x.fr' for i in range(51)]
        with mock.patch('invitations.services.send_invitation_email', create=True):
            r = self.c.post(f'/api/events/{event_id}/invite/', {'emails': emails}, format='json')
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data.get('code'), 'plan_limit')
