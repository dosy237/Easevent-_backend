"""
tickets/tests.py — tickets (MVP §5) et paiements Stripe Connect.
Stripe est simulé (unittest.mock) : aucun appel réseau, aucune clé réelle.
"""
import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event
from invitations.models import Invitation
from users.models import User
from .models import Ticket


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')


@override_settings(STRIPE_SECRET_KEY='sk_test_dummy', STRIPE_WEBHOOK_SECRET='whsec_dummy',
                   PLATFORM_FEE_PERCENT=3, PUBLIC_BASE_URL='https://easevent.example.com')
class TicketFlowTest(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.orga = User.objects.create_user(email='orga@x.fr', password='x', first_name='Léa', last_name='O',
                                             stripe_account_id='acct_123', stripe_charges_enabled=True)
        self.guest = User.objects.create_user(email='guest@x.fr', password='x', first_name='Sarah', last_name='M')
        start = timezone.now() + timedelta(days=10)
        common = dict(organizer=self.orga, event_type='conference', start_date=start,
                      end_date=start + timedelta(hours=8), status='published', visibility='public')
        self.free = Event.objects.create(title='Atelier gratuit', dress_code='Chic', **common)
        self.paid = Event.objects.create(title='Summit', is_paid=True, price='25.00', max_guests=2, **common)
        auth(self.client, self.guest)

    # ── Gratuit ───────────────────────────────────────────────
    def test_participer_gratuit_genere_le_ticket(self):
        r = self.client.post(f'/api/events/{self.free.id}/tickets/')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['status'], 'generated')
        self.assertEqual(r.data['price'], '0.00')
        self.assertEqual(r.data['dress_code'], 'Chic')
        self.assertTrue(r.data['number'].startswith('EV-'))
        self.assertEqual(Ticket.read_qr_payload(r.data['qr_payload']), r.data['id'])

    def test_un_seul_ticket_actif(self):
        a = self.client.post(f'/api/events/{self.paid.id}/tickets/')
        b = self.client.post(f'/api/events/{self.paid.id}/tickets/')
        self.assertEqual(a.data['id'], b.data['id'])
        self.assertEqual(Ticket.objects.filter(event=self.paid).count(), 1)

    def test_qr_falsifie_refuse(self):
        self.assertIsNone(Ticket.read_qr_payload('faux.payload'))

    # ── Payant ────────────────────────────────────────────────
    def test_payant_reste_en_attente_et_checkout(self):
        r = self.client.post(f'/api/events/{self.paid.id}/tickets/')
        self.assertEqual(r.data['status'], 'pending')
        self.assertEqual(r.data['payment_status'], 'pending')
        self.assertIsNone(r.data['qr_payload'])
        self.assertEqual(self.client.post(f"/api/tickets/{r.data['id']}/validate/").status_code, 402)

        with mock.patch('stripe.checkout.Session.create',
                        return_value={'id': 'cs_1', 'url': 'https://checkout.stripe.com/c/cs_1'}) as create:
            c = self.client.post(f"/api/tickets/{r.data['id']}/checkout/")
        self.assertEqual(c.status_code, 200)
        self.assertEqual(c.data['checkout_url'], 'https://checkout.stripe.com/c/cs_1')
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['line_items'][0]['price_data']['unit_amount'], 2500)
        self.assertEqual(kwargs['payment_intent_data']['transfer_data']['destination'], 'acct_123')
        self.assertEqual(kwargs['payment_intent_data']['application_fee_amount'], 75)  # 3 %
        self.assertTrue(kwargs['success_url'].startswith('https://easevent.example.com/api/payments/return/'))

    def test_organisateur_sans_compte_stripe(self):
        self.orga.stripe_charges_enabled = False
        self.orga.save()
        r = self.client.post(f'/api/events/{self.paid.id}/tickets/')
        c = self.client.post(f"/api/tickets/{r.data['id']}/checkout/")
        self.assertEqual(c.status_code, 409)
        self.assertEqual(c.data['code'], 'organizer_not_ready')

    def _webhook(self, kind, obj):
        payload = {'type': kind, 'data': {'object': obj}}
        with mock.patch('stripe.Webhook.construct_event', return_value=payload):
            return self.client.post('/api/stripe/webhook/', data=json.dumps(payload),
                                    content_type='application/json', HTTP_STRIPE_SIGNATURE='t=1,v1=x')

    def test_webhook_paiement_reussi_genere(self):
        ticket = Ticket.objects.get(pk=self.client.post(f'/api/events/{self.paid.id}/tickets/').data['id'])
        r = self._webhook('checkout.session.completed',
                          {'id': 'cs_1', 'payment_status': 'paid', 'payment_intent': 'pi_1',
                           'metadata': {'ticket_id': str(ticket.id)}})
        self.assertEqual(r.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.payment_status), ('generated', 'paid'))
        self.assertEqual(ticket.stripe_payment_intent_id, 'pi_1')
        # Rejouer le webhook ne change rien (idempotent)
        self._webhook('checkout.session.completed', {'id': 'cs_1', 'payment_status': 'paid',
                                                     'metadata': {'ticket_id': str(ticket.id)}})
        self.assertEqual(Ticket.objects.filter(event=self.paid, status='generated').count(), 1)

    def test_webhook_sepa_puis_echec(self):
        ticket = Ticket.objects.get(pk=self.client.post(f'/api/events/{self.paid.id}/tickets/').data['id'])
        self._webhook('checkout.session.completed', {'id': 'cs', 'payment_status': 'unpaid',
                                                     'metadata': {'ticket_id': str(ticket.id)}})
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.payment_status), ('pending', 'processing'))
        self.assertEqual(self.client.post(f'/api/tickets/{ticket.id}/cancel/').status_code, 409)
        self._webhook('checkout.session.async_payment_failed', {'id': 'cs', 'metadata': {'ticket_id': str(ticket.id)}})
        ticket.refresh_from_db()
        self.assertEqual(ticket.payment_status, 'failed')

    def test_webhook_signature_invalide(self):
        r = self.client.post('/api/stripe/webhook/', data='{}', content_type='application/json',
                             HTTP_STRIPE_SIGNATURE='faux')
        self.assertEqual(r.status_code, 400)

    def test_webhook_compte_connect(self):
        self.orga.stripe_charges_enabled = False
        self.orga.save()
        self._webhook('account.updated', {'id': 'acct_123', 'charges_enabled': True, 'payouts_enabled': True})
        self.orga.refresh_from_db()
        self.assertTrue(self.orga.stripe_payouts_enabled)

    # ── Annulation, places, sécurité ─────────────────────────
    def test_annuler_archive(self):
        tid = self.client.post(f'/api/events/{self.paid.id}/tickets/').data['id']
        self.assertEqual(self.client.post(f'/api/tickets/{tid}/cancel/').data['status'], 'cancelled')
        archived = self.client.get('/api/tickets/mine/?status=archived').data['tickets']
        self.assertEqual([t['id'] for t in archived], [tid])

    def test_complet(self):
        self.free.max_guests = 1
        self.free.save()
        other = User.objects.create_user(email='o@x.fr', password='x', first_name='A', last_name='B')
        self.client.post(f'/api/events/{self.free.id}/tickets/')
        auth(self.client, other)
        r = self.client.post(f'/api/events/{self.free.id}/tickets/')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'sold_out')

    def test_ticket_d_un_autre_invisible(self):
        tid = self.client.post(f'/api/events/{self.free.id}/tickets/').data['id']
        intruder = User.objects.create_user(email='i@x.fr', password='x', first_name='I', last_name='X')
        auth(self.client, intruder)
        self.assertEqual(self.client.get(f'/api/tickets/{tid}/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/tickets/{tid}/cancel/').status_code, 404)

    def test_evenement_prive_sans_invitation(self):
        self.free.visibility = 'private'
        self.free.save()
        self.assertEqual(self.client.post(f'/api/events/{self.free.id}/tickets/').status_code, 403)

    def test_organisateur_ne_prend_pas_de_ticket(self):
        auth(self.client, self.orga)
        self.assertEqual(self.client.post(f'/api/events/{self.free.id}/tickets/').status_code, 400)

    # ── Invitations ──────────────────────────────────────────
    def test_accepter_invitation_cree_ticket_en_attente(self):
        inv = Invitation.objects.create(event=self.free, invited_user=self.guest, token='t' * 43,
                                        channel='platform_notification', expires_at=self.free.end_date + timedelta(days=7))
        r = self.client.post(f'/api/invitations/{inv.id}/repondre/', {'status': 'confirmed'}, format='json')
        self.assertEqual(r.status_code, 200)
        ticket = Ticket.objects.get(pk=r.data['ticket_id'])
        self.assertEqual(ticket.status, 'pending')
        counts = self.client.get('/api/tickets/counts/').data
        self.assertEqual(counts['pending'], 1)
        # Annuler le ticket remet l'invitation en « déclinée »
        self.client.post(f'/api/tickets/{ticket.id}/cancel/')
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'declined')

    def test_detail_evenement_indique_mon_ticket_et_places(self):
        self.client.post(f'/api/events/{self.paid.id}/tickets/')
        d = self.client.get(f'/api/events/publics/{self.paid.id}/').data
        self.assertEqual(d['my_ticket']['status'], 'pending')
        self.assertEqual(d['spots_left'], 2)
        self.assertEqual(d['organizer']['name'], 'Léa O')

    # ── Connect ───────────────────────────────────────────────
    def test_activation_paiements_organisateur(self):
        auth(self.client, self.guest)
        with mock.patch('stripe.Account.create', return_value={'id': 'acct_new'}) as create, \
             mock.patch('stripe.AccountLink.create', return_value={'url': 'https://connect.stripe.com/x'}):
            r = self.client.post('/api/payments/connect/onboard/')
        self.assertEqual(r.data['url'], 'https://connect.stripe.com/x')
        self.assertEqual(create.call_args.kwargs['type'], 'express')
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.stripe_account_id, 'acct_new')

    @override_settings(STRIPE_SECRET_KEY='')
    def test_sans_cle_stripe(self):
        r = self.client.post('/api/payments/connect/onboard/')
        self.assertEqual(r.status_code, 503)


