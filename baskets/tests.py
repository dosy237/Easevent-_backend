"""Panier : lancement, objets, argent (carte, Mobile Money), bilan, annonce, remboursement."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event, EventCollaborator
from events.tests_lifecycle import client_for
from invitations.models import Invitation
from messaging.models import Message
from notifications.models import Notification
from tickets.models import Ticket
from users.models import User

from .models import Basket, Contribution


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.headers = data, status, {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@override_settings(NOTCHPAY_API='https://notch.test', STRIPE_SECRET_KEY='sk_test_x', STRIPE_WEBHOOK_SECRET='whsec_x')
class BasketTest(TestCase):

    def setUp(self):
        cache.clear()
        mk = lambda e, f, **kw: User.objects.create_user(email=e, password='x', first_name=f, last_name='T',
                                                         is_verified=True, **kw)
        self.orga = mk('o@x.fr', 'Aline', stripe_account_id='acct_1', stripe_charges_enabled=True)
        self.lea, self.paul, self.zoe, self.co = mk('lea@x.fr', 'Léa'), mk('paul@x.fr', 'Paul'), mk('zoe@x.fr', 'Zoé'), mk('co@x.fr', 'Coco')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(organizer=self.orga, title='Anniversaire de Noé', event_type='anniversaire',
                                          visibility='private', status='published', start_date=start,
                                          end_date=start + timedelta(hours=4), currency='EUR')
        for u in (self.lea, self.paul):
            Invitation.objects.create(event=self.event, invited_user=u, token=f't-{u.first_name}', status='confirmed',
                                      expires_at=start + timedelta(days=7))
        EventCollaborator.objects.create(event=self.event, user=self.co, role='cohost', status='accepted')
        self.url = f'/api/events/{self.event.id}/basket/'

    def launch(self, by=None, **data):
        body = {'title': 'Cadeau pour Noé', 'description': 'Un vélo !', 'goal_amount': '150', **data}
        r = client_for(by or self.orga).post(self.url, body, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        return Basket.objects.get(pk=r.data['basket']['id'])

    def test_lancement_annonce_aux_invites(self):
        b = self.launch(by=self.co)                         # un co-organisateur peut lancer
        self.assertEqual((b.currency, b.goal_amount), ('EUR', Decimal('150.00')))
        msgs = Message.objects.filter(meta__basket=str(b.id))
        self.assertEqual({m.conversation.participant_id for m in msgs}, {self.lea.id, self.paul.id})
        self.assertIn('Cadeau pour Noé', msgs.first().body)
        # Un seul panier ouvert
        r = client_for(self.orga).post(self.url, {'title': 'Autre'}, format='json')
        self.assertEqual(r.status_code, 409)
        # Un invité ne lance pas de panier ; un inconnu ne le voit pas
        self.assertEqual(client_for(self.lea).get(self.url).data['can_launch'], False)
        self.assertEqual(client_for(self.zoe).get(self.url).status_code, 404)
        b.status = 'closed'
        b.save()
        self.assertEqual(client_for(self.lea).post(self.url, {'title': 'X'}, format='json').status_code, 403)

    def test_objets_et_bilan(self):
        b = self.launch()
        lea = client_for(self.lea)
        r = lea.post(f'/api/baskets/{b.id}/items/', {'label': 'Bouteilles de jus', 'quantity': 3}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        client_for(self.paul).post(f'/api/baskets/{b.id}/items/', {'label': 'Gâteau au chocolat'}, format='json')
        s = client_for(self.orga).get(self.url).data['basket']
        self.assertEqual((s['items_count'], s['contributors']), (4, 2))
        self.assertEqual({c['label'] for c in s['contributions']}, {'Bouteilles de jus', 'Gâteau au chocolat'})
        n = Notification.objects.get(user=self.orga, type='basket_contribution')
        self.assertIn('Gâteau au chocolat', n.body)           # une seule notification, mise à jour
        # Retirer : le sien seulement (ou un organisateur)
        mine = next(c for c in s['contributions'] if c['label'] == 'Bouteilles de jus')
        self.assertEqual(client_for(self.paul).delete(f"/api/baskets/contributions/{mine['id']}/").status_code, 404)
        self.assertEqual(lea.delete(f"/api/baskets/contributions/{mine['id']}/").status_code, 204)
        # Validation
        self.assertEqual(lea.post(f'/api/baskets/{b.id}/items/', {'label': ''}, format='json').status_code, 400)
        self.assertEqual(lea.post(f'/api/baskets/{b.id}/items/', {'label': 'x', 'quantity': 0}, format='json').status_code, 400)
        self.assertEqual(client_for(self.zoe).post(f'/api/baskets/{b.id}/items/', {'label': 'x'}, format='json').status_code, 404)
        # Fermé : plus d'ajout
        client_for(self.orga).post(f'/api/baskets/{b.id}/close/')
        r = lea.post(f'/api/baskets/{b.id}/items/', {'label': 'Ballons'}, format='json')
        self.assertEqual(r.data['code'], 'closed')

    def test_argent_par_carte_sans_commission(self):
        b = self.launch()
        lea = client_for(self.lea)
        r = lea.post(f'/api/baskets/{b.id}/money/', {'amount': '20', 'anonymous': True}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        cid = r.data['id']
        with mock.patch('stripe.checkout.Session.create', return_value={'id': 'cs_1', 'url': 'https://pay.test/cs_1'}) as create:
            r = lea.post(f'/api/baskets/contributions/{cid}/checkout/')
        self.assertEqual(r.data['checkout_url'], 'https://pay.test/cs_1')
        kw = create.call_args.kwargs
        self.assertNotIn('application_fee_amount', kw['payment_intent_data'])        # aucune commission
        self.assertEqual(kw['payment_intent_data']['transfer_data'], {'destination': 'acct_1'})
        self.assertEqual(kw['line_items'][0]['price_data']['unit_amount'], 2000)
        # Pas encore payé : absent du bilan
        self.assertEqual(client_for(self.orga).get(self.url).data['basket']['total_money'], 0)
        # Webhook Stripe
        from tickets.stripe_service import handle_event
        handle_event({'type': 'checkout.session.completed', 'data': {'object': {
            'id': 'cs_1', 'mode': 'payment', 'payment_status': 'paid', 'payment_intent': 'pi_1',
            'metadata': {'basket_contribution_id': cid}}}})
        handle_event({'type': 'checkout.session.completed', 'data': {'object': {          # deux fois : idempotent
            'id': 'cs_1', 'mode': 'payment', 'payment_status': 'paid', 'payment_intent': 'pi_1',
            'metadata': {'basket_contribution_id': cid}}}})
        c = Contribution.objects.get(pk=cid)
        self.assertEqual((c.status, c.stripe_payment_intent_id), ('paid', 'pi_1'))
        s_orga = client_for(self.orga).get(self.url).data['basket']
        self.assertEqual((s_orga['total_money'], s_orga['progress'], s_orga['total_money_text']), (20.0, 13, '20,00 €'))
        self.assertEqual(s_orga['contributions'][0]['amount'], 20.0)                  # l'organisateur voit
        s_paul = client_for(self.paul).get(self.url).data['basket']
        self.assertEqual(s_paul['contributions'][0]['amount_text'], 'Montant masqué')   # anonyme pour les autres
        self.assertEqual(Notification.objects.filter(user=self.lea, type='payment_succeeded').count(), 1)
        # Une participation payée ne se retire pas
        self.assertEqual(lea.delete(f'/api/baskets/contributions/{cid}/').status_code, 400)
        # Montants hors limites
        self.assertEqual(lea.post(f'/api/baskets/{b.id}/money/', {'amount': '0'}, format='json').status_code, 400)
        self.assertEqual(lea.post(f'/api/baskets/{b.id}/money/', {'amount': 'abc'}, format='json').status_code, 400)
        self.assertEqual(lea.post(f'/api/baskets/{b.id}/money/', {'amount': '20000'}, format='json').status_code, 400)

    def test_carte_impossible_sans_compte_organisateur(self):
        User.objects.filter(pk=self.orga.pk).update(stripe_charges_enabled=False)
        b = self.launch()
        cid = client_for(self.lea).post(f'/api/baskets/{b.id}/money/', {'amount': '10'}, format='json').data['id']
        r = client_for(self.lea).post(f'/api/baskets/contributions/{cid}/checkout/')
        self.assertEqual(r.data['code'], 'organizer_not_ready')
        # Seul l'auteur paie sa participation
        self.assertEqual(client_for(self.paul).post(f'/api/baskets/contributions/{cid}/checkout/').status_code, 404)

    @mock.patch('tickets.mobile_money.available', return_value=True)
    def test_mobile_money(self, _):
        b = self.launch()
        lea = client_for(self.lea)
        cid = lea.post(f'/api/baskets/{b.id}/money/', {'amount': '10'}, format='json').data['id']
        with mock.patch('requests.post', return_value=FakeResponse({'authorization_url': 'https://pay.notchpay.co/x'})) as post:
            r = lea.post(f'/api/baskets/contributions/{cid}/mobile-money/', {'phone': '+237690000000'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['reference'].startswith('bk-'))
        self.assertEqual(post.call_args.kwargs['json']['amount'], 6560)              # 10 € → 6 560 FCFA
        ref = r.data['reference']
        from tickets import mobile_money
        with mock.patch('requests.get', return_value=FakeResponse({'transaction': {'status': 'complete', 'amount': 6560, 'currency': 'XAF'}})):
            mobile_money.sync(ref)
        self.assertEqual(Contribution.objects.get(pk=cid).status, 'paid')
        # Montant trop faible chez Notch Pay : refusé
        cid2 = lea.post(f'/api/baskets/{b.id}/money/', {'amount': '10'}, format='json').data['id']
        with mock.patch('requests.post', return_value=FakeResponse({'authorization_url': 'https://pay.notchpay.co/y'})):
            ref2 = lea.post(f'/api/baskets/contributions/{cid2}/mobile-money/').data['reference']
        with mock.patch('requests.get', return_value=FakeResponse({'transaction': {'status': 'complete', 'amount': 100, 'currency': 'XAF'}})):
            mobile_money.sync(ref2)
        self.assertEqual(Contribution.objects.get(pk=cid2).status, 'awaiting_payment')

    def test_nouvel_invite_prevenu_du_panier(self):
        b = self.launch()
        client_for(self.lea).post(f'/api/baskets/{b.id}/items/', {'label': 'Ballons'}, format='json')
        inv = Invitation.objects.create(event=self.event, invited_user=self.zoe, token='t-zoe', status='sent',
                                        expires_at=self.event.end_date + timedelta(days=7))
        with self.captureOnCommitCallbacks(execute=True):
            r = client_for(self.zoe).post(f'/api/invitations/{inv.id}/repondre/', {'status': 'confirmed'}, format='json')
        self.assertIn(r.status_code, (200, 201), r.data)
        n = Notification.objects.filter(user=self.zoe, type='basket_open')
        self.assertEqual(n.count(), 1)
        self.assertIn('1 contribution déjà', n.first().body)

    def test_annulation_rembourse(self):
        b = self.launch()
        c = Contribution.objects.create(basket=b, user=self.lea, kind='money', amount=Decimal('15'), currency='EUR',
                                        status='paid', stripe_payment_intent_id='pi_9')
        m = Contribution.objects.create(basket=b, user=self.paul, kind='money', amount=Decimal('10'), currency='EUR',
                                        status='paid', mobile_money_reference='bk-1', mobile_money_amount=6560)
        with mock.patch('stripe.Refund.create') as refund:
            r = client_for(self.orga).delete(f'/api/events/{self.event.id}/delete/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(refund.call_args.kwargs['payment_intent'], 'pi_9')
        self.assertNotIn('refund_application_fee', refund.call_args.kwargs)
        self.assertEqual(Contribution.objects.get(pk=c.pk).status, 'refunded')
        self.assertEqual(Contribution.objects.get(pk=m.pk).status, 'refund_needed')   # Mobile Money : à la main
        self.assertEqual(Basket.objects.get(pk=b.pk).status, 'closed')
