"""Cogestion, répartition des invités, statistiques, finances, souvenirs, diffusion."""
import io
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from events.models import Event, EventCollaborator, EventComment, EventLike, EventMedia
from events.tests_lifecycle import client_for
from invitations.models import Invitation
from messaging.models import Conversation, EventBroadcast, Message
from notifications.models import Notification
from tickets.models import Ticket
from users.models import User

TMP = tempfile.mkdtemp()


def jpeg(name='p.jpg'):
    buf = io.BytesIO()
    Image.new('RGB', (40, 30), (200, 80, 60)).save(buf, 'JPEG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class TeamTest(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        cache.clear()
        mk = lambda e, f: User.objects.create_user(email=e, password='x', first_name=f, last_name='T',
                                                   is_verified=True, subscription_plan='pro')
        self.maman, self.papa, self.photo, self.lea, self.paul, self.zoe = (
            mk('maman@x.fr', 'Aline'), mk('papa@x.fr', 'Bruno'), mk('photo@x.fr', 'Chris'),
            mk('lea@x.fr', 'Léa'), mk('paul@x.fr', 'Paul'), mk('zoe@x.fr', 'Zoé'))
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(
            organizer=self.maman, title='Anniversaire de Noé', event_type='anniversaire', visibility='private',
            status='published', start_date=start, end_date=start + timedelta(hours=4), max_guests=30)
        self.m, self.p = client_for(self.maman), client_for(self.papa)
        self.url = f'/api/events/{self.event.id}'

    def add(self, user, role='cohost', by=None):
        r = client_for(by or self.maman).post(f'{self.url}/team/', {'user_id': str(user.id), 'role': role},
                                              format='json')
        self.assertEqual(r.status_code, 201, r.data)
        c = EventCollaborator.objects.get(event=self.event, user=user)
        self.assertEqual(client_for(user).post(f'/api/events/team/{c.id}/respond/', {'accept': True},
                                               format='json').status_code, 200)
        return c

    def past(self):
        self.event.start_date = timezone.now() - timedelta(hours=6)
        self.event.end_date = timezone.now() - timedelta(hours=1)
        self.event.save()

    # ── Équipe ────────────────────────────────────────────────
    def test_co_organisateur_invite_accepte_puis_gere(self):
        # Avant d'accepter : aucun droit
        self.m.post(f'{self.url}/team/', {'user_id': str(self.papa.id), 'role': 'cohost'}, format='json')
        self.assertEqual(self.p.get(f'{self.url}/detail/').status_code, 404)
        n = Notification.objects.get(user=self.papa, type='team_invite')
        self.assertIn('co-organiser', n.body)
        inv = self.p.get('/api/events/team/invitations/').data['results']
        self.assertEqual((len(inv), inv[0]['role'], inv[0]['invited_by']), (1, 'cohost', 'Aline T'))
        self.p.post(f"/api/events/team/{inv[0]['id']}/respond/", {'accept': True}, format='json')
        self.assertTrue(Notification.objects.filter(user=self.maman, type='team_response').exists())

        # Après : détail, modification, invités, mes événements
        r = self.p.get(f'{self.url}/detail/')
        self.assertEqual((r.status_code, r.data['my_role']), (200, 'cohost'))
        self.assertEqual(self.p.patch(f'{self.url}/update/', {'dress_code': 'Bleu'}, format='json').status_code, 200)
        self.assertEqual(self.p.get(f'{self.url}/participants/').status_code, 200)
        mine = {e['id']: e for e in self.p.get('/api/events/mes-evenements/').data['events']}
        self.assertEqual(mine[str(self.event.id)]['my_role'], 'cohost')
        # Mais ni suppression, ni finances, ni gestion de l'équipe
        self.assertEqual(self.p.delete(f'{self.url}/delete/').status_code, 404)
        self.assertEqual(self.p.get(f'{self.url}/finance/').status_code, 404)
        self.assertEqual(self.p.post(f'{self.url}/team/', {'user_id': str(self.lea.id), 'role': 'cohost'},
                                     format='json').status_code, 403)
        self.assertEqual(self.p.put(f'{self.url}/team/split/', {'apply': 'proposed'}, format='json').status_code, 404)

    def test_refus_et_droits_etrangers(self):
        self.m.post(f'{self.url}/team/', {'user_id': str(self.papa.id), 'role': 'cohost'}, format='json')
        c = EventCollaborator.objects.get(user=self.papa)
        # Une autre personne ne peut pas répondre à sa place
        self.assertEqual(client_for(self.lea).post(f'/api/events/team/{c.id}/respond/', {'accept': True},
                                                   format='json').status_code, 404)
        self.p.post(f'/api/events/team/{c.id}/respond/', {'accept': False}, format='json')
        self.assertEqual(self.p.get(f'{self.url}/detail/').status_code, 404)
        # Un inconnu ne voit pas l'équipe
        self.assertEqual(client_for(self.lea).get(f'{self.url}/team/').status_code, 404)
        # Deux fois la même personne
        self.m.post(f'{self.url}/team/', {'user_id': str(self.papa.id), 'role': 'cohost'}, format='json')
        r = self.m.post(f'{self.url}/team/', {'user_id': str(self.papa.id), 'role': 'cohost'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_photographe_ajoute_des_photos_seulement(self):
        self.add(self.photo, 'photographer')
        ph = client_for(self.photo)
        self.assertEqual(ph.get(f'{self.url}/detail/').status_code, 404)
        self.assertEqual(ph.post(f'{self.url}/invite/', {'emails': ['x@y.fr']}, format='json').status_code, 404)
        # Pas avant le début
        self.assertEqual(ph.post(f'{self.url}/memories/', {'image': jpeg()}, format='multipart').status_code, 409)
        self.past()
        r = ph.post(f'{self.url}/memories/', {'image': jpeg(), 'caption': 'Le gâteau'}, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['caption'], 'Le gâteau')
        # Le fichier est servi par lien signé, sans EXIF
        token_url = r.data['url'].split('testserver')[-1]
        self.assertEqual(client_for().get(token_url).status_code, 200)
        self.assertEqual(client_for().get('/api/events/memories/photo/faux/').status_code, 404)
        # Un invité (pas photographe) ne peut pas en ajouter
        Invitation.objects.create(event=self.event, invited_user=self.lea, token='t1', status='confirmed',
                                  expires_at=timezone.now() + timedelta(days=1))
        lea = client_for(self.lea)
        self.assertEqual(lea.post(f'{self.url}/memories/', {'image': jpeg()}, format='multipart').status_code, 403)
        # … mais il les voit, et pas un inconnu (événement privé)
        got = lea.get(f'{self.url}/memories/').data
        self.assertEqual((len(got['photos']), got['can_add']), (1, False))
        self.assertEqual(client_for(self.zoe).get(f'{self.url}/memories/').status_code, 404)
        # Suppression : l'auteur ou un organisateur
        mid = got['photos'][0]['id']
        self.assertEqual(lea.delete(f'{self.url}/memories/{mid}/').status_code, 404)
        self.assertEqual(self.m.delete(f'{self.url}/memories/{mid}/').status_code, 204)
        self.assertFalse(EventMedia.objects.exists())

    def test_photo_invalide_refusee(self):
        self.past()
        bad = SimpleUploadedFile('x.jpg', b'pas une image', content_type='image/jpeg')
        self.assertEqual(self.m.post(f'{self.url}/memories/', {'image': bad}, format='multipart').status_code, 400)

    # ── Répartition des invités ───────────────────────────────
    def test_repartition_proposee_modifiable_et_respectee(self):
        self.add(self.papa)
        team = self.m.get(f'{self.url}/team/').data
        self.assertEqual(team['proposed'], {str(self.maman.id): 15, str(self.papa.id): 15})
        self.assertEqual(team['split'], {})
        # Appliquer la proposition, puis l'ajuster (20 / 10)
        self.m.put(f'{self.url}/team/split/', {'apply': 'proposed'}, format='json')
        r = self.m.put(f'{self.url}/team/split/', {'split': {str(self.maman.id): 20, str(self.papa.id): 10}},
                       format='json')
        self.assertEqual(r.data['split'][str(self.papa.id)], 10)
        # Somme > places : refusé
        r = self.m.put(f'{self.url}/team/split/', {'split': {str(self.maman.id): 25, str(self.papa.id): 10}},
                       format='json')
        self.assertEqual(r.data['code'], 'split_too_large')
        # Inconnu dans la répartition : refusé
        r = self.m.put(f'{self.url}/team/split/', {'split': {str(self.lea.id): 1}}, format='json')
        self.assertEqual(r.data['code'], 'invalid_split')

        # Le co-organisateur invite dans sa part (10)
        emails = [f'g{i}@x.fr' for i in range(11)]
        r = self.p.post(f'{self.url}/invite/', {'emails': emails}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (403, 'split_limit'))
        r = self.p.post(f'{self.url}/invite/', {'emails': emails[:10]}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Invitation.objects.filter(invited_by=self.papa).count(), 10)
        team = self.m.get(f'{self.url}/team/').data
        self.assertEqual(team['used'][str(self.papa.id)], 10)
        papa = next(x for x in team['members'] if x['user_id'] == str(self.papa.id))
        self.assertEqual((papa['used'], papa['quota']), (10, 10))
        # Un refus libère une place
        Invitation.objects.filter(email='g0@x.fr').update(status='declined')
        self.assertEqual(self.p.post(f'{self.url}/invite/', {'emails': ['g99@x.fr']}, format='json').status_code, 201)

    def test_nombre_invites_facultatif(self):
        self.event.max_guests = None
        self.event.save()
        self.add(self.papa)
        team = self.m.get(f'{self.url}/team/').data
        self.assertEqual((team['proposed'], team['max_guests']), ({}, None))
        r = self.m.put(f'{self.url}/team/split/', {'apply': 'proposed'}, format='json')
        self.assertEqual(r.data['code'], 'no_max_guests')
        self.assertEqual(self.p.post(f'{self.url}/invite/', {'emails': ['a@b.fr']}, format='json').status_code, 201)

    def test_nombre_de_places_total(self):
        self.event.max_guests = 2
        self.event.save()
        r = self.m.post(f'{self.url}/invite/', {'emails': ['a@x.fr', 'b@x.fr', 'c@x.fr']}, format='json')
        self.assertEqual(r.data['code'], 'event_full')

    def test_equipe_ne_s_invite_pas_et_peut_se_parler(self):
        self.add(self.papa)
        r = self.m.post(f'{self.url}/invite/', {'user_ids': [str(self.papa.id)]}, format='json')
        self.assertEqual(r.data['skipped'][0]['reason'], 'self')
        # Messagerie directe entre organisateurs, sans être amis
        r = self.m.post('/api/conversations/', {'friend_id': str(self.papa.id)}, format='json')
        self.assertIn(r.status_code, (200, 201), r.data)
        r = client_for(self.lea).post('/api/conversations/', {'friend_id': str(self.papa.id)}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_retirer_et_quitter(self):
        c = self.add(self.papa)
        self.m.put(f'{self.url}/team/split/', {'apply': 'proposed'}, format='json')
        self.assertEqual(client_for(self.lea).delete(f'{self.url}/team/{c.id}/').status_code, 404)
        self.assertEqual(self.p.delete(f'{self.url}/team/{c.id}/').status_code, 204)      # il quitte
        self.event.refresh_from_db()
        self.assertNotIn(str(self.papa.id), self.event.guest_split)
        c = self.add(self.photo, 'photographer', by=self.maman)
        self.assertEqual(self.m.delete(f'{self.url}/team/{c.id}/').status_code, 204)

    # ── Statistiques ─────────────────────────────────────────
    def test_statistiques_et_presence_reelle(self):
        for i, u in enumerate((self.lea, self.paul, self.zoe)):
            Invitation.objects.create(event=self.event, invited_user=u, token=f't{i}',
                                      status=('confirmed', 'confirmed', 'declined')[i],
                                      expires_at=timezone.now() + timedelta(days=9))
        Ticket.objects.create(event=self.event, user=self.lea, status='generated', checked_in_at=timezone.now())
        Ticket.objects.create(event=self.event, user=self.paul, status='generated')
        EventLike.objects.create(event=self.event, user=self.lea)
        s = self.m.get(f'{self.url}/stats/').data
        self.assertEqual((s['invited'], s['accepted'], s['declined'], s['participants'], s['checked_in'], s['likes']),
                         (3, 2, 1, 2, 1, 1))
        self.assertIsNone(s['attendance'])
        self.assertEqual(self.m.post(f'{self.url}/attendance/', {'count': 2}, format='json').status_code, 409)
        self.past()
        self.add(self.papa)
        s = self.p.post(f'{self.url}/attendance/', {'count': 2}, format='json').data   # co-organisateur aussi
        self.assertEqual((s['attendance'], s['attendance_rate'], s['phase']), (2, 100, 'past'))
        self.assertEqual(self.m.post(f'{self.url}/attendance/', {'count': -1}, format='json').status_code, 400)
        self.assertEqual(self.m.post(f'{self.url}/attendance/', {'count': 'abc'}, format='json').status_code, 400)
        self.assertIsNone(self.m.post(f'{self.url}/attendance/', {'count': ''}, format='json').data['attendance'])
        self.assertEqual(client_for(self.lea).get(f'{self.url}/stats/').status_code, 404)

    # ── Commentaires ─────────────────────────────────────────
    def test_commentaires_apres_evenement_prive_invites_seulement(self):
        Invitation.objects.create(event=self.event, invited_user=self.lea, token='t', status='confirmed',
                                  expires_at=timezone.now() - timedelta(days=1))       # invitation expirée : reste invitée
        lea = client_for(self.lea)
        r = lea.post(f'{self.url}/comments/', {'body': 'Merci !'}, format='json')
        self.assertEqual(r.data['code'], 'not_yet')
        self.past()
        r = lea.post(f'{self.url}/comments/', {'body': 'Merci pour cette journée !'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(client_for(self.zoe).post(f'{self.url}/comments/', {'body': 'x'}, format='json').status_code, 404)
        n = Notification.objects.get(user=self.maman, type='event_comment')
        lea.post(f'{self.url}/comments/', {'body': 'Et encore bravo'}, format='json')
        self.assertEqual(Notification.objects.filter(user=self.maman, type='event_comment').count(), 1)
        n.refresh_from_db()
        self.assertIn('Et encore bravo', n.body)
        self.assertEqual(n.title, 'Léa T')
        got = self.m.get(f'{self.url}/comments/').data
        self.assertEqual((got['count'], got['open'], got['comments'][0]['can_delete']), (2, True, True))
        cid = got['comments'][0]['id']
        self.assertEqual(client_for(self.zoe).delete(f'{self.url}/comments/{cid}/').status_code, 404)
        self.assertEqual(self.m.delete(f'{self.url}/comments/{cid}/').status_code, 204)
        self.assertEqual(lea.post(f'{self.url}/comments/', {'body': ''}, format='json').status_code, 400)
        self.assertEqual(lea.post(f'{self.url}/comments/', {'body': 'x' * 1001}, format='json').status_code, 400)

    def test_commentaires_evenement_public_tout_le_monde(self):
        self.event.visibility = 'public'
        self.event.save()
        self.past()
        r = client_for(self.zoe).post(f'{self.url}/comments/', {'body': 'Super soirée'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(client_for().get(f'{self.url}/comments/').status_code, 401)

    # ── Diffusion ────────────────────────────────────────────
    def test_message_a_tous_les_invites(self):
        for i, u in enumerate((self.lea, self.paul, self.zoe)):
            Invitation.objects.create(event=self.event, invited_user=u, token=f't{i}',
                                      status=('confirmed', 'sent', 'declined')[i],
                                      expires_at=timezone.now() + timedelta(days=9))
        Invitation.objects.create(event=self.event, email='sans@compte.fr', token='t9', status='sent',
                                  expires_at=timezone.now() + timedelta(days=9))
        self.add(self.papa)
        info = self.p.get(f'{self.url}/broadcast/').data
        self.assertEqual((info['recipients'], info['without_account']), (2, 1))
        r = self.p.post(f'{self.url}/broadcast/', {'message': 'Rendez-vous à 15 h, entrée côté jardin.'},
                        format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['recipients'], r.data['without_account']), (2, 1))
        self.assertIn('sans compte', r.data['detail'])
        # Une copie dans la conversation de chacun, signée par le co-organisateur
        msgs = Message.objects.filter(meta__broadcast=r.data['id'])
        self.assertEqual(msgs.count(), 2)
        self.assertEqual({m.conversation.participant_id for m in msgs}, {self.lea.id, self.paul.id})
        self.assertEqual(msgs.first().meta['by'], 'Bruno T')
        self.assertEqual(Notification.objects.filter(type='event_broadcast').count(), 2)
        lea_conv = Conversation.objects.get(event=self.event, participant=self.lea)
        got = client_for(self.lea).get(f'/api/conversations/{lea_conv.id}/messages/').data
        items = got['results'] if isinstance(got, dict) and 'results' in got else got.get('messages', got)
        self.assertTrue(any(m.get('broadcast') for m in items))
        self.assertEqual(len(self.m.get(f'{self.url}/broadcast/').data['history']), 1)
        # Limites
        self.assertEqual(self.m.post(f'{self.url}/broadcast/', {'message': ''}, format='json').status_code, 400)
        self.assertEqual(client_for(self.lea).post(f'{self.url}/broadcast/', {'message': 'x'},
                                                   format='json').status_code, 404)
        for _ in range(4):
            self.m.post(f'{self.url}/broadcast/', {'message': 'Encore'}, format='json')
        self.assertEqual(self.m.post(f'{self.url}/broadcast/', {'message': 'Trop'}, format='json').status_code, 429)
        self.assertEqual(EventBroadcast.objects.count(), 5)

    # ── Finances ─────────────────────────────────────────────
    @override_settings(PLATFORM_FEE_PERCENT=3)
    def test_finances_combien_je_gagne(self):
        self.event.is_paid, self.event.price, self.event.currency = True, Decimal('20'), 'EUR'
        self.event.save()
        Ticket.objects.create(event=self.event, user=self.lea, status='generated', price=20, payment_status='paid')
        Ticket.objects.create(event=self.event, user=self.paul, status='generated', price=20, payment_status='paid',
                              mobile_money_reference='ev-1', purchased_by=self.zoe)
        Ticket.objects.create(event=self.event, user=self.zoe, status='cancelled', price=20, payment_status='refunded')
        Ticket.objects.create(event=self.event, user=self.photo, status='pending', price=20, payment_status='pending')
        f = self.m.get(f'{self.url}/finance/').data
        self.assertEqual((f['tickets_sold'], f['gross'], f['fee_percent'], f['fee'], f['net']),
                         (2, 40.0, 3.0, 1.2, 38.8))
        self.assertEqual((f['refunds'], f['refunded'], f['gifts']), (1, 20.0, 1))
        self.assertEqual(f['by_method'], {'card': 1, 'mobile_money': 1})
        self.assertEqual(f['by_price'], [{'price': 20.0, 'count': 2}])
        self.assertEqual(f['fill_rate'], 7)
        self.assertTrue(f['payouts']['mobile_money_due'])