@override_settings(STRIPE_SECRET_KEY='sk_test_dummy', STRIPE_WEBHOOK_SECRET='whsec_vrai_test')
class RealSignatureWebhookTest(TestCase):
    """Webhook signé comme le fait Stripe, SANS simulation de la librairie."""

    def setUp(self):
        orga = User.objects.create_user(email='o@x.fr', password='x', first_name='O', last_name='R',
                                        stripe_account_id='acct_9', stripe_charges_enabled=True)
        self.guest = User.objects.create_user(email='g@x.fr', password='x', first_name='G', last_name='S')
        start = timezone.now() + timedelta(days=5)
        event = Event.objects.create(organizer=orga, title='Gala', event_type='gala', start_date=start,
                                     end_date=start + timedelta(hours=4), status='published', visibility='public',
                                     is_paid=True, price='40.00')
        self.ticket = Ticket.objects.create(event=event, user=self.guest, price='40.00', payment_status='pending')

    def _post(self, body, secret='whsec_vrai_test'):
        import hashlib, hmac, time
        t = int(time.time())
        sig = hmac.new(secret.encode(), f'{t}.{body}'.encode(), hashlib.sha256).hexdigest()
        return APIClient().post('/api/stripe/webhook/', data=body, content_type='application/json',
                                HTTP_STRIPE_SIGNATURE=f't={t},v1={sig}')

    def test_paiement_confirme_genere_le_ticket(self):
        body = json.dumps({'id': 'evt_1', 'object': 'event', 'type': 'checkout.session.completed',
                           'data': {'object': {'id': 'cs_1', 'object': 'checkout.session', 'payment_status': 'paid',
                                               'payment_intent': 'pi_1', 'metadata': {'ticket_id': str(self.ticket.id)}}}})
        self.assertEqual(self._post(body).status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual((self.ticket.status, self.ticket.payment_status), ('generated', 'paid'))

    def test_mauvaise_signature_refusee(self):
        body = json.dumps({'type': 'checkout.session.completed', 'data': {'object': {}}})
        self.assertEqual(self._post(body, secret='whsec_pirate').status_code, 400)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'pending')


