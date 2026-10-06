"""
invitations/tests.py — lot Invitations (M12, M13, M29–M31).
Emails : backend mémoire de Django. SMS : Twilio simulé (aucun appel réseau).
"""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event
from tickets.models import Ticket
from users.models import User

from .crypto import decrypt
from .models import Invitation
from .services import hash_token, mask_phone, normalize_phone

TWILIO = dict(TWILIO_ACCOUNT_SID='AC_test', TWILIO_AUTH_TOKEN='secret', TWILIO_FROM_NUMBER='+33700000000')


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')


def link_token(body):
    return re.search(r'/i/([A-Za-z0-9_\-]{43})/', body).group(1)


class SmsOk:
    status_code = 201

    @staticmethod
    def json():
        return {'sid': 'SM1'}


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PUBLIC_BASE_URL='https://easevent.example.com', **TWILIO)
class InvitationFlowTest(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.orga = User.objects.create_user(email='sarah@x.fr', password='x', first_name='Sarah',
                                             last_name='Martin', is_verified=True)
        self.claire = User.objects.create_user(email='claire@x.fr', password='x', first_name='Claire',
                                               last_name='Lemoine', is_verified=True)
        start = timezone.now() + timedelta(days=10)
        self.event = Event.objects.create(
            organizer=self.orga, title='Summit Innovation AI', event_type='conference', start_date=start,
            end_date=start + timedelta(hours=8), status='published', visibility='private',
            is_paid=True, price='25.00', dress_code='Business', location_address='Station F, Paris')
        auth(self.client, self.orga)

    def invite(self, **body):
        with mock.patch('invitations.sms.requests.post', return_value=SmsOk()) as post:
            r = self.client.post(f'/api/events/{self.event.id}/invite/', body, format='json')
        return r, post

    # ── M12 / M29 : envoi par lot ────────────────────────────
    def test_lot_email_membre_et_telephone(self):
        r, post = self.invite(
            emails=['claire@x.fr', 'julien.morel@gmail.com', 'pas-un-email'],
            phone_numbers=['+33 6 12 45 78 90', {'phone': '0033698210344', 'name': 'Amina'}, '123'],
            message='On a hâte de vous voir !')
        self.assertEqual(r.status_code, 201, r.data)
        kinds = sorted(c['kind'] for c in r.data['created'])
        self.assertEqual(kinds, ['email', 'member', 'phone', 'phone'])
        self.assertEqual({s['reason'] for s in r.data['skipped']}, {'invalid_email', 'invalid_phone'})
        self.assertEqual(r.data['usage'], {'used': 4, 'limit': 50, 'plan': 'free'})

        # Emails M30 : membre + non-inscrit, lien /i/<jeton>/, infos de l'événement
        self.assertEqual(len(mail.outbox), 2)
        julien = next(m for m in mail.outbox if m.to == ['julien.morel@gmail.com'])
        self.assertEqual(julien.subject, 'Sarah vous invite à Summit Innovation AI')
        html = julien.alternatives[0][0]
        for text in ('25,00 €', 'Business', 'Station F, Paris', 'On a hâte de vous voir !', "Voir l'invitation"):
            self.assertIn(text, html)
        self.assertIn('https://easevent.example.com/i/', julien.body)

        # SMS Twilio : 2 numéros normalisés, lien personnel
        self.assertEqual(post.call_count, 2)
        sent_to = sorted(call.kwargs['data']['To'] for call in post.call_args_list)
        self.assertEqual(sent_to, ['+33612457890', '+33698210344'])
        self.assertIn('Sarah vous invite à « Summit Innovation AI »', post.call_args.kwargs['data']['Body'])

        inv = Invitation.objects.get(contact_name='Amina')
        self.assertEqual(inv.channel, 'sms')
        self.assertEqual(inv.delivery_status, 'sent')
        self.assertNotIn('698210344', inv.phone_number)          # chiffré en base
        self.assertEqual(decrypt(inv.phone_number), '+33698210344')
        member = Invitation.objects.get(invited_user=self.claire)
        self.assertEqual(member.channel, 'platform_notification')

    def test_doublons_et_soi_meme(self):
        self.invite(emails=['julien@x.fr'], phone_numbers=['+33612457890'])
        r, _ = self.invite(emails=['JULIEN@x.fr', 'sarah@x.fr'], phone_numbers=['06 12 45 78 90', '+33 612457890'])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['created'], [])
        reasons = sorted(s['reason'] for s in r.data['skipped'])
        self.assertEqual(reasons, ['already_invited', 'already_invited', 'invalid_phone', 'self'])

    def test_limite_du_plan(self):
        with self.settings(PLAN_GUEST_LIMITS={'free': 2, 'standard': 500, 'pro': None}):
            r, _ = self.invite(emails=['a@x.fr', 'b@x.fr', 'c@x.fr'])
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'plan_limit')
        self.assertEqual(r.data['remaining'], 2)
        self.assertEqual(Invitation.objects.count(), 0)

    def test_message_trop_long(self):
        r, _ = self.invite(emails=['a@x.fr'], message='x' * 101)
        self.assertEqual(r.data['code'], 'message_too_long')

    def test_sms_non_configure(self):
        with self.settings(TWILIO_ACCOUNT_SID=''):
            r, post = self.invite(phone_numbers=['+221774501290'])
        post.assert_not_called()
        self.assertEqual(r.data['created'][0]['delivery_status'], 'not_configured')

    def test_bola_evenement_dun_autre(self):
        auth(self.client, self.claire)
        r, _ = self.invite(emails=['a@x.fr'])
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get(f'/api/events/{self.event.id}/participants/').status_code, 404)

    # ── Membres : recherche et vérification d'emails ─────────
    def test_recherche_membres(self):
        r = self.client.get('/api/users/search/', {'q': 'cla lem', 'event': str(self.event.id)})
        self.assertEqual([u['first_name'] for u in r.data['results']], ['Claire'])
        self.assertNotIn('email', r.data['results'][0])
        self.assertEqual(self.client.get('/api/users/search/', {'q': 'c'}).data['results'], [])
        self.assertEqual(self.client.get('/api/users/search/', {'q': 'sarah'}).data['results'], [])  # pas soi-même

        self.invite(user_ids=[str(self.claire.id)])
        r = self.client.get('/api/users/search/', {'q': 'claire', 'event': str(self.event.id)})
        self.assertTrue(r.data['results'][0]['already_invited'])

    def test_lookup_emails(self):
        r = self.client.post('/api/users/lookup/', {'emails': ['Claire@x.fr', 'inconnu@x.fr', 'nope']}, format='json')
        res = r.data['results']
        self.assertEqual((res[0]['has_account'], res[0]['name']), (True, 'Claire Lemoine'))
        self.assertFalse(res[1]['has_account'])
        self.assertFalse(res[2]['valid'])

    # ── M13 : liste, compteurs, relances ─────────────────────
    def test_liste_compteurs_et_relances(self):
        self.invite(user_ids=[str(self.claire.id)], emails=['julien@x.fr'], phone_numbers=['+33612457890'])
        mail.outbox.clear()
        inv = Invitation.objects.get(invited_user=self.claire)
        Ticket.objects.create(event=self.event, user=self.claire, invitation=inv, status='generated',
                              price='25.00', payment_status='paid')
        inv.status = 'confirmed'
        inv.save()

        r = self.client.get(f'/api/events/{self.event.id}/participants/')
        self.assertEqual(r.data['counts'], {'confirmed': 1, 'pending': 2, 'declined': 0, 'total': 3})
        self.assertEqual(r.data['remindable'], 2)
        phone_row = next(p for p in r.data['participants'] if p['kind'] == 'phone')
        self.assertEqual(phone_row['phone'], '+33 6 •• •• 78 90')

        julien = Invitation.objects.get(email='julien@x.fr')
        old_hash = julien.token
        r = self.client.post(f'/api/invitations/{julien.id}/remind/')
        self.assertEqual(r.status_code, 200, r.data)
        julien.refresh_from_db()
        self.assertNotEqual(julien.token, old_hash)                # nouveau lien
        self.assertTrue(mail.outbox[-1].subject.startswith('Rappel : '))
        self.assertEqual(julien.token, hash_token(link_token(mail.outbox[-1].body)))
        # Une relance par jour au plus
        self.assertEqual(self.client.post(f'/api/invitations/{julien.id}/remind/').status_code, 400)

        with mock.patch('invitations.sms.requests.post', return_value=SmsOk()):
            r = self.client.post(f'/api/events/{self.event.id}/remind-pending/')
        self.assertEqual(r.data['reminded'], 1)  # seul le SMS restait relançable

    def test_relance_sms_sans_twilio_garde_le_lien(self):
        self.invite(phone_numbers=['+33612457890'])
        inv = Invitation.objects.get()
        before = inv.token
        with self.settings(TWILIO_ACCOUNT_SID=''):
            data = self.client.get(f'/api/events/{self.event.id}/participants/').data
            self.assertEqual(data['remindable'], 0)
            r = self.client.post(f'/api/invitations/{inv.id}/remind/')
            self.assertEqual(r.status_code, 400)
            r = self.client.post(f'/api/events/{self.event.id}/remind-pending/')
            self.assertEqual(r.data['reminded'], 0)
        inv.refresh_from_db()
        self.assertEqual(inv.token, before)                       # lien d'origine toujours valable

    def test_export_reserve_au_plan_standard(self):
        self.invite(emails=['=cmd@x.fr'])
        self.assertEqual(self.client.post(f'/api/events/{self.event.id}/participants/export-link/').data['code'],
                         'plan_required')
        self.orga.subscription_plan = 'standard'
        self.orga.save()
        url = self.client.post(f'/api/events/{self.event.id}/participants/export-link/').data['url']
        self.client.credentials()
        r = self.client.get(url.replace('https://easevent.example.com', ''))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode('utf-8-sig')
        self.assertIn("'=cmd@x.fr", body)                         # injection CSV neutralisée
        self.assertEqual(self.client.get('/api/events/participants/export/faux/').status_code, 404)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PUBLIC_BASE_URL='https://easevent.example.com', **TWILIO)
