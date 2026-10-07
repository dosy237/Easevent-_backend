import json
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event
from events.tests_lifecycle import client_for
from invitations.models import Invitation
from notifications.models import Notification
from users.models import User

from . import catalog, colors
from .models import MiniSiteGeneration, MiniSiteProposal

TMP = tempfile.mkdtemp()


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def openai_reply(obj):
    return FakeResponse(200, {'choices': [{'message': {'content': json.dumps(obj)}}]})


def gemini_reply(obj):
    return FakeResponse(200, {'candidates': [{'content': {'parts': [{'text': json.dumps(obj)}]}}]})


@override_settings(MINISITE_ASYNC=False, MINISITE_DATASET_DIR=TMP, GEMINI_API_KEY='', GROQ_API_KEY='',
                   OPENROUTER_API_KEY='', MISTRAL_API_KEY='')
class MiniSiteTest(TestCase):

    def setUp(self):
        cache.clear()
        mk = lambda e, f: User.objects.create_user(email=e, password='x', first_name=f, last_name='T', is_verified=True)
        self.orga, self.guest, self.stranger = mk('lea@x.fr', 'Léa'), mk('claire@x.fr', 'Claire'), mk('zoe@x.fr', 'Zoé')
        start = timezone.now() + timedelta(days=30)
        self.event = Event.objects.create(
            organizer=self.orga, title='Mariage de Léa & Tom', event_type='mariage', visibility='private', status='published',
            description='Nous nous marions ! Écrivez-nous à lea.tom@exemple.fr ou au 06 12 34 56 78.',
            start_date=start, end_date=start + timedelta(hours=10), location_address='Château de Versailles, 78000 Versailles, France',
            ambiance='elegant', palette={'primary': '#C4A882'}, dress_code='Chic', max_guests=120, theme='Bohème champêtre',
            template_config={'cover_image': 'https://img/c.jpg', 'gallery': ['https://img/1.jpg', 'https://img/2.jpg']})
        self.c = client_for(self.orga)

    def generate(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.post(f'/api/events/{self.event.id}/minisite/generate/', {}, format='json')
        self.assertEqual(r.status_code, 202, r.data)
        return self.c.get(f'/api/events/{self.event.id}/minisite/generation/').data

    # ── Génération sans aucune clé : 6 propositions quand même ─────────────
    def test_six_propositions_uniques_et_valides_sans_ia(self):
        data = self.generate()
        self.assertEqual(data['status'], 'done')
        props = data['proposals']
        self.assertEqual(len(props), 6)
        self.assertEqual(len({p['spec']['fingerprint'] for p in props}), 6)
        self.assertEqual(len({p['spec']['sections'][0]['variant'] for p in props}), 6)   # accueils tous différents
        for p in props:
            spec = p['spec']
            self.assertTrue(colors.check(spec['theme']['colors']))
            for alt in spec['theme']['alternatives'].values():
                self.assertTrue(colors.check(alt))
            self.assertEqual(spec['sections'][0]['kind'], 'hero')
            self.assertEqual(spec['sections'][-1]['kind'], 'footer')
            self.assertIn('cta', [s['kind'] for s in spec['sections']])
            for s in spec['sections']:
                self.assertIn(s['variant'], catalog.SECTIONS[s['kind']]['variants'])
            blob = json.dumps(spec, ensure_ascii=False)
            self.assertNotIn('lea.tom@exemple.fr', blob)
            self.assertNotIn('06 12 34 56 78', blob)
        gen = MiniSiteGeneration.objects.get(pk=data['id'])
        self.assertFalse(gen.engine['ai_direction'])
        self.assertTrue(Notification.objects.filter(user=self.orga, type='minisite_ready').exists())

    def test_faq_uniquement_avec_les_vraies_donnees(self):
        props = self.generate()['proposals']
        faq = next(s for p in props for k, s in p['spec']['copy'].items() if k == 'faq')
        answers = ' '.join(i['a'] for i in faq['items'])
        self.assertIn('Chic', answers)
        self.assertIn('120', answers)
        self.assertIn('gratuite', answers)

    def test_dispositions_jamais_repetees_entre_generations(self):
        a = {p['spec']['fingerprint'] for p in self.generate()['proposals']}
        b = {p['spec']['fingerprint'] for p in self.generate()['proposals']}
        self.assertEqual(len(a | b), 12)

    # ── Plusieurs modèles, rôles séparés ──────────────────────────────────
    @override_settings(GEMINI_API_KEY='g', GROQ_API_KEY='q', MISTRAL_API_KEY='m', OPENROUTER_API_KEY='',
                       MINISITE_ROLES={'direction': ('mistral', 'gemini'), 'copy': ('mistral', 'gemini', 'groq'),
                                       'review': ('groq', 'gemini'), 'critic': ('groq', 'gemini')})
    def test_roles_et_mistral_sans_donnees_sensibles(self):
        art = {'proposals': [{'direction': d, 'font_pair': 'cormorant-lora' if d == 'luxe' else 'syne-inter',
                              'harmony': 'gold' if d == 'luxe' else 'vivid', 'hero': 'arch' if d == 'luxe' else 'poster',
                              'radius': 'sharp', 'density': 'airy', 'ornament': 'stars', 'mood': 'doré et solennel'}
                             for d in catalog.DIRECTION_ORDER]}
        texts = {'proposals': [{'direction': d, 'hero': {'kicker': f'Accroche {d}', 'subtitle': 'Un jour unique'},
                                'intro': {'title': 'Notre histoire', 'body': 'Rendez-vous à 19h30 pour le dîner.'},
                                'cta': {'title': 'Venez fêter avec nous', 'label': 'Je viens'}} for d in catalog.DIRECTION_ORDER]}
        calls = []

        def fake_post(url, json=None, timeout=None, headers=None):
            calls.append((url, json))
            if 'mistral' in url:
                return openai_reply(art)
            if 'googleapis' in url:
                return gemini_reply(texts)
            return openai_reply(texts)          # relecture (Groq)

        with mock.patch('minisite.ai.requests.post', side_effect=fake_post):
            props = self.generate()['proposals']
        mistral = [body for url, body in calls if 'mistral' in url]
        self.assertNotIn('Bohème', json.dumps(mistral, ensure_ascii=False))
        self.assertEqual(len(mistral), 1)
        sent = json.dumps(mistral[0], ensure_ascii=False)
        for secret in ('Mariage de Léa', 'Versailles', 'Nous nous marions', 'Léa'):
            self.assertNotIn(secret, sent)                      # Mistral : aucune donnée saisie
        self.assertTrue(any('googleapis' in u for u, _ in calls))
        self.assertTrue(any('groq' in u for u, _ in calls))     # relecture par un autre modèle
        luxe = next(p for p in props if p['direction'] == 'luxe')['spec']
        self.assertEqual(luxe['theme']['fonts'], 'cormorant-lora')
        self.assertEqual(luxe['theme']['harmony'], 'gold')
        self.assertEqual(luxe['copy']['hero']['kicker'], 'Accroche luxe')
        self.assertEqual(luxe['mood'], 'doré et solennel')
        # « 19h30 » n'est pas dans les données : le texte inventé est rejeté
        self.assertNotIn('19h30', json.dumps([p['spec']['copy'] for p in props], ensure_ascii=False))
        gen = MiniSiteGeneration.objects.latest('created_at')
        self.assertTrue(gen.engine['ai_direction'] and gen.engine['ai_copy'])

    @override_settings(GEMINI_API_KEY='g', GROQ_API_KEY='q')
    def test_repli_si_un_modele_echoue(self):
        texts = {'proposals': [{'direction': d, 'hero': {'kicker': 'Par Groq'}} for d in catalog.DIRECTION_ORDER]}

        def fake_post(url, json=None, timeout=None, headers=None):
            if 'googleapis' in url:
                return FakeResponse(429, {})
            return openai_reply(texts)

        with mock.patch('minisite.ai.requests.post', side_effect=fake_post):
            props = self.generate()['proposals']
        self.assertTrue(all(p['spec']['copy']['hero']['kicker'] == 'Par Groq' for p in props))
        calls = MiniSiteGeneration.objects.latest('created_at').engine['calls']
        self.assertTrue(any(c['provider'] == 'gemini' and not c['ok'] for c in calls))

    # ── Quota de générations ──────────────────────────────────────────────
    def test_quota_generations_plan_gratuit(self):
        for _ in range(3):
            self.generate()
        r = self.c.post(f'/api/events/{self.event.id}/minisite/generate/', {}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'plan_limit')
        self.orga.subscription_plan = 'pro'
        self.orga.save()
        self.assertEqual(self.generate()['status'], 'done')

    def test_echec_non_decompte(self):
        with mock.patch('minisite.composer.compose_batch', side_effect=RuntimeError):
            data = self.generate()
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['quota']['used'], 0)

    # ── Choix, accès, retouches ───────────────────────────────────────────
    def choose_first(self):
        props = self.generate()['proposals']
        r = self.c.post(f'/api/events/{self.event.id}/minisite/choose/', {'proposal_id': props[2]['id']}, format='json')
        self.assertEqual(r.status_code, 200)
        return props[2]

    def test_acces_au_mini_site(self):
        url = f'/api/events/{self.event.id}/minisite/'
        self.assertEqual(self.c.get(url).status_code, 404)                    # rien de choisi
        chosen = self.choose_first()
        self.assertEqual(self.c.get(url).data['spec']['fingerprint'], chosen['spec']['fingerprint'])
        self.assertEqual(client_for().get(url).status_code, 404)              # privé : visiteur refusé
        self.assertEqual(client_for(self.stranger).get(url).status_code, 404)  # non invité refusé
        Invitation.objects.create(event=self.event, invited_user=self.guest, token='t' * 40, status='sent',
                                  channel='platform_notification', expires_at=timezone.now() + timedelta(days=60))
        r = client_for(self.guest).get(url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['event']['has_minisite'])
        self.assertFalse(r.data['is_organizer'])
        self.event.refresh_from_db()
        self.event.visibility = 'public'
        self.event.save()
        self.assertEqual(client_for().get(url).status_code, 200)               # public : visible dans l'app
        self.event.status = 'draft'
        self.event.save()
        self.assertEqual(client_for(self.guest).get(url).status_code, 404)     # brouillon : organisateur seul
        self.assertEqual(self.c.get(url).status_code, 200)

    def test_seul_l_organisateur_genere_et_choisit(self):
        other = client_for(self.stranger)
        self.assertEqual(other.post(f'/api/events/{self.event.id}/minisite/generate/', {}, format='json').status_code, 404)
        props = self.generate()['proposals']
        self.assertEqual(other.post(f'/api/events/{self.event.id}/minisite/choose/', {'proposal_id': props[0]['id']},
                                    format='json').status_code, 404)

    def test_retouches(self):
        chosen = self.choose_first()['spec']
        url = f'/api/events/{self.event.id}/minisite/'
        alt = next(iter(chosen['theme']['alternatives']))
        r = self.c.patch(url, {'harmony': alt, 'fonts': 'lora-inter'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['spec']['theme']['harmony'], alt)
        self.assertIn(chosen['theme']['harmony'], r.data['spec']['theme']['alternatives'])
        kinds = [s['kind'] for s in chosen['sections']]
        middle = kinds[1:-1][::-1]
        self.assertEqual(self.c.patch(url, {'order': ['hero'] + middle + ['footer']}, format='json').status_code, 200)
        self.assertEqual(self.c.patch(url, {'order': middle + ['hero', 'footer']}, format='json').status_code, 400)
        self.assertEqual(self.c.patch(url, {'hidden': ['cta']}, format='json').status_code, 400)
        self.assertEqual(self.c.patch(url, {'copy': {'hero': {'kicker': 'x' * 200}}}, format='json').status_code, 400)
        r = self.c.patch(url, {'copy': {'hero': {'kicker': 'Le plus beau jour'}}}, format='json')
        self.assertEqual(r.data['spec']['copy']['hero']['kicker'], 'Le plus beau jour')
        self.assertEqual(client_for(self.stranger).patch(url, {'fonts': 'lora-inter'}, format='json').status_code, 404)

    def test_banniere(self):
        chosen = self.choose_first()['spec']
        hero = chosen['sections'][0]
        self.assertIn(hero['filter'], catalog.HERO_FILTERS)
        self.assertIn(hero['tint'], catalog.BANNER_TINTS_BY_TYPE['mariage'])        # teintes de célébration
        url = f'/api/events/{self.event.id}/minisite/'
        own = f'https://res.cloudinary.com/demo/image/upload/v1/easevent/events/{self.orga.id}/banner_ab12.jpg'
        with mock.patch('cloudinary.config', return_value=mock.Mock(cloud_name='demo')):
            r = self.c.patch(url, {'banner': {'image': own, 'filter': 'glass', 'tint': 'blue', 'variant': 'fullbleed'}}, format='json')
            self.assertEqual(r.status_code, 200, r.data)
            h = r.data['spec']['sections'][0]
            self.assertEqual((h['image'], h['filter'], h['tint'], h['variant']), (own, 'glass', 'blue', 'fullbleed'))
            # Photo d'un autre compte, adresse extérieure, valeurs inconnues : refusées
            other = own.replace(str(self.orga.id), str(self.stranger.id))
            for bad in ({'image': other}, {'image': 'https://evil.example/x.jpg'}, {'filter': 'sepia'}, {'tint': 'vert'},
                        {'variant': 'nope'}, {}, 'x'):
                self.assertEqual(self.c.patch(url, {'banner': bad}, format='json').status_code, 400, bad)
            r = self.c.patch(url, {'banner': {'image': None}}, format='json')
            self.assertNotIn('image', r.data['spec']['sections'][0])
        with mock.patch('cloudinary.config', return_value=mock.Mock(cloud_name='')):
            self.assertEqual(self.c.patch(url, {'banner': {'image': own}}, format='json').status_code, 400)

    def test_journal_d_apprentissage(self):
        self.choose_first()
        lines = []
        for name in os.listdir(TMP):
            with open(os.path.join(TMP, name), encoding='utf-8') as fh:
                lines += [json.loads(l) for l in fh if l.strip()]
        kinds = {l['type'] for l in lines}
        self.assertTrue({'generation', 'choice'} <= kinds)
        blob = json.dumps(lines, ensure_ascii=False)
        self.assertNotIn('lea.tom@exemple.fr', blob)
        self.assertNotIn('06 12 34 56 78', blob)
        self.assertNotIn('lea@x.fr', blob)


    # ── Directeur de création : corrections validées ─────────────────────
    @override_settings(GEMINI_API_KEY='g', MINISITE_ROLES={'direction': (), 'copy': (), 'review': (), 'critic': ('gemini',)})
    def test_critique_appliquee_et_controlee(self):
        review = {'proposals': [{
            'direction': d, 'scores': {'hierarchy': 8, 'color': 7, 'typography': 9, 'rhythm': 6, 'copy': 7, 'distinctiveness': 8},
            'verdict': 'Bonne base, le bouton arrive trop tard.',
            'actions': [
                {'type': 'move', 'kind': 'cta', 'before': 'details'},
                {'type': 'harmony', 'value': 'gold'},
                {'type': 'fonts', 'value': 'cormorant-lora'},
                {'type': 'variant', 'kind': 'hero', 'value': 'inexistante'},            # ignorée
                {'type': 'move', 'kind': 'hero', 'before': 'footer'},                  # ignorée : accueil fixe
                {'type': 'copy', 'kind': 'hero', 'field': 'kicker', 'value': 'Un moment inoubliable'},  # charte : refusée
            ]} for d in catalog.DIRECTION_ORDER]}
        with mock.patch('minisite.ai.requests.post', return_value=gemini_reply(review)):
            props = self.generate()['proposals']
        self.assertEqual(len({p['spec']['fingerprint'] for p in props}), 6)
        self.assertEqual(len({p['spec']['theme']['fonts'] for p in props}), 6)     # typographies toujours distinctes
        for p in props:
            spec = p['spec']
            kinds = [s['kind'] for s in spec['sections']]
            self.assertEqual(kinds[0], 'hero')
            self.assertEqual(kinds[-1], 'footer')
            self.assertLess(kinds.index('cta'), kinds.index('details'))
            self.assertEqual(spec['theme']['harmony'], 'gold')
            self.assertTrue(colors.check(spec['theme']['colors']))
            self.assertGreaterEqual(len(spec['critique']['applied']), 1)
            self.assertNotIn('inoubliable', spec['copy']['hero'].get('kicker', ''))
            self.assertEqual(spec['critique']['scores']['typography'], 9)

    def test_formules_interdites_rejetees(self):
        from .copy import clean_text
        f = {'time_text': '', 'end_time_text': '', 'price_text': '', 'description': '', 'title': ''}
        for bad in ('Plus qu’un mariage, une promesse', 'Un moment inoubliable', 'N’hésitez pas à venir',
                    'Ce n’est pas une fête, mais une aventure', 'Venez !!', 'On danse 🎉'):
            self.assertIsNone(clean_text(bad, 140, f), bad)
        self.assertEqual(clean_text('Sous les tilleuls', 40, f), 'Sous les tilleuls')
        body = "Découvrez les algorithmes qui allègent nos villes. Pas de jargon complexe, uniquement des démonstrations réelles. Venez voir."
        self.assertEqual(clean_text(body, 600, f), 'Découvrez les algorithmes qui allègent nos villes. Venez voir.')