class TicketPdfTest(TestCase):

    def setUp(self):
        cache.clear()
        orga = User.objects.create_user(email='o@x.fr', password='x', first_name='Léa', last_name='O')
        self.guest = User.objects.create_user(email='g@x.fr', password='x', first_name='Sarah', last_name='Martin')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(organizer=orga, title='Mariage de Sarah & Karim', event_type='mariage',
                                          start_date=start, end_date=start + timedelta(hours=8), status='published',
                                          visibility='public', dress_code='Tenue de soirée',
                                          cover_image='/static/app/covers/photos/mariage-reception.jpg')
        self.client = APIClient()
        auth(self.client, self.guest)

    def test_telechargement_pdf(self):
        tid = self.client.post(f'/api/events/{self.event.id}/tickets/').data['id']
        link = self.client.post(f'/api/tickets/{tid}/pdf-link/')
        self.assertEqual(link.status_code, 200)
        path = link.data['url'].split('://', 1)[1].split('/', 1)[1]
        pdf = APIClient().get('/' + path)          # sans authentification : le lien signé suffit
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertIn('ticket-EV-', pdf['Content-Disposition'])

    def test_lien_falsifie_ou_ticket_en_attente(self):
        self.assertEqual(APIClient().get('/api/tickets/pdf/faux-jeton/').status_code, 404)
        self.event.is_paid, self.event.price = True, '10.00'
        self.event.save()
        tid = self.client.post(f'/api/events/{self.event.id}/tickets/').data['id']
        self.assertEqual(self.client.post(f'/api/tickets/{tid}/pdf-link/').status_code, 400)

    def test_commission_par_defaut_3_pourcent(self):
        from django.conf import settings
        from tickets.stripe_service import platform_fee
        self.assertEqual(settings.PLATFORM_FEE_PERCENT, 3)
        self.assertEqual(platform_fee(2500), 75)


