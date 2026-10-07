"""rsvp/tests.py — M14 questions (organisateur), M19 réponses (invité), liste et CSV."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from events.models import Event
from invitations.models import Invitation
from tickets.models import Ticket
from users.models import User

from .models import RsvpAnswer, RsvpQuestion


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')


class RsvpTest(TestCase):

    def setUp(self):
        cache.clear()
        self.c = APIClient()
        mk = lambda e, f: User.objects.create_user(email=e, password='x', first_name=f, last_name='T', is_verified=True)
        self.orga, self.claire, self.intrus, self.paul = mk('lea@x.fr', 'Léa'), mk('claire@x.fr', 'Claire'), mk('z@x.fr', 'Zoé'), mk('p@x.fr', 'Paul')
        start = timezone.now() + timedelta(days=5)
        self.event = Event.objects.create(organizer=self.orga, title='Mariage', event_type='mariage',
                                          start_date=start, end_date=start + timedelta(hours=8),
                                          status='published', visibility='private')
        self.inv = Invitation.objects.create(event=self.event, invited_user=self.claire, token='t' * 64,
                                             status='sent', channel='platform_notification',
                                             expires_at=start + timedelta(days=8), sent_at=timezone.now())
        self.url = f'/api/events/{self.event.id}/rsvp-questions/'

    def add(self, **data):
        auth(self.c, self.orga)
        return self.c.post(self.url, data, format='json')

    def test_creation_validation_et_suggestions(self):
        auth(self.c, self.orga)
        r = self.c.get(self.url)
        self.assertEqual(r.data['questions'], [])
        self.assertIn('Venez-vous accompagné(e) ?', [s['label'] for s in r.data['suggestions']])    # selon le type

        r = self.add(kind='single', label='  Menu   ?', options=['Poisson', 'Viande', 'poisson', ''], required=True)
        self.assertEqual(r.status_code, 201, r.data)
        q = r.data['questions'][0]
        self.assertEqual((q['label'], q['options'], q['required']), ('Menu ?', ['Poisson', 'Viande'], True))

        self.assertEqual(self.add(kind='single', label='Un seul choix', options=['A']).data['code'], 'invalid_options')
        self.assertEqual(self.add(kind='autre', label='x').data['code'], 'invalid_kind')
        self.assertEqual(self.add(kind='text', label='').data['code'], 'required_field')
        self.assertEqual(self.add(kind='text', label='x' * 201).data['code'], 'too_long')
        for i in range(4):
            self.add(kind='text', label=f'Question {i}')
        r = self.add(kind='text', label='Sixième')
        self.assertEqual((r.status_code, r.data['code']), (400, 'max_questions'))
        self.assertEqual(self.c.get(self.url).data['suggestions'], [])

    def test_seul_l_organisateur_gere_les_questions(self):
        q = self.add(kind='yesno', label='Navette ?').data['questions'][0]
        for user in (self.claire, self.intrus):
            auth(self.c, user)
            self.assertEqual(self.c.get(self.url).status_code, 404)
            self.assertEqual(self.c.post(self.url, {'kind': 'text', 'label': 'x'}, format='json').status_code, 404)
            self.assertEqual(self.c.delete(f"{self.url}{q['id']}/").status_code, 404)
            self.assertEqual(self.c.get(f'/api/events/{self.event.id}/rsvp-answers/').status_code, 404)

    def test_accepter_exige_les_reponses_obligatoires(self):
        q_menu = self.add(kind='single', label='Menu ?', options=['Poisson', 'Viande'], required=True).data['questions'][0]
        q_nav = self.add(kind='yesno', label='Navette ?').data['questions'][1]
        auth(self.c, self.claire)
        accept = f'/api/invitations/{self.inv.id}/repondre/'

        r = self.c.post(accept, {'status': 'confirmed'}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (400, 'rsvp_questions'))     # l'app affiche les questions
        self.assertEqual(r.data['missing'], [q_menu['id']])
        self.assertEqual(len(r.data['questions']), 2)
        r = self.c.post(accept, {'status': 'confirmed', 'rsvp_answers': {}}, format='json')
        self.assertEqual(r.data['code'], 'rsvp_required')
        self.assertFalse(Ticket.objects.filter(user=self.claire).exists())

        r = self.c.post(accept, {'status': 'confirmed', 'rsvp_answers': {q_menu['id']: 'Pizza'}}, format='json')
        self.assertEqual(r.data['code'], 'invalid_answer')

        r = self.c.post(accept, {'status': 'confirmed', 'rsvp_answers': {q_menu['id']: 'Viande', q_nav['id']: True}}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(Ticket.objects.filter(user=self.claire, event=self.event).exists())
        mine = self.c.get(f'/api/events/{self.event.id}/rsvp/').data
        self.assertEqual(mine['answers'], {q_menu['id']: 'Viande', q_nav['id']: True})

        # Modifier ses réponses ensuite ; effacer une réponse facultative
        self.c.post(f'/api/events/{self.event.id}/rsvp/', {'answers': {q_nav['id']: None}}, format='json')
        self.assertEqual(self.c.get(f'/api/events/{self.event.id}/rsvp/').data['answers'], {q_menu['id']: 'Viande'})

        # Organisateur : liste des invités, synthèse, CSV (plan Standard)
        self.orga.subscription_plan = 'standard'
        self.orga.save()
        auth(self.c, self.orga)
        row = self.c.get(f'/api/events/{self.event.id}/participants/').data['participants'][0]
        self.assertEqual([(a['label'], a['display']) for a in row['rsvp']], [('Menu ?', 'Viande')])
        summary = self.c.get(f'/api/events/{self.event.id}/rsvp-answers/').data['questions']
        self.assertEqual(summary[0]['counts'], [{'label': 'Poisson', 'count': 0}, {'label': 'Viande', 'count': 1}])
        link = self.c.post(f'/api/events/{self.event.id}/participants/export-link/').data['url']
        csv = self.c.get(link[link.index('/api/'):]).content.decode('utf-8-sig')
        self.assertIn('Menu ?', csv.splitlines()[0])
        self.assertIn('Viande', csv.splitlines()[1])

    def test_questions_facultatives_proposees_une_fois(self):
        q = self.add(kind='yesno', label='Navette ?').data['questions'][0]
        auth(self.c, self.claire)
        accept = f'/api/invitations/{self.inv.id}/repondre/'
        self.assertEqual(self.c.post(accept, {'status': 'confirmed'}, format='json').data['code'], 'rsvp_questions')
        self.assertEqual(self.c.post(accept, {'status': 'confirmed', 'rsvp_answers': {}}, format='json').status_code, 200)

    def test_refuser_ne_demande_rien(self):
        self.add(kind='text', label='Allergies ?', required=True)
        auth(self.c, self.claire)
        r = self.c.post(f'/api/invitations/{self.inv.id}/repondre/', {'status': 'declined'}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_ticket_evenement_public(self):
        self.event.visibility = 'public'
        self.event.save()
        q = self.add(kind='multiple', label='Sessions ?', options=['A', 'B', 'C'], required=True).data['questions'][0]
        auth(self.c, self.paul)
        take = f'/api/events/{self.event.id}/tickets/'
        self.assertEqual(self.c.post(take, {}, format='json').data['code'], 'rsvp_questions')
        self.assertEqual(self.c.post(take, {'rsvp_answers': {q['id']: []}}, format='json').data['code'], 'rsvp_required')
        r = self.c.post(take, {'rsvp_answers': {q['id']: ['C', 'A', 'A']}}, format='json')
        self.assertIn(r.status_code, (200, 201), r.data)
        self.assertEqual(RsvpAnswer.objects.get(user=self.paul).value, ['A', 'C'])     # ordre des choix, sans doublon

    def test_invite_externe_ne_voit_pas_les_questions_d_un_evenement_prive(self):
        self.add(kind='text', label='Allergies ?')
        auth(self.c, self.intrus)
        self.assertEqual(self.c.get(f'/api/events/{self.event.id}/rsvp/').status_code, 404)
        self.assertEqual(self.c.post(f'/api/events/{self.event.id}/rsvp/', {'answers': {}}, format='json').status_code, 404)

    def test_modifier_une_question_met_a_jour_les_reponses(self):
        q = self.add(kind='multiple', label='Sessions ?', options=['A', 'B', 'C']).data['questions'][0]
        question = RsvpQuestion.objects.get(pk=q['id'])
        RsvpAnswer.objects.create(question=question, user=self.claire, value=['A', 'B'])
        RsvpAnswer.objects.create(question=question, user=self.paul, value=['C'])
        auth(self.c, self.orga)
        r = self.c.patch(f"{self.url}{q['id']}/", {'options': ['A', 'D']}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(list(RsvpAnswer.objects.values_list('value', flat=True)), [['A']])    # C retiré : réponse supprimée
        self.c.patch(f"{self.url}{q['id']}/", {'kind': 'text'}, format='json')
        self.assertFalse(RsvpAnswer.objects.exists())                                         # type changé

    def test_ordre_et_suppression(self):
        ids = [self.add(kind='text', label=f'Q{i}').data['questions'][i]['id'] for i in range(3)]
        r = self.c.post(f'{self.url}reorder/', {'order': [ids[2], ids[0], ids[1]]}, format='json')
        self.assertEqual([q['label'] for q in r.data['questions']], ['Q2', 'Q0', 'Q1'])
        self.assertEqual(self.c.post(f'{self.url}reorder/', {'order': ids[:2]}, format='json').status_code, 400)
        r = self.c.delete(f'{self.url}{ids[2]}/')
        self.assertEqual([(q['label'], q['position']) for q in r.data['questions']], [('Q0', 0), ('Q1', 1)])
