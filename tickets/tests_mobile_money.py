import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event
from notifications.models import Notification
from users.models import User

from . import mobile_money
from .models import Ticket


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return c


class Resp:
    def __init__(self, data, code=200):
        self.data, self.status_code = data, code

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise mobile_money.requests.HTTPError(str(self.status_code))


@override_settings(NOTCHPAY_PUBLIC_KEY='pk_test_x', NOTCHPAY_HASH_KEY='hash_test', NOTCHPAY_API='https://api.notchpay.co')
class MobileMoneyTest(TestCase):
    def setUp(self):
        cache.clear()
        orga = User.objects.create_user(email='o@x.fr', password='x', first_name='O', last_name='R')
        self.guest = User.objects.create_user(email='g@x.fr', password='x', first_name='Aïcha', last_name='N')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(organizer=orga, title='Concert Douala', event_type='concert', start_date=start,
                                          end_date=start + timedelta(hours=4), status='published', visibility='public',
                                          is_paid=True, price='12.00')
        self.ticket = Ticket.objects.create(event=self.event, user=self.guest, price='12.00', currency='EUR', payment_status='pending')
        self.c = client_for(self.guest)

    def checkout(self):
        with mock.patch('tickets.mobile_money.requests.post', return_value=Resp({
                'status': 'Accepted', 'authorization_url': 'https://pay.notchpay.co/abc', 'transaction': {'reference': 'x'}})) as post:
            r = self.c.post(f'/api/tickets/{self.ticket.id}/mobile-money/', {'phone': '+237 6 90 00 00 00'}, format='json')
        return r, post

    def webhook(self, reference, key='hash_test'):
        body = json.dumps({'event': 'payment.complete', 'data': {'reference': reference, 'status': 'complete'}}).encode()
        sig = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
        return APIClient().post('/api/payments/mobile-money/webhook/', data=body, content_type='application/json',
                                HTTP_X_NOTCH_SIGNATURE=sig)

    def test_conversion_fcfa(self):
        self.assertEqual(mobile_money.amount_xaf('12.00', 'EUR'), 7875)          # 7 871,48 → multiple de 5 supérieur
        self.assertEqual(mobile_money.amount_xaf('5000', 'XAF'), 5000)
        self.assertIsNone(mobile_money.amount_xaf('10', 'USD'))

    def test_moyens_de_paiement(self):
        self.assertTrue(self.c.get('/api/payments/methods/').data['mobile_money'])
        with self.settings(NOTCHPAY_PUBLIC_KEY=''):
            self.assertFalse(self.c.get('/api/payments/methods/').data['mobile_money'])
            self.assertEqual(self.c.post(f'/api/tickets/{self.ticket.id}/mobile-money/', {}, format='json').status_code, 503)

    def test_paiement_confirme_par_webhook_puis_verifie(self):
        r, post = self.checkout()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data['amount'], r.data['currency'], r.data['url']), (7875, 'XAF', 'https://pay.notchpay.co/abc'))
        sent = post.call_args.kwargs['json']
        self.assertEqual(sent['amount'], 7875)
        self.assertEqual(sent['customer']['phone'], '+237690000000')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'pk_test_x')
        ref = r.data['reference']
        # Signature fausse : rejeté, rien ne change
        self.assertEqual(self.webhook(ref, key='autre').status_code, 400)
        # Webhook valide : l'état est redemandé à Notch Pay avant de générer le billet
        tx = {'transaction': {'reference': ref, 'status': 'complete', 'amount': 7875, 'currency': 'XAF'}}
        with mock.patch('tickets.mobile_money.requests.get', return_value=Resp(tx)) as get:
            self.assertEqual(self.webhook(ref).status_code, 200)
            self.assertEqual(self.webhook(ref).status_code, 200)                 # rejoué : idempotent
        self.assertIn(f'/payments/{ref}', get.call_args.args[0])
        self.ticket.refresh_from_db()
        self.assertEqual((self.ticket.status, self.ticket.payment_status), ('generated', 'paid'))
        self.assertEqual(Notification.objects.filter(user=self.guest, type='payment_succeeded').count(), 1)

    def test_montant_insuffisant_refuse(self):
        r, _ = self.checkout()
        tx = {'transaction': {'reference': r.data['reference'], 'status': 'complete', 'amount': 100, 'currency': 'XAF'}}
        with mock.patch('tickets.mobile_money.requests.get', return_value=Resp(tx)):
            self.webhook(r.data['reference'])
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'pending')

    def test_echec_et_page_de_retour(self):
        r, _ = self.checkout()
        tx = {'transaction': {'reference': r.data['reference'], 'status': 'failed', 'amount': 7875, 'currency': 'XAF'}}
        with mock.patch('tickets.mobile_money.requests.get', return_value=Resp(tx)):
            page = APIClient().get(f"/api/payments/mobile-money/return/?reference={r.data['reference']}")
        self.assertContains(page, 'Paiement non abouti')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.payment_status, 'failed')
        self.assertTrue(Notification.objects.filter(user=self.guest, type='payment_failed').exists())

    def test_billet_d_un_autre_et_devise_non_convertible(self):
        other = User.objects.create_user(email='z@x.fr', password='x', first_name='Z', last_name='Z')
        self.assertEqual(client_for(other).post(f'/api/tickets/{self.ticket.id}/mobile-money/', {}, format='json').status_code, 404)
        Ticket.objects.filter(pk=self.ticket.pk).update(currency='USD')
        self.assertEqual(self.c.post(f'/api/tickets/{self.ticket.id}/mobile-money/', {}, format='json').status_code, 400)

    def test_remboursement_signale_si_annulation(self):
        r, _ = self.checkout()
        tx = {'transaction': {'reference': r.data['reference'], 'status': 'complete', 'amount': 7875, 'currency': 'XAF'}}
        with mock.patch('tickets.mobile_money.requests.get', return_value=Resp(tx)):
            self.webhook(r.data['reference'])
        from events.lifecycle import cancel_event
        self.assertEqual(cancel_event(self.event)['refunds_failed'], 1)       # remboursement Notch Pay à faire à la main
