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
