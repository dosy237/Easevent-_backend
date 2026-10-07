"""
events/tests.py — Easevent
═══════════════════════════════════════════════════════════════
Tests unitaires de l'application "events".

Pour executer ces tests :
    python manage.py test events

Pour executer un test specifique :
    python manage.py test events.tests.EventModelTest
    python manage.py test events.tests.EventAPITest
═══════════════════════════════════════════════════════════════
"""

from django.test import TestCase
from django.utils import timezone

from rest_framework.test import APIClient
from rest_framework      import status
from rest_framework_simplejwt.tokens import RefreshToken

from datetime import timedelta

from users.models  import User
from events.models import Event


# ─────────────────────────────────────────────────────────────
# CLASSE 1 — Tests du modele Event
# ─────────────────────────────────────────────────────────────
class EventModelTest(TestCase):
    """
    Tests unitaires sur le modele Event.
    On verifie les valeurs par defaut, les contraintes et
    les comportements du modele.
    """

    def setUp(self):
        """
        Cree un utilisateur et un evenement de reference
        avant chaque test.
        """
        self.organisateur = User.objects.create_user(
            email      = 'organisateur@easevent.fr',
            password   = 'MotDePasse123',
            first_name = 'Marie',
            last_name  = 'Organisatrice',
        )
        self.event = Event.objects.create(
            organizer        = self.organisateur,
            title            = 'Conférence Tech Paris 2026',
            event_type       = 'conference',
            description      = 'Une conférence sur le développement logiciel.',
            start_date       = timezone.now() + timedelta(days=30),
            end_date         = timezone.now() + timedelta(days=30, hours=4),
            location_address = '10 Rue de Rivoli, Paris',
        )

    def test_creation_evenement_statut_par_defaut(self):
        """
        Test 1 — Un evenement cree est en statut 'draft' et visibilite 'draft'.
        Il ne doit jamais etre visible publiquement avant publication explicite.
        """
        self.assertEqual(self.event.status,     'draft')
        self.assertEqual(self.event.visibility, 'draft')

    def test_cle_primaire_est_uuid(self):
        """
        Test 2 — La cle primaire de l'evenement est un UUID.
        Securite : un attaquant ne peut pas deviner les IDs
        en incrementant un entier.
        """
        import uuid
        try:
            uuid.UUID(str(self.event.id))
            est_uuid_valide = True
        except ValueError:
            est_uuid_valide = False
        self.assertTrue(est_uuid_valide)

    def test_soft_delete_evenement(self):
        """
        Test 3 — La suppression d'un evenement est un soft delete.
        deleted_at est renseigne mais la ligne reste en base.
        Les evenements passes restent accessibles avec "Organisateur supprime".
        """
        event_id = self.event.id
        self.event.deleted_at = timezone.now()
        self.event.save()

        event_en_base = Event.objects.get(id=event_id)
        self.assertIsNotNone(event_en_base.deleted_at)

    def test_relation_organisateur(self):
        """
        Test 4 — L'evenement est correctement lie a son organisateur.
        On verifie la relation ForeignKey User -> Event.
        """
        self.assertEqual(self.event.organizer.email, 'organisateur@easevent.fr')

    def test_titre_max_100_caracteres(self):
        """
        Test 5 — Le titre est limite a 100 caracteres (contrainte du modele).
        Conforme a la specification US-10 du cahier des charges.
        """
        max_length = Event._meta.get_field('title').max_length
        self.assertEqual(max_length, 100)

    def test_timestamps_automatiques(self):
        """
        Test 6 — created_at et updated_at sont remplis automatiquement.
        On verifie que ces champs ne sont pas null apres creation.
        """
        self.assertIsNotNone(self.event.created_at)
        self.assertIsNotNone(self.event.updated_at)


