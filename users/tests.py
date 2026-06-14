"""
users/tests.py — Easevent
═══════════════════════════════════════════════════════════════
Tests unitaires de l'application "users".

Pour executer ces tests :
    python manage.py test users

Pour executer un test specifique :
    python manage.py test users.tests.UserModelTest
    python manage.py test users.tests.AuthAPITest
═══════════════════════════════════════════════════════════════
"""

from django.test import TestCase
from django.urls  import reverse
from django.utils import timezone

from rest_framework.test    import APIClient
from rest_framework         import status

from .models import User, EmailVerification


# ─────────────────────────────────────────────────────────────
# CLASSE 1 — Tests du modele User
# Ce qu'on teste : la creation, les proprietes, le soft delete
# ─────────────────────────────────────────────────────────────
class UserModelTest(TestCase):
    """
    Tests unitaires sur le modele User.
    On teste les comportements du modele directement en base,
    sans passer par l'API.
    """

    def setUp(self):
        """
        setUp() s'execute avant chaque test.
        On cree un utilisateur de reference pour les tests.
        """
        self.user = User.objects.create_user(
            email      = 'synthia@easevent.fr',
            password   = 'MotDePasse123',
            first_name = 'Synthia',
            last_name  = 'Donfack',
        )

    def test_creation_utilisateur(self):
        """
        Test 1 — Un utilisateur cree a les bonnes valeurs par defaut.
        On verifie que le plan est 'free' et que is_verified est False
        apres la creation, conformement aux specs du cahier des charges.
        """
        self.assertEqual(self.user.subscription_plan, 'free')
        self.assertFalse(self.user.is_verified)
        self.assertIsNone(self.user.deleted_at)

    def test_identifiant_est_email(self):
        """
        Test 2 — L'identifiant de connexion est l'email, pas un username.
        On verifie que USERNAME_FIELD est bien configure sur 'email'.
        """
        self.assertEqual(User.USERNAME_FIELD, 'email')

    def test_mot_de_passe_hache(self):
        """
        Test 3 — Le mot de passe est hache avec bcrypt.
        On verifie que le mot de passe n'est jamais stocke en clair.
        check_password() utilise bcrypt pour la comparaison.
        """
        self.assertTrue(self.user.check_password('MotDePasse123'))
        self.assertNotEqual(self.user.password, 'MotDePasse123')

    def test_cle_primaire_est_uuid(self):
        """
        Test 4 — La cle primaire est un UUID (non devinable).
        Securite : on ne peut pas deviner l'ID d'un utilisateur
        en incrementant un entier.
        """
        import uuid
        try:
            uuid.UUID(str(self.user.id))
            est_uuid_valide = True
        except ValueError:
            est_uuid_valide = False
        self.assertTrue(est_uuid_valide)

    def test_soft_delete_rgpd(self):
        """
        Test 5 — La suppression de compte est un soft delete (RGPD Art. 17).
        On verifie que deleted_at est renseigne apres suppression
        et que la ligne existe toujours en base (pas de DELETE SQL).
        """
        user_id = self.user.id
        self.user.deleted_at = timezone.now()
        self.user.save()

        # La ligne existe toujours en base
        user_en_base = User.objects.get(id=user_id)
        self.assertIsNotNone(user_en_base.deleted_at)
        self.assertTrue(user_en_base.is_deleted)

    def test_propriete_full_name(self):
        """
        Test 6 — La propriete full_name concatene prenom et nom.
        """
        self.assertEqual(self.user.full_name, 'Synthia Donfack')

    def test_email_unique(self):
        """
        Test 7 — Deux utilisateurs ne peuvent pas avoir le meme email.
        On s'attend a une exception IntegrityError si on essaie.
        """
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                email      = 'synthia@easevent.fr',  # meme email
                password   = 'AutreMotDePasse456',
                first_name = 'Autre',
                last_name  = 'Personne',
            )


