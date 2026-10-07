"""
events/tests_lifecycle.py — logique de bout en bout côté serveur :
visibilité (public / privé / brouillon / supprimé), annulation et
remboursements, révocation, modifications prévenues, notifications
groupées, sécurité de la messagerie, entrées invalides (pas d'erreur 500),
notifications push, abonnements.
Stripe et le service Expo Push sont simulés (aucun appel réseau).
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
from notifications.models import DeviceToken, Notification
from tickets.models import Ticket
from users.models import User


def client_for(user=None):
    c = APIClient()
    if user is not None:
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
    return c


def feed_ids(c):
    return {e['id'] for e in c.get('/api/events/publics/').data['events']}


@override_settings(STRIPE_SECRET_KEY='sk_test_dummy', STRIPE_WEBHOOK_SECRET='whsec_dummy',
                   PUBLIC_BASE_URL='https://easevent.example.com')
class LifecycleTest(TestCase):

    def setUp(self):
        cache.clear()
        mk = lambda e, f, **kw: User.objects.create_user(email=e, password='x', first_name=f, last_name='T',
                                                         is_verified=True, **kw)
        self.orga = mk('lea@x.fr', 'Léa', stripe_account_id='acct_1', stripe_charges_enabled=True)
        self.claire, self.paul, self.zoe = mk('claire@x.fr', 'Claire'), mk('paul@x.fr', 'Paul'), mk('zoe@x.fr', 'Zoé')
        self.start = timezone.now() + timedelta(days=10)
        self.common = dict(organizer=self.orga, event_type='soiree', start_date=self.start,
                           end_date=self.start + timedelta(hours=6), status='published')
        self.public = Event.objects.create(title='Concert', visibility='public', **self.common)
        self.private = Event.objects.create(title='Mariage', visibility='private', **self.common)
        self.orga_c = client_for(self.orga)

    def invite(self, event, user, status='sent'):
        return Invitation.objects.create(event=event, invited_user=user, token=f'{user.id.hex}{event.id.hex}'[:64],
                                         status=status, channel='platform_notification',
                                         expires_at=self.start + timedelta(days=8), sent_at=timezone.now())

    # ── Visibilité ────────────────────────────────────────────
    def test_fil_public_ne_montre_que_le_public_publie(self):
        draft = Event.objects.create(title='Brouillon', visibility='public', **{**self.common, 'status': 'draft'})
        anon = client_for()
        ids = feed_ids(anon)
        self.assertIn(str(self.public.id), ids)
        self.assertNotIn(str(self.private.id), ids)
        self.assertNotIn(str(draft.id), ids)
        # Privé : détail refusé à un inconnu, permis à l'invité
        self.assertEqual(client_for(self.zoe).get(f'/api/events/publics/{self.private.id}/').status_code, 403)
        self.invite(self.private, self.claire)
        self.assertEqual(client_for(self.claire).get(f'/api/events/publics/{self.private.id}/').status_code, 200)
        # Public → privé : disparaît tout de suite du fil et du détail pour les autres
        self.orga_c.patch(f'/api/events/{self.public.id}/update/', {'visibility': 'private'}, format='json')
        self.assertNotIn(str(self.public.id), feed_ids(anon))
        self.assertEqual(client_for(self.zoe).get(f'/api/events/publics/{self.public.id}/').status_code, 403)
        # Supprimé : introuvable
        self.orga_c.delete(f'/api/events/{self.private.id}/delete/')
        self.assertEqual(client_for(self.claire).get(f'/api/events/publics/{self.private.id}/').status_code, 404)

    def test_lien_en_ligne_reserve_aux_participants(self):
        self.public.is_online, self.public.online_link = True, 'https://meet.example.com/abc'
        self.public.save()
        detail = lambda c: c.get(f'/api/events/publics/{self.public.id}/').data['online_link']
        self.assertIsNone(detail(client_for()))
        self.assertIsNone(detail(client_for(self.paul)))
        Ticket.objects.create(event=self.public, user=self.paul, status='generated', price='0')
        self.assertEqual(detail(client_for(self.paul)), 'https://meet.example.com/abc')
        self.assertTrue(all(e['online_link'] is None for e in client_for().get('/api/events/publics/').data['events']))

    # ── Annulation ────────────────────────────────────────────
    def test_suppression_annule_rembourse_et_previent(self):
        inv = self.invite(self.private, self.claire, status='confirmed')
        paid = Ticket.objects.create(event=self.private, user=self.claire, status='generated', price='25.00',
                                     payment_status='paid', stripe_payment_intent_id='pi_1', invitation=inv)
        pending = Ticket.objects.create(event=self.public, user=self.paul, status='pending', price='0')
        with mock.patch('stripe.Refund.create') as refund:
            r = self.orga_c.delete(f'/api/events/{self.private.id}/delete/')
        self.assertEqual(r.status_code, 200)
        refund.assert_called_once()
        self.assertTrue(refund.call_args.kwargs['reverse_transfer'])
        paid.refresh_from_db()
        self.assertEqual((paid.status, paid.payment_status), ('cancelled', 'refunded'))
        self.assertTrue(Notification.objects.filter(user=self.claire, type='event_cancelled').exists())
        self.assertTrue(Notification.objects.filter(user=self.claire, type='payment_refunded').exists())
        self.assertEqual(client_for(self.claire).get('/api/invitations/mine/').data['count'], 0)
        # L'autre événement n'est pas touché
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'pending')

    def test_ticket_en_attente_bloque_apres_annulation_ou_revocation(self):
        inv = self.invite(self.private, self.claire, status='confirmed')
        t = Ticket.objects.create(event=self.private, user=self.claire, status='pending', price='0', invitation=inv)
        c = client_for(self.claire)
        r = self.orga_c.delete(f'/api/invitations/{inv.id}/revoke/')
        self.assertEqual(r.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, 'cancelled')
        self.assertTrue(Notification.objects.filter(user=self.claire, type='invitation_revoked').exists())
        self.assertEqual(c.post(f'/api/tickets/{t.id}/validate/').status_code, 400)       # plus en attente
        # Ticket en attente sur un événement ensuite supprimé : impossible de le valider ni de le payer
        t2 = Ticket.objects.create(event=self.public, user=self.paul, status='pending', price='0')
        Event.objects.filter(pk=self.public.pk).update(deleted_at=timezone.now())
        r = client_for(self.paul).post(f'/api/tickets/{t2.id}/validate/')
        self.assertEqual((r.status_code, r.data['code']), (410, 'event_cancelled'))

    def test_paiement_arrive_apres_annulation_rembourse(self):
        t = Ticket.objects.create(event=self.public, user=self.paul, status='cancelled', price='25.00',
                                  payment_status='pending')
        payload = {'type': 'checkout.session.completed', 'data': {'object': {
            'id': 'cs', 'payment_status': 'paid', 'payment_intent': 'pi_9', 'metadata': {'ticket_id': str(t.id)}}}}
        with mock.patch('stripe.Webhook.construct_event', return_value=payload), \
             mock.patch('stripe.Refund.create') as refund:
            r = client_for().post('/api/stripe/webhook/', data=json.dumps(payload), content_type='application/json',
                                  HTTP_STRIPE_SIGNATURE='t=1,v1=x')
        self.assertEqual(r.status_code, 200)
        refund.assert_called_once()
        t.refresh_from_db()
        self.assertEqual((t.status, t.payment_status), ('cancelled', 'refunded'))

    def test_webhook_identifiant_invalide_sans_erreur(self):
        payload = {'type': 'checkout.session.completed', 'data': {'object': {
            'id': 'cs', 'payment_status': 'paid', 'metadata': {'ticket_id': 'pas-un-uuid'}}}}
        with mock.patch('stripe.Webhook.construct_event', return_value=payload):
            r = client_for().post('/api/stripe/webhook/', data=json.dumps(payload), content_type='application/json',
                                  HTTP_STRIPE_SIGNATURE='t=1,v1=x')
        self.assertEqual(r.status_code, 200)

    def test_depublier_et_inviter(self):
        draft = Event.objects.create(title='Brouillon', visibility='private', **{**self.common, 'status': 'draft'})
        r = self.orga_c.post(f'/api/events/{draft.id}/invite/', {'user_ids': [str(self.claire.id)]}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (409, 'event_not_published'))
        # Dépublier : refusé si des participants ont leur ticket
        Ticket.objects.create(event=self.public, user=self.paul, status='generated', price='0')
        r = self.orga_c.post(f'/api/events/{self.public.id}/publish/')
        self.assertEqual((r.status_code, r.data['code']), (409, 'has_participants'))
        # Sans ticket : dépublié, l'invitation n'apparaît plus chez l'invitée
        self.invite(self.private, self.claire)
        c = client_for(self.claire)
        self.assertEqual(c.get('/api/invitations/mine/').data['count'], 1)
        self.assertEqual(self.orga_c.post(f'/api/events/{self.private.id}/publish/').data['status'], 'draft')
        self.assertEqual(c.get('/api/invitations/mine/').data['count'], 0)
        self.assertEqual(c.get('/api/tickets/counts/').data['invitations_to_answer'], 0)

    # ── Modifications prévenues ───────────────────────────────
    def test_changement_de_date_previent_les_participants(self):
        Ticket.objects.create(event=self.public, user=self.paul, status='generated', price='0')
        self.invite(self.public, self.claire)
        new_start = (self.start + timedelta(days=1)).isoformat()
        new_end = (self.start + timedelta(days=1, hours=5)).isoformat()
        self.orga_c.patch(f'/api/events/{self.public.id}/update/', {'start_date': new_start, 'end_date': new_end},
                          format='json')
        for user in (self.paul, self.claire):
            n = Notification.objects.get(user=user, type='event_updated')
            self.assertIn('nouvelle date', n.body)
        # Simple changement de description : personne n'est dérangé
        self.orga_c.patch(f'/api/events/{self.public.id}/update/', {'description': 'Nouveau texte'}, format='json')
        self.assertEqual(Notification.objects.filter(type='event_updated').count(), 2)

    # ── Réponses des invités (organisateur) ───────────────────
    def test_reponses_groupees_pour_l_organisateur(self):
        for user in (self.claire, self.paul):
            inv = self.invite(self.private, user)
            client_for(user).post(f'/api/invitations/{inv.id}/repondre/', {'status': 'confirmed'}, format='json')
        inv = self.invite(self.private, self.zoe)
        client_for(self.zoe).post(f'/api/invitations/{inv.id}/repondre/', {'status': 'declined'}, format='json')
        notes = Notification.objects.filter(user=self.orga, type='guest_response')
        self.assertEqual(notes.count(), 1)
        n = notes.get()
        self.assertEqual(n.data['counts'], {'accepted': 2, 'declined': 1})
        self.assertIn('2 autres réponses', n.body)

    def test_participation_publique_previent_l_organisateur(self):
        with self.captureOnCommitCallbacks(execute=True):
            client_for(self.paul).post(f'/api/events/{self.public.id}/tickets/')
        n = Notification.objects.get(user=self.orga, type='guest_response')
        self.assertIn('participe', n.body)

    # ── Messagerie ────────────────────────────────────────────
    def test_organisateur_n_ecrit_qu_a_ses_invites(self):
        r = self.orga_c.post('/api/conversations/', {'event_id': str(self.public.id), 'participant_id': str(self.zoe.id)},
                             format='json')
        self.assertEqual(r.status_code, 403)
        self.invite(self.public, self.zoe)
        r = self.orga_c.post('/api/conversations/', {'event_id': str(self.public.id), 'participant_id': str(self.zoe.id)},
                             format='json')
        self.assertEqual(r.status_code, 201)

    # ── Entrées invalides : jamais d'erreur 500 ───────────────
    def test_entrees_invalides(self):
        base = {'title': 'Test', 'event_type': 'soiree', 'start_date': '2026-11-20T19:00:00',
                'end_date': '2026-11-20T23:00:00'}
        cases = [
            {'start_date': '2026-02-30T10:00:00'}, {'start_date': 12}, {'end_date': '2026-11-20T18:00:00'},
            {'is_online': 'peut-être'}, {'latitude': 999}, {'longitude': 'abc'},
            {'online_link': 'javascript:alert(1)'}, {'title': 123}, {'title': '   '}, {'description': None, 'title': None},
            {'template_config': 'x'}, {'location_address': 'x' * 600},
        ]
        for extra in cases:
            r = self.orga_c.post('/api/events/create/', {**base, **extra}, format='json')
            self.assertEqual(r.status_code, 400, (extra, r.data))
        r = self.orga_c.post('/api/events/create/', base, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        eid = r.data['event']['id']
        for extra in ({'start_date': 'demain'}, {'is_online': 'x'}, {'title': None}, {'end_date': '2020-01-01T00:00:00'}):
            self.assertEqual(self.orga_c.patch(f'/api/events/{eid}/update/', extra, format='json').status_code, 400, extra)
        self.assertEqual(self.orga_c.post('/api/events/create/', [1, 2], format='json').status_code, 400)
        self.assertEqual(self.orga_c.get('/api/conversations/?event=pas-un-uuid').status_code, 200)
        self.assertEqual(self.orga_c.post('/api/conversations/', {'event_id': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.orga_c.post('/api/friends/requests/', {'user_id': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.orga_c.get('/api/notifications/?before=2026-13-45T99:00:00').status_code, 200)


# ─────────────────────────────────────────────────────────────
# Notifications push
# ─────────────────────────────────────────────────────────────
@override_settings(PUSH_ENABLED=True)
class PushTest(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email='p@x.fr', password='x', first_name='Paul', last_name='T', is_verified=True)
        self.c = client_for(self.user)
        self.token = 'ExponentPushToken[abcdefghijklmnop]'

    def _ok(self, n=1):
        res = mock.Mock(status_code=200)
        res.json.return_value = {'data': [{'status': 'ok'}] * n}
        res.raise_for_status.return_value = None
        return res

    def test_enregistrement_et_envoi(self):
        self.assertEqual(self.c.post('/api/notifications/devices/', {'token': 'nimporte'}, format='json').status_code, 400)
        r = self.c.post('/api/notifications/devices/', {'token': self.token, 'platform': 'android'}, format='json')
        self.assertEqual(r.status_code, 201)
        from notifications.services import notify
        with mock.patch('notifications.push.requests.post', return_value=self._ok()) as post:
            notify(self.user, 'friend_request', 'Claire', "vous a envoyé une demande d'ami", data={'friendship_id': 'f1'})
        post.assert_called_once()
        msg = post.call_args.kwargs['json'][0]
        self.assertEqual((msg['to'], msg['title'], msg['data']['type']), (self.token, 'Claire', 'friend_request'))
        self.assertEqual(msg['data']['friendship_id'], 'f1')
        self.assertEqual(msg['badge'], 1)

    def test_preferences_et_appareil_desinstalle(self):
        DeviceToken.objects.create(user=self.user, token=self.token)
        from notifications.services import notify
        self.c.patch('/api/notifications/preferences/', {'push': False}, format='json')
        self.user.refresh_from_db()
        with mock.patch('notifications.push.requests.post') as post:
            notify(self.user, 'reminder', 'Demain', 'Concert à 19:00')
        post.assert_not_called()
        self.c.patch('/api/notifications/preferences/', {'push': True}, format='json')
        self.user.refresh_from_db()
        res = self._ok()
        res.json.return_value = {'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]}
        with mock.patch('notifications.push.requests.post', return_value=res):
            notify(self.user, 'reminder', 'Demain', 'Concert à 20:00')
        self.assertFalse(DeviceToken.objects.exists())

    def test_deconnexion_retire_l_appareil(self):
        self.c.post('/api/notifications/devices/', {'token': self.token}, format='json')
        self.c.delete('/api/notifications/devices/', {'token': self.token}, format='json')
        self.assertFalse(DeviceToken.objects.exists())


# ─────────────────────────────────────────────────────────────
# Abonnements (Stripe simulé)
# ─────────────────────────────────────────────────────────────
@override_settings(STRIPE_SECRET_KEY='sk_test_dummy', STRIPE_WEBHOOK_SECRET='whsec_dummy',
                   PUBLIC_BASE_URL='https://easevent.example.com')
class SubscriptionTest(TestCase):

    def setUp(self):
        cache.clear()
        from subscriptions import services
        services._PRICE_CACHE.clear()
        self.user = User.objects.create_user(email='s@x.fr', password='x', first_name='Sam', last_name='T', is_verified=True)
        self.c = client_for(self.user)

    def _webhook(self, kind, obj):
        payload = {'type': kind, 'data': {'object': obj}}
        with mock.patch('stripe.Webhook.construct_event', return_value=payload):
            return client_for().post('/api/stripe/webhook/', data=json.dumps(payload), content_type='application/json',
                                     HTTP_STRIPE_SIGNATURE='t=1,v1=x')

    def _sub(self, status='active', plan='standard', cancel=False):
        return {'id': 'sub_1', 'customer': 'cus_1', 'status': status, 'cancel_at_period_end': cancel,
                'cancel_at': 1900000000 if cancel else None, 'current_period_start': 1800000000,
                'current_period_end': 1802592000, 'metadata': {'user_id': str(self.user.id), 'plan': plan, 'interval': 'monthly'},
                'items': {'data': [{'id': 'si_1', 'price': {'lookup_key': f'easevent_{plan}_monthly', 'recurring': {'interval': 'month'}}}]}}

    def test_catalogue(self):
        r = self.c.get('/api/subscriptions/')
        self.assertEqual([p['id'] for p in r.data['plans']], ['free', 'standard', 'pro'])
        self.assertEqual(r.data['subscription']['plan'], 'free')

    def test_achat_puis_webhooks(self):
        with mock.patch('stripe.Customer.create', return_value={'id': 'cus_1'}), \
             mock.patch('stripe.Price.list', return_value={'data': []}), \
             mock.patch('stripe.Product.create', return_value={'id': 'prod_1'}), \
             mock.patch('stripe.Price.create', return_value={'id': 'price_1'}) as price, \
             mock.patch('stripe.checkout.Session.create', return_value={'url': 'https://checkout.stripe.com/s'}) as session:
            r = self.c.post('/api/subscriptions/checkout/', {'plan': 'standard', 'interval': 'monthly'}, format='json')
        self.assertEqual(r.data['checkout_url'], 'https://checkout.stripe.com/s')
        self.assertEqual(price.call_args.kwargs['unit_amount'], 999)
        self.assertEqual(session.call_args.kwargs['mode'], 'subscription')
        self.assertEqual(self.c.post('/api/subscriptions/checkout/', {'plan': 'gold'}, format='json').status_code, 400)
        # L'application ne change jamais le plan : seul le webhook signé le fait
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_plan, 'free')
        self._webhook('customer.subscription.created', self._sub())
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_plan, 'standard')
        self.assertTrue(Notification.objects.filter(user=self.user, type='subscription').exists())
        # Paiement refusé : prévenu, accès conservé pendant les relances
        self._webhook('invoice.payment_failed', {'id': 'in_1', 'customer': 'cus_1'})
        self._webhook('customer.subscription.updated', self._sub(status='past_due'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_plan, 'standard')
        # Fin de l'abonnement → plan Gratuit
        self._webhook('customer.subscription.deleted', self._sub(status='canceled'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_plan, 'free')

    def test_resilier_et_reprendre(self):
        self.user.stripe_customer_id = 'cus_1'
        self.user.save()
        self._webhook('customer.subscription.created', self._sub())
        with mock.patch('stripe.Subscription.modify', return_value=self._sub(cancel=True)) as modify:
            r = self.c.post('/api/subscriptions/cancel/')
        self.assertTrue(modify.call_args.kwargs['cancel_at_period_end'])
        self.assertIsNotNone(r.data['subscription']['cancel_at'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_plan, 'standard')        # accès jusqu'à la fin de la période
        with mock.patch('stripe.Subscription.modify', return_value=self._sub()):
            r = self.c.post('/api/subscriptions/resume/')
        self.assertIsNone(r.data['subscription']['cancel_at'])

    def test_sans_cle_stripe(self):
        with override_settings(STRIPE_SECRET_KEY=''):
            r = self.c.post('/api/subscriptions/checkout/', {'plan': 'pro', 'interval': 'annual'}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (503, 'payments_unavailable'))
