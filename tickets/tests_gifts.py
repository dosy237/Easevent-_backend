import json
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event
from messaging.models import Conversation, Message
from notifications.models import Notification
from users.models import User

from . import stripe_service
from .models import Ticket, TicketGift


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return c


@override_settings(STRIPE_SECRET_KEY='sk_test_x', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class GiftTest(TestCase):
    def setUp(self):
        cache.clear()
        mk = lambda e, f, **kw: User.objects.create_user(email=e, password='x', first_name=f, last_name='T', is_verified=True, **kw)
        self.orga = mk('orga@x.fr', 'Orga', stripe_account_id='acct_1', stripe_charges_enabled=True)
        self.buyer, self.mum, self.other = mk('aicha@x.fr', 'Aïcha'), mk('maman@x.fr', 'Maman'), mk('z@x.fr', 'Zoé')
        start = timezone.now() + timedelta(days=12)
        self.event = Event.objects.create(organizer=self.orga, title="Astronomie : la Lune et nous", event_type='conference',
                                          status='published', visibility='public', start_date=start,
                                          end_date=start + timedelta(hours=3), is_paid=True, price='15.00', max_guests=100)
        self.c = client_for(self.buyer)
        self.url = f'/api/events/{self.event.id}/gifts/'

    def pay(self, gift_id):
        with mock.patch('tickets.stripe_service.stripe.checkout.Session.create', return_value={'id': 'cs_1', 'url': 'https://checkout.stripe.com/x'}) as create:
            r = self.c.post(f'/api/gifts/{gift_id}/checkout/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(create.call_args.kwargs['metadata'], {'gift_id': gift_id})
        self.assertEqual(create.call_args.kwargs['payment_intent_data']['transfer_data']['destination'], 'acct_1')
        stripe_service.handle_event({'type': 'checkout.session.completed', 'data': {'object': {
            'mode': 'payment', 'payment_status': 'paid', 'payment_intent': 'pi_gift', 'metadata': {'gift_id': gift_id}}}})

    def test_offrir_a_un_membre(self):
        r = self.c.post(self.url, {'recipient': {'user_id': str(self.mum.id)}, 'message': 'Pour tes étoiles, maman.'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['status'], r.data['recipient']['name']), ('awaiting_payment', 'Maman T'))
        self.pay(r.data['id'])
        t = Ticket.objects.get(event=self.event, user=self.mum)
        self.assertEqual((t.status, t.payment_status, t.purchased_by, t.stripe_payment_intent_id),
                         ('generated', 'paid', self.buyer, 'pi_gift'))
        self.assertFalse(Ticket.objects.filter(user=self.buyer).exists())         # l'acheteur n'a pas de billet
        self.assertTrue(Notification.objects.filter(user=self.mum, type='ticket_gift').exists())
        self.assertTrue(Notification.objects.filter(user=self.buyer, type='ticket_gift').exists())
        msg = Message.objects.get(kind='event')
        self.assertEqual((msg.sender, msg.body, msg.meta['gift']), (self.buyer, 'Pour tes étoiles, maman.', True))
        # La conversation est ouverte aux deux (sans être amis)
        conv = msg.conversation
        r2 = client_for(self.mum).post(f'/api/conversations/{conv.id}/messages/', {'body': 'Merci ma fille !'}, format='json')
        self.assertEqual(r2.status_code, 201, getattr(r2, 'data', None))
        # Le billet de maman indique qui l'a offert
        mine = client_for(self.mum).get(f'/api/tickets/{t.id}/').data
        self.assertEqual(mine['offered_by']['first_name'], 'Aïcha')
        # Annulation de l'événement : le billet offert est remboursé à l'acheteur
        from events.lifecycle import cancel_event
        with mock.patch('tickets.stripe_service.stripe.Refund.create') as refund:
            cancel_event(self.event)
        self.assertEqual(refund.call_args.kwargs['payment_intent'], 'pi_gift')

    def test_offrir_a_une_personne_non_inscrite_puis_inscription(self):
        r = self.c.post(self.url, {'recipient': {'name': 'Mireille', 'email': 'Mireille@Exemple.fr'}}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(r.data['recipient']['is_member'])
        self.pay(r.data['id'])
        gift = TicketGift.objects.get(pk=r.data['id'])
        self.assertEqual(gift.status, 'paid')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Aïcha T vous offre un billet', mail.outbox[0].subject)
        self.assertIn('mireille@exemple.fr', mail.outbox[0].to)
        # Mireille s'inscrit et vérifie son email : le billet lui est remis
        mireille = User.objects.create_user(email='mireille@exemple.fr', password='x', first_name='Mireille', last_name='K', is_verified=True)
        from users.phone import claim_invitations
        claim_invitations(mireille, email=mireille.email)
        gift.refresh_from_db()
        self.assertEqual(gift.status, 'delivered')
        self.assertEqual(Ticket.objects.get(event=self.event, user=mireille).status, 'generated')

    def test_telephone_d_un_membre_verifie(self):
        from invitations.crypto import blind_index
        User.objects.filter(pk=self.mum.pk).update(phone_hash=blind_index('+237690000001'), phone_verified_at=timezone.now())
        r = self.c.post(self.url, {'recipient': {'name': 'Maman', 'phone': '+237 6 90 00 00 01'}}, format='json')
        self.assertTrue(r.data['recipient']['is_member'])

    def test_refus(self):
        bad = [({'recipient': {'user_id': str(self.buyer.id)}}, 'self_gift'),
               ({'recipient': {'user_id': str(self.orga.id)}}, 'recipient_is_organizer'),
               ({'recipient': {}}, 'recipient_required'),
               ({'recipient': {'email': 'x@y.fr'}}, 'name_required'),
               ({'recipient': {'name': 'A', 'email': 'pas-un-email'}}, 'invalid_email'),
               ({'recipient': {'name': 'A', 'phone': '12'}}, 'invalid_phone')]
        for body, code in bad:
            r = self.c.post(self.url, body, format='json')
            self.assertEqual(r.data.get('code'), code, (body, r.data))
        Ticket.objects.create(event=self.event, user=self.mum, status='generated', price='15')
        self.assertEqual(self.c.post(self.url, {'recipient': {'user_id': str(self.mum.id)}}, format='json').data['code'], 'already_has_ticket')
        self.event.visibility = 'private'; self.event.save()
        self.assertEqual(self.c.post(self.url, {'recipient': {'user_id': str(self.other.id)}}, format='json').status_code, 403)
        # Un autre ne voit ni ne paie le cadeau de quelqu'un
        self.event.visibility = 'public'; self.event.save()
        gid = self.c.post(self.url, {'recipient': {'user_id': str(self.other.id)}}, format='json').data['id']
        self.assertEqual(client_for(self.mum).get(f'/api/gifts/{gid}/').status_code, 404)
        self.assertEqual(client_for(self.mum).post(f'/api/gifts/{gid}/checkout/').status_code, 404)

    def test_gratuit_remis_aussitot_et_doublon_repris(self):
        self.event.is_paid, self.event.price = False, 0
        self.event.save()
        r = self.c.post(self.url, {'recipient': {'user_id': str(self.mum.id)}}, format='json')
        self.assertEqual(r.data['status'], 'delivered')
        self.assertEqual(Ticket.objects.get(user=self.mum, event=self.event).payment_status, 'not_required')
        self.event.is_paid, self.event.price = True, 10
        self.event.save()
        a = self.c.post(self.url, {'recipient': {'user_id': str(self.other.id)}}, format='json').data['id']
        b = self.c.post(self.url, {'recipient': {'user_id': str(self.other.id)}}, format='json').data['id']
        self.assertEqual(a, b)

    def test_proche_deja_inscrit_entre_temps_rembourse(self):
        r = self.c.post(self.url, {'recipient': {'user_id': str(self.mum.id)}}, format='json')
        Ticket.objects.create(event=self.event, user=self.mum, status='generated', price='15')
        with mock.patch('tickets.stripe_service.stripe.Refund.create') as refund:
            self.pay(r.data['id'])
        refund.assert_called_once()
        self.assertEqual(TicketGift.objects.get(pk=r.data['id']).status, 'cancelled')
