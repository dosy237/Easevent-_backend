"""Envoi des emails : SendGrid d'abord, Gmail (SMTP) en secours."""
from unittest import mock

from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from easevent.mail import EmailDeliveryError, ResilientEmailBackend

GMAIL = dict(EMAIL_HOST_USER='easevent.test@gmail.com', EMAIL_HOST_PASSWORD='abcd efgh ijkl mnop',
             EMAIL_PROVIDER='auto')


def message():
    return EmailMessage('Code', 'Votre code : 123456', 'Easevent <dosyca35@gmail.com>', ['ami@exemple.fr'])


@override_settings(SENDGRID_API_KEY='SG.cle.valide', **GMAIL)
class ResilientEmailBackendTest(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch('adminpanel.keys.stored_key', return_value='')     # pas de base : .env seulement
        patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch('easevent.mail.SmtpBackend.send_messages', return_value=1)
    @mock.patch('easevent.mail.DynamicSendgridBackend.send_messages', return_value=1)
    def test_sendgrid_first(self, sendgrid, smtp):
        self.assertEqual(ResilientEmailBackend().send_messages([message()]), 1)
        sendgrid.assert_called_once()
        smtp.assert_not_called()
        self.assertEqual(ResilientEmailBackend.last_channel, 'sendgrid')

    @mock.patch('easevent.mail.SmtpBackend.send_messages', return_value=1)
    @mock.patch('easevent.mail.DynamicSendgridBackend.send_messages', side_effect=Exception('HTTP Error 403: Forbidden'))
    def test_gmail_when_sendgrid_fails(self, sendgrid, smtp):
        with self.assertLogs('easevent.mail', 'ERROR') as logs:
            self.assertEqual(ResilientEmailBackend().send_messages([message()]), 1)
        self.assertIn('expéditeur non vérifié', logs.output[0])
        sent = smtp.call_args.args[0][0]
        self.assertEqual(sent.from_email, 'Easevent <easevent.test@gmail.com>')   # Gmail : son propre compte
        self.assertEqual(sent.reply_to, ['Easevent <dosyca35@gmail.com>'])
        self.assertEqual(ResilientEmailBackend.last_channel, 'smtp')

    @override_settings(SENDGRID_API_KEY='')
    @mock.patch('easevent.mail.SmtpBackend.send_messages', return_value=1)
    def test_gmail_without_sendgrid(self, smtp):
        self.assertEqual(ResilientEmailBackend().send_messages([message()]), 1)
        smtp.assert_called_once()

    @override_settings(EMAIL_PROVIDER='smtp')
    @mock.patch('easevent.mail.SmtpBackend.send_messages', return_value=1)
    @mock.patch('easevent.mail.DynamicSendgridBackend.send_messages', return_value=1)
    def test_force_gmail(self, sendgrid, smtp):
        ResilientEmailBackend().send_messages([message()])
        sendgrid.assert_not_called()
        smtp.assert_called_once()

    def test_password_spaces_removed(self):
        backend = ResilientEmailBackend()._smtp()
        self.assertEqual(backend.password, 'abcdefghijklmnop')

    @override_settings(SENDGRID_API_KEY='', EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='')
    def test_nothing_configured_raises(self):
        with self.assertLogs('easevent.mail', 'ERROR'):
            with self.assertRaises(EmailDeliveryError):
                ResilientEmailBackend().send_messages([message()])

    @mock.patch('easevent.mail.SmtpBackend.send_messages', side_effect=OSError('timed out'))
    @mock.patch('easevent.mail.DynamicSendgridBackend.send_messages', side_effect=Exception('401 Unauthorized'))
    def test_both_fail_reports_both(self, sendgrid, smtp):
        with self.assertLogs('easevent.mail', 'ERROR'):
            with self.assertRaises(EmailDeliveryError) as ctx:
                ResilientEmailBackend().send_messages([message()])
        self.assertIn('SendGrid', str(ctx.exception))
        self.assertIn('Gmail', str(ctx.exception))
        self.assertIn('injoignable', str(ctx.exception))

    def test_network_error_not_mistaken_for_sender(self):
        from easevent.mail import describe_error
        text = describe_error(Exception('<urlopen error Tunnel connection failed: 403 Forbidden>'))
        self.assertIn('injoignable', text)
        self.assertNotIn('expéditeur', text)