class CheckInTest(TestCase):
    """Scanner de l'organisateur : QR signé ou numéro, une seule entrée par ticket."""

    def setUp(self):
        cache.clear()
        self.orga = User.objects.create_user(email='o@x.fr', password='x', first_name='Léa', last_name='O')
        self.guest = User.objects.create_user(email='g@x.fr', password='x', first_name='Sarah', last_name='M')
        start = timezone.now() + timedelta(hours=1)
        common = dict(organizer=self.orga, event_type='soiree', start_date=start, end_date=start + timedelta(hours=5),
                      status='published', visibility='public')
        self.event = Event.objects.create(title='Soirée', **common)
        self.other = Event.objects.create(title='Autre', **common)
        self.ticket = Ticket.objects.create(event=self.event, user=self.guest, status='generated', price='0')
        self.client = APIClient()
        auth(self.client, self.orga)
        self.url = f'/api/events/{self.event.id}/check-in/'

    def scan(self, code):
        return self.client.post(self.url, {'code': code}, format='json').data

    def test_entree_unique(self):
        r = self.scan(self.ticket.qr_payload)
        self.assertEqual((r['result'], r['participant']['name']), ('ok', 'Sarah M'))
        self.assertEqual(r['counts'], {'checked_in': 1, 'total': 1})
        self.assertEqual(self.scan(self.ticket.qr_payload)['result'], 'already')
        self.assertEqual(self.client.get(self.url).data['recent'][0]['number'], self.ticket.number)

    def test_numero_saisi_et_codes_refuses(self):
        self.assertEqual(self.scan(self.ticket.number.lower())['result'], 'ok')
        self.assertEqual(self.scan('faux.qr')['result'], 'invalid')
        self.assertEqual(self.scan('')['result'], 'invalid')
        other = Ticket.objects.create(event=self.other, user=self.guest, status='generated', price='0')
        self.assertEqual(self.scan(other.qr_payload)['result'], 'wrong_event')
        pending = Ticket.objects.create(event=self.other, user=self.orga, status='pending', price='5')
        self.assertEqual(self.client.post(f'/api/events/{self.other.id}/check-in/', {'code': pending.qr_payload},
                                          format='json').data['result'], 'not_valid')

    def test_seul_l_organisateur_scanne(self):
        auth(self.client, self.guest)
        self.assertEqual(self.client.post(self.url, {'code': self.ticket.qr_payload}, format='json').status_code, 404)
