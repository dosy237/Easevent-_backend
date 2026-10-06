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
        Test 8 — L'inscription cree un compte non verifie (US-01, M01).
        Aucun jeton de session n'est renvoye tant que l'email n'est pas
        verifie : l'application affiche l'ecran M03.
        """
        payload = {
            'email':            'nouveau@easevent.fr',
            'password':         'MotDePasse123',
            'first_name':       'Nouveau',
            'last_name':        'Membre',
            'accepted_privacy': True,
            'marketing_opt_in': True,
            'phone_number':     '+33 6 11 22 33 44',
        }
        response = self.client.post('/api/auth/register/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['requires_verification'])
        self.assertNotIn('access', response.data)
        self.assertFalse(response.data['user']['is_verified'])

        user = User.objects.get(email='nouveau@easevent.fr')
        self.assertIsNotNone(user.accepted_privacy_at)
        self.assertTrue(user.marketing_opt_in)

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
            'accepted_privacy': True,
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


# ─────────────────────────────────────────────────────────────
# CLASSE 3 — Parcours de compte du MVP (M01 → M04)
# ─────────────────────────────────────────────────────────────
from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from .tokens import create_email_verification, hash_token
from .models import EmailVerification


@override_settings(FRONTEND_URL='https://app.easevent.test')
class AccountFlowTest(TestCase):

    def setUp(self):
        cache.clear()   # remet les compteurs de limitation a zero
        self.client = APIClient()

    def _register(self, **overrides):
        payload = {
            'email': 'sarah@easevent.fr', 'password': 'Jardin-Emeraude-26',
            'first_name': 'Sarah', 'last_name': 'Martin', 'accepted_privacy': True,
            'phone_number': '+33 6 11 22 33 44',
        }
        payload.update(overrides)
        return self.client.post('/api/auth/register/', payload, format='json')

    # ── M01 : consentement obligatoire ─────────────────────────
    def test_inscription_refusee_sans_consentement(self):
        response = self._register(accepted_privacy=False)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('accepted_privacy', response.data)
        self.assertFalse(User.objects.filter(email='sarah@easevent.fr').exists())

    def test_inscription_refuse_mot_de_passe_faible(self):
        response = self._register(password='12345678')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.data)

    def test_inscription_refuse_balises_dans_le_nom(self):
        response = self._register(first_name='<script>')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inscription_envoie_un_lien_vers_l_application(self):
        self._register()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://app.easevent.test/verify/', mail.outbox[0].body)

    # ── Code de vérification : email ou SMS au choix ──────────
    def _code_from_mail(self):
        import re
        return re.search(r'code de vérification : (\d{6})', mail.outbox[-1].body).group(1)

    def test_numero_obligatoire(self):
        response = self._register(phone_number='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('phone_number', response.data)

    def test_code_par_email_active_le_compte(self):
        r = self._register()
        self.assertEqual(r.data['channel'], 'email')
        code = self._code_from_mail()
        self.assertIn(code, mail.outbox[-1].subject)
        bad = self.client.post('/api/auth/verify-code/', {'email': 'sarah@easevent.fr', 'code': '000000' if code != '000000' else '111111'}, format='json')
        self.assertEqual(bad.data['code'], 'wrong_code')
        r = self.client.post('/api/auth/verify-code/', {'email': 'sarah@easevent.fr', 'code': code}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('access', r.data)
        self.assertTrue(User.objects.get(email='sarah@easevent.fr').is_verified)

    def test_code_email_essais_limites(self):
        self._register()
        code = self._code_from_mail()
        for _ in range(5):
            self.client.post('/api/auth/verify-code/', {'email': 'sarah@easevent.fr', 'code': '999999' if code != '999999' else '888888'}, format='json')
        cache.clear()
        r = self.client.post('/api/auth/verify-code/', {'email': 'sarah@easevent.fr', 'code': code}, format='json')
        self.assertEqual(r.data['code'], 'too_many_attempts')

    def test_code_par_sms_active_le_compte_et_le_numero(self):
        import re
        from unittest import mock

        class Ok:
            status_code = 201
            @staticmethod
            def json():
                return {}
        twilio = dict(TWILIO_ACCOUNT_SID='AC', TWILIO_AUTH_TOKEN='t', TWILIO_FROM_NUMBER='+33700000000')
        with self.settings(**twilio), mock.patch('invitations.sms.requests.post', return_value=Ok()) as post:
            r = self._register(verification_channel='sms')
            self.assertEqual(r.data['channel'], 'sms')
            self.assertEqual(len(mail.outbox), 0)
            code = re.search(r'est (\d{6})', post.call_args.kwargs['data']['Body']).group(1)
            r = self.client.post('/api/auth/verify-code/', {'email': 'sarah@easevent.fr', 'code': code, 'channel': 'sms'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['user']['phone_verified'])

    def test_sms_indisponible_bascule_sur_email(self):
        with self.settings(TWILIO_ACCOUNT_SID=''):
            r = self._register(verification_channel='sms')
        self.assertEqual(r.data['channel'], 'email')
        self.assertEqual(len(mail.outbox), 1)

    def test_verify_code_compte_inconnu_reponse_generique(self):
        r = self.client.post('/api/auth/verify-code/', {'email': 'personne@x.fr', 'code': '123456'}, format='json')
        self.assertEqual(r.data['code'], 'wrong_code')

    # ── M03 : vérification de l'email ─────────────────────────
    def test_jeton_stocke_sous_forme_hachee(self):
        user = User.objects.create_user(email='a@easevent.fr', password='x', first_name='A', last_name='B')
        raw = create_email_verification(user)
        stored = EmailVerification.objects.get(user=user).token
        self.assertNotEqual(stored, raw)
        self.assertEqual(stored, hash_token(raw))

    def test_verification_connecte_l_utilisateur_et_jeton_a_usage_unique(self):
        user = User.objects.create_user(email='a@easevent.fr', password='x', first_name='A', last_name='B')
        raw = create_email_verification(user)

        response = self.client.post('/api/auth/verify-email/', {'token': raw}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        user.refresh_from_db()
        self.assertTrue(user.is_verified)

        again = self.client.post('/api/auth/verify-email/', {'token': raw}, format='json')
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_connexion_non_verifie_renvoie_un_code(self):
        User.objects.create_user(email='a@easevent.fr', password='Jardin-Emeraude-26', first_name='A', last_name='B')
        response = self.client.post('/api/auth/login/',
                                    {'email': 'a@easevent.fr', 'password': 'Jardin-Emeraude-26'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'email_not_verified')

    def test_renvoi_reponse_generique(self):
        known = self.client.post('/api/auth/resend-verification/', {'email': 'inconnu@easevent.fr'}, format='json')
        User.objects.create_user(email='a@easevent.fr', password='x', first_name='A', last_name='B')
        unknown = self.client.post('/api/auth/resend-verification/', {'email': 'a@easevent.fr'}, format='json')
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 1)

    # ── M04 : mot de passe oublié ─────────────────────────────
    def test_reinitialisation_complete(self):
        import re
        user = User.objects.create_user(email='a@easevent.fr', password='Ancien-Mot-2026',
                                        first_name='A', last_name='B', is_verified=True)
        response = self.client.post('/api/auth/password-reset/', {'email': 'a@easevent.fr'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        link = re.search(r'/reset-password/([^/\s]+)/([^/\s]+)', mail.outbox[0].body)
        uid, token = link.group(1), link.group(2)

        response = self.client.post('/api/auth/password-reset/confirm/',
                                    {'uid': uid, 'token': token, 'new_password': 'Nouveau-Mot-2026'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Nouveau-Mot-2026'))

        # Le lien ne sert qu'une fois
        reuse = self.client.post('/api/auth/password-reset/confirm/',
                                 {'uid': uid, 'token': token, 'new_password': 'Autre-Mot-2026'}, format='json')
        self.assertEqual(reuse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reinitialisation_email_inconnu_meme_reponse(self):
        response = self.client.post('/api/auth/password-reset/', {'email': 'personne@easevent.fr'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    # ── Sécurité ──────────────────────────────────────────────
    def test_limitation_des_tentatives_de_connexion(self):
        codes = [
            self.client.post('/api/auth/login/', {'email': 'x@easevent.fr', 'password': 'faux'}, format='json').status_code
            for _ in range(11)
        ]
        self.assertEqual(codes[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_deconnexion_invalide_le_refresh_token(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        user = User.objects.create_user(email='a@easevent.fr', password='x', first_name='A', last_name='B')
        refresh = str(RefreshToken.for_user(user))
        self.client.post('/api/auth/logout/', {'refresh': refresh}, format='json')
        response = self.client.post('/api/auth/token/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_export_rgpd_et_stats(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        user = User.objects.create_user(email='a@easevent.fr', password='x', first_name='A', last_name='B')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')
        export = self.client.get('/api/auth/me/export/')
        self.assertEqual(export.status_code, status.HTTP_200_OK)
        self.assertEqual(export.data['account']['email'], 'a@easevent.fr')
        self.assertNotIn('password', str(export.data))
        stats = self.client.get('/api/auth/me/stats/')
        self.assertEqual(stats.data['events_count'], 0)


# ─────────────────────────────────────────────────────────────
# CLASSE 4 — Application mobile (APK) : liens email et images
# ─────────────────────────────────────────────────────────────
@override_settings(FRONTEND_URL='', PUBLIC_BASE_URL='https://easevent.example.com')
class MobileAppLinksTest(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_lien_reinitialisation_ouvre_la_page_du_backend(self):
        import re
        User.objects.create_user(email='a@easevent.fr', password='Ancien-Mot-2026', first_name='A', last_name='B')
        self.client.post('/api/auth/password-reset/', {'email': 'a@easevent.fr'}, format='json')
        path = re.search(r'https?://[^/\s]+(/api/auth/password-reset/[^/\s]+/[^/\s]+/)', mail.outbox[0].body).group(1)

        page = self.client.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Nouveau mot de passe')

        from django.test import Client
        browser = Client(enforce_csrf_checks=False)
        done = browser.post(path, {'new_password': 'Nouveau-Mot-2026', 'confirm_password': 'Nouveau-Mot-2026'})
        self.assertContains(done, 'Mot de passe modifié')
        self.assertTrue(User.objects.get(email='a@easevent.fr').check_password('Nouveau-Mot-2026'))

        # Le lien ne sert qu'une fois
        self.assertContains(browser.get(path), 'Lien non valide')

    def test_page_reinitialisation_lien_invalide(self):
        response = self.client.get('/api/auth/password-reset/xxx/yyy/')
        self.assertContains(response, 'Lien non valide')

    def test_image_de_couverture_en_url_absolue_https(self):
        from datetime import timedelta
        from events.models import Event
        organizer = User.objects.create_user(email='o@easevent.fr', password='x', first_name='O', last_name='R')
        Event.objects.create(
            organizer=organizer, title='Gala', event_type='gala',
            start_date=timezone.now() + timedelta(days=3), end_date=timezone.now() + timedelta(days=4),
            status='published', visibility='public', cover_image='events/seed_0_gallery01.png',
        )
        events = self.client.get('/api/events/publics/').data['events']
        self.assertEqual(events[0]['cover_image'], 'https://easevent.example.com/media/events/seed_0_gallery01.png')

    def test_media_servi_par_django(self):
        import os, tempfile
        from django.conf import settings
        os.makedirs(os.path.join(settings.MEDIA_ROOT, 'events'), exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=os.path.join(settings.MEDIA_ROOT, 'events'), suffix='.png', delete=False) as f:
            f.write(b'\x89PNG test')
        try:
            name = os.path.basename(f.name)
            response = self.client.get(f'/media/events/{name}')
            self.assertEqual(response.status_code, 200)
            self.assertIn(self.client.get('/media/../manage.py').status_code, (400, 404))
        finally:
            os.remove(f.name)
