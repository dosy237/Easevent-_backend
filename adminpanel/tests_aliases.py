"""Clés lues sous d'autres noms courants du .env, clé publique Stripe refusée."""
import os
from unittest import mock

from django.test import SimpleTestCase, override_settings

from adminpanel.keys import env_value, get_key


@mock.patch('adminpanel.keys.stored_key', return_value='')
class KeyAliasTest(SimpleTestCase):
    @override_settings(STRIPE_SECRET_KEY='')
    @mock.patch.dict(os.environ, {'STRIPE_API_KEY': ' sk_test_alias123 '})
    def test_alias_used_when_official_name_missing(self, _):
        self.assertEqual(get_key('STRIPE_SECRET_KEY'), 'sk_test_alias123')
        self.assertEqual(env_value('STRIPE_SECRET_KEY')[1], 'STRIPE_API_KEY')

    @override_settings(STRIPE_SECRET_KEY='sk_test_officiel')
    @mock.patch.dict(os.environ, {'STRIPE_API_KEY': 'sk_test_alias123'})
    def test_official_name_wins(self, _):
        self.assertEqual(get_key('STRIPE_SECRET_KEY'), 'sk_test_officiel')

    @override_settings(STRIPE_SECRET_KEY='pk_live_publique')
    def test_publishable_key_refused(self, _):
        self.assertEqual(get_key('STRIPE_SECRET_KEY'), '')

    @override_settings(TWILIO_FROM_NUMBER='')
    @mock.patch.dict(os.environ, {'TWILIO_PHONE_NUMBER': '+4915888620339'})
    def test_twilio_number_alias(self, _):
        from invitations import sms
        self.assertEqual(get_key('TWILIO_FROM_NUMBER'), '+4915888620339')
        with override_settings(TWILIO_ACCOUNT_SID='AC' + '0' * 32, TWILIO_AUTH_TOKEN='0' * 32):
            self.assertTrue(sms.is_configured())

    @override_settings(NOTCHPAY_PUBLIC_KEY='')
    @mock.patch.dict(os.environ, {'NOTCHPAY_API_KEY': 'pk.notch123456'})
    def test_notchpay_alias(self, _):
        from tickets import mobile_money
        self.assertTrue(mobile_money.available())


class SmsReasonTest(SimpleTestCase):
    @override_settings(TWILIO_ACCOUNT_SID='AC' + '0' * 32, TWILIO_AUTH_TOKEN='0' * 32,
                       TWILIO_FROM_NUMBER='+4915888620339', TWILIO_MESSAGING_SERVICE_SID='')
    @mock.patch('adminpanel.keys.stored_key', return_value='')
    @mock.patch('invitations.sms.requests.post')
    def test_trial_account_reason_returned(self, post, _):
        from invitations import sms
        post.return_value = mock.Mock(status_code=400, json=lambda: {'code': 21608})
        self.assertEqual(sms.send_sms('+237690000000', 'test'), 'failed')
        self.assertIn("Compte Twilio d'essai", sms.last_error['reason'])
