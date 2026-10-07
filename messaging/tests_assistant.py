from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from events.models import Event
from notifications.models import Notification
from tickets.models import Ticket
from users.models import User

from . import assistant, services
from .models import EventQuestion, Message

NO_AI = mock.patch('minisite.ai.run_role', return_value=(None, [], None))


def ai_says(answerable, answer='', is_question=True):
    return mock.patch('minisite.ai.run_role', return_value=(
        {'is_question': is_question, 'answerable': answerable, 'answer': answer}, [], 'gemini'))


class AssistantTest(TestCase):
    def setUp(self):
        cache.clear()
        mk = lambda e, f: User.objects.create_user(email=e, password='x', first_name=f, last_name='T', is_verified=True)
        self.orga, self.paul, self.claire = mk('orga@x.fr', 'Nadia'), mk('paul@x.fr', 'Paul'), mk('claire@x.fr', 'Claire')
        start = timezone.now() + timedelta(days=20)
        self.event = Event.objects.create(
            organizer=self.orga, title='Astronomie : la Lune et nous', event_type='conference', status='published',
            visibility='public', start_date=start, end_date=start + timedelta(hours=3), timezone='Africa/Douala',
            location_address='Institut français, Avenue Charles de Gaulle, Douala', is_paid=True, price='15.00',
            max_guests=400, theme='La Lune, de la mythologie aux missions Artemis', dress_code='')
        self.other = Event.objects.create(
            organizer=self.orga, title='Soirée secrète', event_type='soiree', status='published', visibility='public',
            start_date=start, end_date=start + timedelta(hours=3), location_address='Adresse secrète, Yaoundé')
        Ticket.objects.create(event=self.event, user=self.paul, status='generated', price='15')
        self.conv = services.get_or_create(self.event, self.paul)

    def ask(self, text, user=None):
        with mock.patch('easevent.dispatch.dispatch'):
            msg = services.send(self.conv, user or self.paul, text)
        assistant.handle(msg)
        return msg

    def last_bot(self):
        return Message.objects.filter(conversation=self.conv, meta__assistant=True).order_by('-created_at').first()

    def test_sans_ia_regles_simples(self):
        with NO_AI:
            self.ask('Bonjour, où se trouve la salle ?')
        self.assertIn('Institut français', self.last_bot().body)
        with NO_AI:
            self.ask('Combien coûte la place ?')
        self.assertIn('15 €', self.last_bot().body)
        self.assertEqual(EventQuestion.objects.filter(status='auto').count(), 2)

    def test_reponse_ia_et_cache(self):
        with ai_says(True, 'La conférence commence à 18h, heure de Douala.') as run:
            self.ask('À quelle heure ça commence ?')
            self.ask('à quelle heure ça commence')                  # même question : cache
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.last_bot().body, 'La conférence commence à 18h, heure de Douala.')
        # Seules les informations de CET événement sont transmises au modèle
        prompt = run.call_args.args[2]
        self.assertIn('Astronomie', prompt)
        self.assertNotIn('Soirée secrète', prompt)
        self.assertNotIn('Adresse secrète', prompt)

    def test_question_inconnue_transmise_puis_apprise(self):
        with ai_says(False):
            self.ask('Y a-t-il un parking pour les voitures ?')
        self.assertIn('Nadia', self.last_bot().body)
        q = EventQuestion.objects.get(status='pending')
        n = Notification.objects.get(user=self.orga, type='question_to_answer')
        self.assertEqual(n.data['conversation_id'], str(self.conv.id))
        # L'organisateur répond : la question est résolue et sa réponse rejoint les connaissances
        services.send(self.conv, self.orga, 'Oui, un parking gratuit est ouvert derrière l’Institut.')
        q.refresh_from_db()
        self.assertEqual(q.status, 'answered')
        info = assistant.knowledge(self.event, self.claire)
        self.assertIn('parking gratuit', str(info['réponses_déjà_données_par_l_organisateur']))

    def test_pas_de_reponse_a_un_remerciement(self):
        with ai_says(False, is_question=False):
            self.ask('Merci beaucoup !')
        self.assertIsNone(self.last_bot())

    def test_desactive_lien_reserve_et_limite(self):
        self.event.is_online, self.event.online_link = True, 'https://meet.example.com/lune'
        self.event.save()
        info = assistant.knowledge(self.event, self.claire)                # pas de billet : lien caché
        self.assertNotIn('meet.example.com', str(info))
        self.assertIn('meet.example.com', str(assistant.knowledge(self.event, self.paul)))
        Event.objects.filter(pk=self.event.pk).update(assistant_enabled=False)
        self.conv.refresh_from_db()
        self.conv.event.refresh_from_db()
        with NO_AI:
            self.ask('Où est la salle ?')
        self.assertIsNone(self.last_bot())
        Event.objects.filter(pk=self.event.pk).update(assistant_enabled=True)
        self.conv.event.refresh_from_db()
        cache.set(f'assistant:rate:{self.conv.id}', assistant.PER_HOUR)
        with NO_AI:
            self.ask('Où est la salle ?')
        self.assertIsNone(self.last_bot())

    def test_envoi_declenche_la_tache_et_pas_pour_l_organisateur(self):
        with mock.patch('easevent.dispatch.dispatch') as d:
            services.send(self.conv, self.paul, 'Où est-ce ?')
            services.send(self.conv, self.orga, 'Bonjour Paul')
        self.assertEqual(d.call_count, 1)
