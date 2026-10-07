from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from . import keys
from .models import AdminAction, ServiceKey

User = get_user_model()


class ServiceKeyTest(TestCase):
    def setUp(self):
        cache.clear()
        self.root = User.objects.create_superuser(email='root@x.fr', password='Jardin-Emeraude-26', first_name='R', last_name='T')
        self.staff = User.objects.create_user(email='staff@x.fr', password='Jardin-Emeraude-26', first_name='S', last_name='T',
                                              is_staff=True, is_verified=True)

    @override_settings(GEMINI_API_KEY='cle-env')
    def test_repli_environnement_puis_cle_chiffree_sans_redemarrage(self):
        self.assertEqual(keys.get_key('GEMINI_API_KEY'), 'cle-env')
        self.client.force_login(self.root)
        r = self.client.post('/admin/adminpanel/servicekey/add/', {'name': 'GEMINI_API_KEY', 'value': 'AIzaSecrete1234'})
        self.assertEqual(r.status_code, 302, r.content[:500])
        row = ServiceKey.objects.get(name='GEMINI_API_KEY')
        self.assertNotIn('AIzaSecrete', row.encrypted_value)                    # chiffrée en base
        self.assertEqual(row.last4, '1234')
        self.assertEqual(keys.get_key('GEMINI_API_KEY'), 'AIzaSecrete1234')     # prise en compte aussitôt
        # La page de modification n'affiche jamais la valeur
        page = self.client.get(f'/admin/adminpanel/servicekey/{row.pk}/change/').content.decode()
        self.assertNotIn('AIzaSecrete1234', page)
        self.assertIn('1234', page)
        # Laisser vide garde la valeur ; suppression → retour à l'environnement
        self.client.post(f'/admin/adminpanel/servicekey/{row.pk}/change/', {'name': 'GEMINI_API_KEY', 'value': ''})
        self.assertEqual(keys.get_key('GEMINI_API_KEY'), 'AIzaSecrete1234')
        row.delete()
        self.assertEqual(keys.get_key('GEMINI_API_KEY'), 'cle-env')
        self.assertTrue(AdminAction.objects.filter(action='servicekey.save').exists())

    def test_reserve_aux_super_administrateurs(self):
        self.client.force_login(self.staff)
        self.assertIn(self.client.get('/admin/adminpanel/servicekey/').status_code, (302, 403))

    def test_cle_illisible_si_chiffrement_change(self):
        ServiceKey.objects.create(name='GROQ_API_KEY', encrypted_value=keys.encrypt('gsk_abc'))
        self.assertEqual(keys.get_key('GROQ_API_KEY'), 'gsk_abc')
        keys.forget('GROQ_API_KEY')
        with override_settings(KEYS_ENCRYPTION_KEY='autre-cle'):
            self.assertEqual(keys.get_key('GROQ_API_KEY'), '')                   # repli : environnement (vide ici)