class InvitationLinkTest(TestCase):
    """Parcours G : personne sans compte (M30 → M31 → compte → ticket)."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.orga = User.objects.create_user(email='sarah@x.fr', password='x', first_name='Sarah',
                                             last_name='Martin', is_verified=True)
        start = timezone.now() + timedelta(days=10)
        self.event = Event.objects.create(
            organizer=self.orga, title='Mariage', event_type='mariage', start_date=start,
            end_date=start + timedelta(hours=8), status='published', visibility='private')
        auth(self.client, self.orga)
        self.client.post(f'/api/events/{self.event.id}/invite/', {'emails': ['julien@x.fr']}, format='json')
        self.token = link_token(mail.outbox[-1].body)
        self.inv = Invitation.objects.get(email='julien@x.fr')
        self.client.credentials()

    def test_token_stocke_hache(self):
        self.assertEqual(self.inv.token, hash_token(self.token))
        self.assertNotEqual(self.inv.token, self.token)

    def test_lien_public_marque_ouvert(self):
        r = self.client.get(f'/api/invitations/by-token/{self.token}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['event']['title'], 'Mariage')
        self.assertEqual(r.data['invited']['email'], 'julien@x.fr')
        self.assertFalse(r.data['invited']['has_account'])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, 'opened')
        self.assertEqual(self.client.get('/api/invitations/by-token/inconnu/').status_code, 404)

    def test_lien_revoque_ne_revele_rien(self):
        self.inv.status = 'revoked'
        self.inv.save()
        r = self.client.get(f'/api/invitations/by-token/{self.token}/')
        self.assertEqual(r.status_code, 410)
        self.assertNotIn('event', r.data)

    def test_decliner_sans_compte(self):
        r = self.client.post(f'/api/invitations/by-token/{self.token}/decline/')
        self.assertEqual(r.status_code, 200)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, 'declined')

    def test_inscription_avec_le_lien_connecte_directement(self):
        r = self.client.post('/api/auth/register/', {
            'email': 'julien@x.fr', 'password': 'Un-mot-de-passe-solide-42', 'first_name': 'Julien',
            'last_name': 'Morel', 'accepted_privacy': True, 'invitation_token': self.token}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIn('access', r.data)                            # email prouvé par le lien
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.invited_user.email, 'julien@x.fr')

        # L'invitation est dans « Mes invitations » ; accepter crée le ticket en attente
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        self.assertEqual(self.client.get('/api/invitations/mine/').data['count'], 1)
        r = self.client.post(f'/api/invitations/{self.inv.id}/repondre/', {'status': 'confirmed'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Ticket.objects.get(id=r.data['ticket_id']).status, 'pending')

    def test_inscription_autre_email_reste_a_verifier(self):
        r = self.client.post('/api/auth/register/', {
            'email': 'autre@x.fr', 'password': 'Un-mot-de-passe-solide-42', 'first_name': 'Jo',
            'last_name': 'Morel', 'accepted_privacy': True, 'invitation_token': self.token}, format='json')
        self.assertNotIn('access', r.data)
        self.assertTrue(r.data['requires_verification'])

    def test_rattachement_apres_connexion(self):
        julie = User.objects.create_user(email='julie@x.fr', password='x', first_name='Julie',
                                         last_name='D', is_verified=True)
        auth(self.client, julie)
        r = self.client.post('/api/invitations/claim/', {'token': self.token}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.invited_user, julie)

        # Un autre compte ne peut plus s'en emparer
        other = User.objects.create_user(email='o@x.fr', password='x', first_name='O', last_name='O')
        auth(self.client, other)
        self.assertEqual(self.client.post('/api/invitations/claim/', {'token': self.token}, format='json').status_code, 409)

    def test_page_web_et_refus(self):
        r = self.client.get(f'/i/{self.token}/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Sarah Martin')
        self.assertContains(r, f'easevent://i/{self.token}')
        self.assertEqual(r['Referrer-Policy'], 'same-origin')
        self.assertEqual(r['Cache-Control'], 'no-store')

        client = APIClient(enforce_csrf_checks=True)
        page = client.get(f'/i/{self.token}/')
        csrf = page.cookies['csrftoken'].value
        r = client.post(f'/i/{self.token}/', {'action': 'decline', 'csrfmiddlewaretoken': csrf})
        self.assertContains(r, 'Invitation déclinée')
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, 'declined')

        self.assertEqual(self.client.get('/i/faux-jeton/').status_code, 404)
        self.assertEqual(self.client.get('/confidentialite/').status_code, 200)


class PhoneHelpersTest(TestCase):
    def test_normalisation(self):
        self.assertEqual(normalize_phone('+33 6 12-34.56 78'), '+33612345678')
        self.assertEqual(normalize_phone('00221 77 450 12 90'), '+221774501290')
        self.assertIsNone(normalize_phone('0612345678'))   # indicatif obligatoire
        self.assertIsNone(normalize_phone('+33 abc'))

    def test_masque(self):
        self.assertEqual(mask_phone('+33612345678'), '+33 6 •• •• 56 78')
        self.assertEqual(mask_phone('+221774501290'), '+221 7 •• •• 12 90')
        self.assertEqual(mask_phone('+14155550123'), '+1 4 •• •• 01 23')
