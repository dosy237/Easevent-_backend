"""Taux de change (repli, franc CFA) et fuseau horaire de l'événement."""
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from events.models import Event
from events.tests_lifecycle import client_for
from invitations.services import fr_datetime
from users.models import User


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FxTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_taux_du_jour_et_franc_cfa(self):
        with mock.patch('events.fx.requests.get', return_value=FakeResp({'date': '2026-10-07', 'rates': {'USD': 1.1}})):
            data = client_for().get('/api/fx/rates/').data
        self.assertEqual(data['rates']['USD'], 1.1)
        self.assertEqual(data['rates']['XAF'], 655.957)
        self.assertEqual(data['source'], 'Banque centrale européenne')
        self.assertIn('XAF', [c['code'] for c in data['currencies']])

    def test_source_injoignable_derniers_taux_puis_repli(self):
        with mock.patch('events.fx.requests.get', side_effect=OSError):
            data = client_for().get('/api/fx/rates/').data
        self.assertEqual(data['source'], 'Taux indicatifs')
        self.assertEqual(data['rates']['XOF'], 655.957)
        self.assertIn('USD', data['rates'])


class TimezoneTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email='o@x.fr', password='x', first_name='O', last_name='R', is_verified=True,
                                             subscription_plan='pro')

    def test_creation_avec_le_fuseau_du_telephone(self):
        c = client_for(self.user)
        start = datetime(2026, 11, 14, 11, 0, tzinfo=dt_tz.utc)          # 12h à Douala (UTC+1)
        body = {'title': 'Soirée', 'event_type': 'soiree', 'visibility': 'public', 'location_address': 'Douala',
                'start_date': start.isoformat(), 'end_date': (start + timedelta(hours=4)).isoformat(),
                'timezone': 'Africa/Douala'}
        r = c.post('/api/events/create/', body, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['event']['timezone'], 'Africa/Douala')
        event = Event.objects.get(pk=r.data['event']['id'])
        self.assertIn('12h00 (heure de Douala)', fr_datetime(event.start_date, event))
        bad = c.post('/api/events/create/', {**body, 'timezone': 'Mars/Olympus'}, format='json')
        self.assertEqual(bad.status_code, 400)