# ─────────────────────────────────────────────────────────────
# CLASSE 2 — Tests de l'API Events
# ─────────────────────────────────────────────────────────────
class EventAPITest(TestCase):
    """
    Tests d'integration sur les endpoints de l'API events.
    On verifie la protection des routes, la creation et
    la visibilite des evenements.
    """

    def setUp(self):
        """
        Cree un utilisateur authentifie et configure le client API.
        """
        self.client = APIClient()

        self.user = User.objects.create_user(
            email      = 'organisateur@easevent.fr',
            password   = 'MotDePasse123',
            first_name = 'Marie',
            last_name  = 'Organisatrice',
            is_verified = True,
        )

        # Generer un token JWT pour l'utilisateur
        refresh = RefreshToken.for_user(self.user)
        self.access_token = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')

        # Creer un evenement de reference
        self.event = Event.objects.create(
            organizer        = self.user,
            title            = 'Conférence Test',
            event_type       = 'conference',
            description      = 'Description de test.',
            start_date       = timezone.now() + timedelta(days=10),
            end_date         = timezone.now() + timedelta(days=10, hours=2),
            location_address = 'Paris',
            status           = 'published',
            visibility       = 'public',
        )

    def test_creation_evenement_authentifie(self):
        """
        Test 7 — Un utilisateur authentifie peut creer un evenement.
        On verifie que POST /api/events/create/ retourne 201.
        """
        payload = {
            'title':            'Nouveau Evenement',
            'event_type':       'soiree',
            'description':      'Une soiree test.',
            'start_date':       (timezone.now() + timedelta(days=20)).isoformat(),
            'end_date':         (timezone.now() + timedelta(days=20, hours=3)).isoformat(),
            'location_address': 'Lyon',
            'visibility':       'public',
        }
        response = self.client.post('/api/events/create/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_creation_evenement_non_authentifie(self):
        """
        Test 8 — Un utilisateur non authentifie ne peut pas creer un evenement.
        On retire le token et on verifie que l'API retourne 401.
        Verifie que @permission_classes([IsAuthenticated]) protege la route.
        """
        self.client.credentials()  # Retire le token
        payload = {
            'title':      'Tentative sans token',
            'event_type': 'conference',
        }
        response = self.client.post('/api/events/create/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_liste_evenements_publics_sans_authentification(self):
        """
        Test 9 — Les evenements publics sont accessibles sans authentification.
        GET /api/events/publics/ doit retourner 200 meme sans token.
        Conforme a US-06 : le fil est visible pour les visiteurs non connectes.
        """
        self.client.credentials()  # Retire le token
        response = self.client.get('/api/events/publics/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_evenement_prive_invisible_publiquement(self):
        """
        Test 10 — Un evenement prive n'apparait pas dans le fil public.
        Securite : un evenement prive ne doit jamais fuiter dans le fil.
        Conforme a la regle de visibilite 2 du cahier des charges.
        """
        Event.objects.create(
            organizer        = self.user,
            title            = 'Evenement Prive Secret',
            event_type       = 'mariage',
            description      = 'Evenement prive.',
            start_date       = timezone.now() + timedelta(days=5),
            end_date         = timezone.now() + timedelta(days=5, hours=4),
            location_address = 'Adresse Privee',
            status           = 'published',
            visibility       = 'private',
        )
        self.client.credentials()  # Visiteur non connecte
        response = self.client.get('/api/events/publics/')

        if response.status_code == 200:
            titres = [e.get('title', '') for e in response.data.get('events', [])]
            self.assertNotIn('Evenement Prive Secret', titres)

    def test_mes_evenements_authentifie(self):
        """
        Test 11 — Un utilisateur authentifie voit ses evenements.
        GET /api/events/mes-evenements/ retourne les evenements de l'utilisateur.
        """
        response = self.client.get('/api/events/mes-evenements/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titres = [e.get('title', '') for e in response.data.get('events', [])]
        self.assertIn('Conférence Test', titres)

    def test_mes_evenements_sans_authentification(self):
        """
        Test 12 — Sans token, /api/events/mes-evenements/ retourne 401.
        Un visiteur ne peut pas voir les evenements d'un utilisateur.
        """
        self.client.credentials()
        response = self.client.get('/api/events/mes-evenements/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ─────────────────────────────────────────────────────────────
# CLASSE 3 — Billetterie & dress code (M23)
# ─────────────────────────────────────────────────────────────
from django.core.cache import cache


class TicketingFieldsTest(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(email='orga@easevent.fr', password='x', first_name='O', last_name='R')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')
        start = timezone.now() + timedelta(days=10)
        self.base = {
            'title': 'Summit Innovation AI', 'event_type': 'conference', 'description': 'd',
            'start_date': start.strftime('%Y-%m-%dT%H:%M:%S'),
            'end_date': (start + timedelta(hours=8)).strftime('%Y-%m-%dT%H:%M:%S'),
            'location_address': 'Station F', 'visibility': 'public',
        }

    def _create(self, **extra):
        return self.client.post('/api/events/create/', {**self.base, **extra}, format='json')

    def test_evenement_payant(self):
        r = self._create(is_paid=True, price='25,00', currency='EUR', max_guests=250,
                         dress_code='Business — veste conseillée')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        e = r.data['event']
        self.assertTrue(e['is_paid'])
        self.assertEqual(e['price'], '25.00')
        self.assertEqual(e['max_guests'], 250)
        self.assertEqual(e['dress_code'], 'Business — veste conseillée')

    def test_evenement_gratuit_prix_zero(self):
        r = self._create(is_paid=False, price='40')
        self.assertEqual(r.data['event']['price'], '0.00')
        self.assertFalse(r.data['event']['is_paid'])

    def test_payant_sans_prix_refuse(self):
        r = self._create(is_paid=True, price='')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('price', r.data)

    def test_valeurs_invalides(self):
        self.assertEqual(self._create(is_paid=True, price='-3').status_code, 400)
        self.assertEqual(self._create(max_guests=0).status_code, 400)
        self.assertEqual(self._create(dress_code='x' * 81).status_code, 400)
        self.assertEqual(self._create(dress_code='<b>chic</b>').status_code, 400)
        self.assertEqual(self._create(visibility='secret').status_code, 400)

    def test_compatibilite_template_config(self):
        r = self._create(template_config={'is_paid': True, 'price': 12.5, 'max_guests': 40})
        self.assertEqual(r.data['event']['price'], '12.50')
        self.assertEqual(r.data['event']['max_guests'], 40)

    def test_modification_partielle_conserve_le_prix(self):
        event_id = self._create(is_paid=True, price='25').data['event']['id']
        r = self.client.patch(f'/api/events/{event_id}/update/', {'dress_code': 'Tenue de soirée'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['event']['price'], '25.00')
        self.assertEqual(r.data['event']['dress_code'], 'Tenue de soirée')
        r = self.client.patch(f'/api/events/{event_id}/update/', {'is_paid': False}, format='json')
        self.assertEqual(r.data['event']['price'], '0.00')

    def test_passer_public_prive_et_inverse(self):
        event_id = self._create(template_config={'gallery': ['a.jpg']}).data['event']['id']
        self.client.post(f'/api/events/{event_id}/publish/', {}, format='json')
        r = self.client.patch(f'/api/events/{event_id}/update/', {'visibility': 'private'}, format='json')
        self.assertEqual(r.data['event']['visibility'], 'private')
        self.assertEqual(r.data['event']['status'], 'published')       # reste publié
        in_feed = lambda: any(str(e['id']) == event_id for e in self.client.get('/api/events/publics/').data['events'])
        self.assertFalse(in_feed())
        r = self.client.patch(f'/api/events/{event_id}/update/', {'visibility': 'public'}, format='json')
        self.assertEqual(r.data['event']['visibility'], 'public')
        self.assertTrue(in_feed())
        detail = self.client.get(f'/api/events/{event_id}/detail/').data['event']
        self.assertEqual(detail['template_config']['gallery'], ['a.jpg'])

    def test_titre_vide_et_visibilite_invalide_refuses(self):
        event_id = self._create().data['event']['id']
        self.assertEqual(self.client.patch(f'/api/events/{event_id}/update/', {'title': '  '}, format='json').status_code, 400)
        self.assertEqual(self.client.post(f'/api/events/{event_id}/publish/', {'visibility': 'secret'}, format='json').status_code, 400)

    def test_seul_l_organisateur_modifie(self):
        event_id = self._create().data['event']['id']
        other = User.objects.create_user(email='autre@easevent.fr', password='x', first_name='A', last_name='B')
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(other).access_token}')
        self.assertEqual(c.patch(f'/api/events/{event_id}/update/', {'visibility': 'private'}, format='json').status_code, 404)


class StyleFieldsTest(TicketingFieldsTest):
    """Type libre (« Autre ») et palette de couleurs libre."""

    def test_type_personnalise(self):
        r = self._create(event_type='autre', event_type_label='Baptême')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['event']['event_type_label'], 'Baptême')
        self.assertEqual(r.data['event']['event_type_display'], 'Baptême')

    def test_autre_sans_nom_refuse(self):
        self.assertEqual(self._create(event_type='autre').status_code, 400)

    def test_type_predefini_ignore_le_libelle(self):
        r = self._create(event_type='gala', event_type_label='Truc')
        self.assertEqual(r.data['event']['event_type_label'], '')
        self.assertEqual(r.data['event']['event_type_display'], 'Gala')

    def test_palette_libre(self):
        r = self._create(palette={'primary': '#ff00aa', 'secondary': '#123456'})
        self.assertEqual(r.data['event']['palette'], {'primary': '#FF00AA', 'secondary': '#123456'})

    def test_palette_invalide(self):
        self.assertEqual(self._create(palette={'primary': 'red'}).status_code, 400)
        self.assertEqual(self._create(palette={'primary': 'url(javascript:x)'}).status_code, 400)


class CustomAmbianceAndDemoCoversTest(TicketingFieldsTest):

    def test_ambiance_personnalisee(self):
        r = self._create(ambiance='autre', ambiance_label='Bohème')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['event']['ambiance_label'], 'Bohème')
        self.assertEqual(self._create(ambiance='autre').status_code, 400)
        self.assertEqual(self._create(ambiance='inconnue').status_code, 400)

    def test_couverture_coherente(self):
        from events.demo_covers import cover_for
        self.assertRegex(cover_for('conference', 'Conférence Tech : Django Masterclass'), r'/photos/(conference|seminaire)-')
        self.assertIn('/photos/tech-', cover_for('exposition', "Exposition d'Art : Digital Art"))
        self.assertIn('/photos/cuisine-', cover_for('atelier', 'Masterclass Cuisine'))
        self.assertIn('/photos/mariage-', cover_for('mariage', 'Sarah & Marc'))
        self.assertRegex(cover_for('anniversaire', 'Les 30 ans'), r'/static/app/covers/anniversaire-[123]\.jpg$')

    def test_commande_corrige_les_evenements_de_demo(self):
        from django.core.management import call_command
        demo = Event.objects.create(organizer=self.user, title='Live Concert : Afro-Jazz Night', event_type='concert',
                                    start_date=timezone.now(), end_date=timezone.now() + timedelta(hours=2),
                                    cover_image='events/seed_3_chef.png')
        upload = Event.objects.create(organizer=self.user, title='Mon mariage', event_type='mariage',
                                      start_date=timezone.now(), end_date=timezone.now() + timedelta(hours=2),
                                      cover_image='https://res.cloudinary.com/x/photo.jpg')
        call_command('fix_demo_covers', stdout=open('/dev/null', 'w'))
        demo.refresh_from_db(); upload.refresh_from_db()
        self.assertEqual(demo.cover_image, '/static/app/covers/photos/concert-stade.jpg')
        self.assertEqual(upload.cover_image, 'https://res.cloudinary.com/x/photo.jpg')

    def test_url_absolue_de_la_couverture_statique(self):
        from easevent.media import public_url
        with self.settings(PUBLIC_BASE_URL='https://easevent.example.com'):
            self.assertEqual(public_url('/static/app/covers/gala-1.jpg'),
                             'https://easevent.example.com/static/app/covers/gala-1.jpg')


class GeoTest(TestCase):
    """Recherche d'adresse (Photon par défaut, Google si clé) et carte du lieu."""

    def setUp(self):
        from django.core.cache import cache
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(email='geo@x.fr', password='x', first_name='G', last_name='O', is_verified=True)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')

    def test_recherche_photon(self):
        from unittest import mock

        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {'features': [{'geometry': {'coordinates': [2.3711, 48.8341]}, 'properties': {
                    'name': 'Station F', 'street': 'Parvis Alan Turing', 'housenumber': '5', 'postcode': '75013',
                    'city': 'Paris', 'country': 'France', 'osm_type': 'W', 'osm_id': 1}}]}
        with mock.patch('events.geo.requests.get', return_value=R()):
            res = self.client.get('/api/geo/search/', {'q': 'station f'}).data
        self.assertEqual(res['provider'], 'osm')
        self.assertEqual(res['results'][0]['address'], 'Station F, 5 Parvis Alan Turing, 75013 Paris, France')
        self.assertEqual((res['results'][0]['lat'], res['results'][0]['lng']), (48.8341, 2.3711))
        self.assertEqual(self.client.get('/api/geo/search/', {'q': 'ab'}).data['results'], [])

    def test_carte_osm_mise_en_cache(self):
        import io
        import tempfile
        from unittest import mock
        from PIL import Image
        buf = io.BytesIO(); Image.new('RGB', (256, 256), (220, 220, 220)).save(buf, 'PNG')

        class Tile:
            content = buf.getvalue()
            def raise_for_status(self): pass
        with tempfile.TemporaryDirectory() as tmp, self.settings(MEDIA_ROOT=tmp), \
                mock.patch('requests.Session.get', return_value=Tile()) as get:
            r = self.client.get('/api/geo/static-map/', {'lat': '48.8341', 'lng': '2.3711'})
            self.assertEqual((r.status_code, r['Content-Type']), (200, 'image/png'))
            calls = get.call_count
            self.client.get('/api/geo/static-map/', {'lat': '48.8341', 'lng': '2.3711'})
            self.assertEqual(get.call_count, calls)                     # 2e fois : fichier en cache
        self.assertEqual(self.client.get('/api/geo/static-map/', {'lat': '999', 'lng': '2'}).status_code, 404)

    def test_carte_dans_le_detail(self):
        from django.utils import timezone
        from datetime import timedelta
        start = timezone.now() + timedelta(days=3)
        e = Event.objects.create(organizer=self.user, title='Gala', event_type='gala', start_date=start,
                                 end_date=start + timedelta(hours=3), status='published', visibility='public',
                                 location_address='Station F, Paris', latitude=48.8341, longitude=2.3711)
        body = self.client.get(f'/api/events/publics/{e.id}/').data
        data = body.get('event', body)
        self.assertIn('destination=48.8341', data['map']['directions'])
        self.assertIn('/api/geo/static-map/', data['map']['image'])