# ─────────────────────────────────────────────────────────────
# CLASSE 2 — Tests de l'API d'authentification
# Ce qu'on teste : inscription, connexion, verification email
# ─────────────────────────────────────────────────────────────
class AuthAPITest(TestCase):
    """
    Tests d'integration sur les endpoints d'authentification.
    On utilise APIClient de DRF pour simuler des requetes HTTP.
    """

    def setUp(self):
        """
        Initialisation du client API avant chaque test.
        """
        self.client = APIClient()

    def test_inscription_succes(self):
        """
        Test 8 — L'inscription cree un compte et retourne des tokens JWT.
        Critere d'acceptation US-01 : le compte est cree avec is_verified=False.
        """
        payload = {
            'email':      'nouveau@easevent.fr',
            'password':   'MotDePasse123',
            'first_name': 'Nouveau',
            'last_name':  'Membre',
        }
        response = self.client.post('/api/auth/register/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('access',  response.data)
        self.assertIn('refresh', response.data)
        self.assertFalse(response.data['user']['is_verified'])

        # Verifier que le compte existe en base
        self.assertTrue(User.objects.filter(email='nouveau@easevent.fr').exists())

    def test_inscription_email_deja_utilise(self):
        """
        Test 9 — L'inscription echoue si l'email est deja utilise.
        Retourne 400 avec un message d'erreur.
        """
        User.objects.create_user(
            email='existant@easevent.fr',
            password='MotDePasse123',
            first_name='Existant',
            last_name='Utilisateur',
        )
        payload = {
            'email':      'existant@easevent.fr',
            'password':   'AutreMotDePasse456',
            'first_name': 'Autre',
            'last_name':  'Personne',
        }
        response = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_connexion_succes(self):
        """
        Test 10 — La connexion avec des identifiants corrects retourne des tokens JWT.
        """
        User.objects.create_user(
            email='membre@easevent.fr',
            password='MotDePasse123',
            first_name='Membre',
            last_name='Connecte',
            is_verified=True,
        )
        payload = {
            'email':    'membre@easevent.fr',
            'password': 'MotDePasse123',
        }
        response = self.client.post('/api/auth/login/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access',  response.data)
        self.assertIn('refresh', response.data)

    def test_connexion_mauvais_mot_de_passe(self):
        """
        Test 11 — La connexion echoue avec un mauvais mot de passe.
        Securite OWASP A07 : le message d'erreur est generique.
        """
        User.objects.create_user(
            email='membre@easevent.fr',
            password='MotDePasse123',
            first_name='Membre',
            last_name='Test',
        )
        payload = {
            'email':    'membre@easevent.fr',
            'password': 'MauvaisMotDePasse',
        }
        response = self.client.post('/api/auth/login/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_acces_me_sans_token(self):
        """
        Test 12 — L'endpoint /api/auth/me/ est protege.
        Sans token JWT, on doit recevoir 401 Unauthorized.
        Verifie que @permission_classes([IsAuthenticated]) fonctionne.
        """
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_acces_me_avec_token(self):
        """
        Test 13 — Avec un token JWT valide, /api/auth/me/ retourne les infos utilisateur.
        """
        from rest_framework_simplejwt.tokens import RefreshToken

        user = User.objects.create_user(
            email='connecte@easevent.fr',
            password='MotDePasse123',
            first_name='Connecte',
            last_name='Valide',
            is_verified=True,
        )
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(refresh.access_token)}')

        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['email'], 'connecte@easevent.fr')

    def test_suppression_compte_rgpd(self):
        """
        Test 14 — La suppression de compte anonymise les donnees (RGPD Art. 17).
        Apres suppression, email, prenom et nom sont anonymises.
        La ligne existe toujours en base (soft delete).
        """
        from rest_framework_simplejwt.tokens import RefreshToken

        user = User.objects.create_user(
            email='asupprimer@easevent.fr',
            password='MotDePasse123',
            first_name='A',
            last_name='Supprimer',
        )
        user_id = user.id
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(refresh.access_token)}')

        response = self.client.post(
            '/api/auth/delete-account/',
            {'password': 'MotDePasse123'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # La ligne existe toujours (soft delete)
        user_en_base = User.objects.get(id=user_id)
        self.assertIsNotNone(user_en_base.deleted_at)

        # Les donnees personnelles sont anonymisees
        self.assertIn('deleted_', user_en_base.email)
        self.assertEqual(user_en_base.first_name, 'Utilisateur')
