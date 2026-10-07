"""Vérification des clés : format, nettoyage, test en direct, commande « keys »."""
import os
import tempfile
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from . import key_checks, keys
from .models import ServiceKey

STRIPE = 'sk_live_' + 'a1B2c3D4e5F6g7H8i9J0'


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}
        self.ok = status < 400

    def json(self):
        return self._data


class KeyChecksTest(TestCase):

    def test_nettoyage_des_erreurs_de_copie(self):
        self.assertEqual(key_checks.clean(f'  "{STRIPE}"\n'), STRIPE)
        self.assertEqual(key_checks.clean(f'STRIPE_SECRET_KEY={STRIPE}'), STRIPE)

    def test_format(self):
        self.assertEqual(key_checks.check_format('STRIPE_SECRET_KEY', STRIPE), '')
        self.assertIn('sk_live_', key_checks.check_format('STRIPE_SECRET_KEY', 'pk_live_' + 'x' * 20))   # clé publique collée
        self.assertIn('whsec_', key_checks.check_format('STRIPE_WEBHOOK_SECRET', STRIPE))                # inversion
        self.assertIn('espace', key_checks.check_format('GROQ_API_KEY', 'gsk_abc def'))
        self.assertEqual(key_checks.check_format('TWILIO_ACCOUNT_SID', 'AC' + 'a' * 32), '')
        self.assertTrue(key_checks.warnings('STRIPE_SECRET_KEY', 'sk_test_' + 'a' * 20))
        # Nouveau format Google : accepté ; format inconnu d'un service non strict : averti, pas bloqué
        self.assertEqual(key_checks.check_format('GEMINI_API_KEY', 'AQ.' + 'Ab_1' * 12), '')
        self.assertEqual(key_checks.check_format('MISTRAL_API_KEY', 'nouveau-format.123'), '')
        self.assertTrue(key_checks.warnings('MISTRAL_API_KEY', 'nouveau-format.123'))

    def test_test_en_direct(self):
        get = {'STRIPE_SECRET_KEY': STRIPE}.get
        with mock.patch('requests.request', return_value=Resp(200)):
            self.assertEqual(key_checks.live_check('STRIPE_SECRET_KEY', get), (True, 'clé acceptée (mode réel)'))
        with mock.patch('requests.request', return_value=Resp(401)):
            ok, msg = key_checks.live_check('STRIPE_SECRET_KEY', get)
        self.assertFalse(ok)
        self.assertIn('refusée', msg)
        self.assertEqual(key_checks.live_check('GROQ_API_KEY', get)[0], None)                      # non renseignée

    def test_commande_import_puis_efface_le_fichier(self):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w') as fh:
            fh.write(f'# mes clés\nSTRIPE_SECRET_KEY="{STRIPE}"\nSTRIPE_WEBHOOK_SECRET=pk_live_inversee\nINCONNUE=1\n')
        out = StringIO()
        with mock.patch('requests.request', return_value=Resp(200)):
            call_command('keys', 'import', path, stdout=out)
        self.assertFalse(os.path.exists(path))                                                   # fichier effacé
        self.assertEqual(keys.decrypt(ServiceKey.objects.get(name='STRIPE_SECRET_KEY').encrypted_value), STRIPE)
        self.assertFalse(ServiceKey.objects.filter(name='STRIPE_WEBHOOK_SECRET').exists())        # confusion refusée
        text = out.getvalue()
        self.assertIn('1 clé(s) enregistrée(s), 1 refusée(s)', text)
        self.assertNotIn(STRIPE, text)                                                           # jamais affichée

    def test_admin_refuse_un_mauvais_format(self):
        from django.contrib.auth import get_user_model
        admin = get_user_model().objects.create_superuser(email='a@x.fr', password='x', first_name='A', last_name='B')
        self.client.force_login(admin)
        r = self.client.post('/admin/adminpanel/servicekey/add/', {'name': 'STRIPE_WEBHOOK_SECRET', 'value': STRIPE})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'whsec_')
        self.assertFalse(ServiceKey.objects.exists())
        with mock.patch('requests.request', return_value=Resp(200)):
            r = self.client.post('/admin/adminpanel/servicekey/add/', {'name': 'STRIPE_SECRET_KEY', 'value': f' "{STRIPE}" '},
                                 follow=True)
        self.assertContains(r, 'Test en direct')
        self.assertEqual(keys.decrypt(ServiceKey.objects.get().encrypted_value), STRIPE)
