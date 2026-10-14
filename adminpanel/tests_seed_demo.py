"""Compte de démonstration : plan Pro, billets en attente à générer, commande relançable."""
from django.core.management import call_command
from django.test import TestCase

from tickets.models import Ticket
from tickets.services import validate_free_ticket
from users.models import User


class SeedDemoTest(TestCase):
    def test_demo_account_ready_and_rerunnable(self):
        call_command('seed_demo', password='Demo-Test-2026', stdout=open('/dev/null', 'w'))
        call_command('seed_demo', password='Demo-Test-2026', stdout=open('/dev/null', 'w'))   # remise à zéro
        demo = User.objects.get(email='demo@easevent.test')
        self.assertEqual(demo.subscription_plan, 'pro')
        self.assertTrue(demo.check_password('Demo-Test-2026'))
        self.assertIsNotNone(demo.phone_verified_at)
        self.assertEqual(demo.organized_events.count(), 4)
        pending = Ticket.objects.filter(user=demo, status='pending')
        self.assertEqual(pending.count(), 2)
        self.assertEqual(validate_free_ticket(pending.first()).status, 'generated')

    def test_delete(self):
        call_command('seed_demo', stdout=open('/dev/null', 'w'))
        call_command('seed_demo', delete=True, stdout=open('/dev/null', 'w'))
        self.assertFalse(User.objects.filter(email='demo@easevent.test').exists())
        self.assertFalse(User.objects.filter(email__endswith='@demo.easevent.test').exists())
