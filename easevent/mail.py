"""
easevent/mail.py — envoi des emails : SendGrid d'abord, Gmail (SMTP) en secours
════════════════════════════════════════════════════════════════
- SENDGRID_API_KEY : lue à chaque envoi (administration Django chiffrée, sinon .env).
- Secours SMTP (Gmail) : EMAIL_HOST_USER + EMAIL_HOST_PASSWORD (mot de passe
  d'application Google). Utilisé si SendGrid échoue ou n'est pas configuré.
- EMAIL_PROVIDER=smtp : Gmail directement, SendGrid ignoré.
Gmail n'accepte que son propre compte comme expéditeur : l'adresse d'envoi
devient EMAIL_HOST_USER (le nom affiché « Easevent » est conservé).
════════════════════════════════════════════════════════════════
"""
import copy
import logging
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from sendgrid_backend import SendgridBackend

from adminpanel.keys import get_key

logger = logging.getLogger(__name__)


class DynamicSendgridBackend(SendgridBackend):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('api_key', get_key('SENDGRID_API_KEY'))
        super().__init__(*args, **kwargs)


def smtp_password():
    # Google affiche le mot de passe d'application par groupes de 4 : « abcd efgh ijkl mnop »
    return get_key('EMAIL_HOST_PASSWORD').replace(' ', '')


def smtp_configured():
    return bool(settings.EMAIL_HOST_USER and smtp_password())


def sendgrid_configured():
    return bool(get_key('SENDGRID_API_KEY'))


def describe_error(exc):
    """Cause lisible d'un échec d'envoi (journal, commande diagnostic)."""
    body = getattr(exc, 'body', b'') or b''
    if isinstance(body, bytes):
        body = body.decode('utf-8', 'replace')
    msg = f'{type(exc).__name__} : {exc} {body}'.strip()
    network = ('URLError', 'Tunnel', 'urlopen', 'gaierror', 'timed out', 'Timeout', 'Connection refused',
               'unreachable', 'Name or service not known', 'ConnectionResetError')
    if any(word in msg for word in network):
        return msg[:300] + ' → service injoignable depuis le serveur (réseau, DNS ou port bloqué par l’hébergeur)'
    if 'credits exceeded' in msg.lower():
        return msg[:300] + ' → crédits SendGrid épuisés (offre gratuite terminée) : utilisez Gmail en secours ou rechargez SendGrid'
    if '401' in msg or 'Unauthorized' in msg:
        return msg[:300] + ' → clé SendGrid invalide ou sans le droit « Mail Send »'
    if '403' in msg or 'Forbidden' in msg:
        return msg[:300] + " → expéditeur non vérifié dans SendGrid (Settings › Sender Authentication)"
    if 'SMTPAuthenticationError' in msg or '535' in msg:
        return msg[:300] + " → Gmail refuse : utilisez un mot de passe d'application (validation en deux étapes activée)"
    return msg[:300]


class ResilientEmailBackend(BaseEmailBackend):
    """SendGrid, puis Gmail (SMTP) si SendGrid échoue ou n'est pas configuré."""

    last_channel = ''      # canal du dernier envoi réussi : 'sendgrid' ou 'smtp' (diagnostic)

    def _sendgrid(self):
        return DynamicSendgridBackend(fail_silently=False)

    def _smtp(self):
        return SmtpBackend(
            host=settings.EMAIL_HOST, port=settings.EMAIL_PORT,
            username=settings.EMAIL_HOST_USER, password=smtp_password(),
            use_tls=settings.EMAIL_USE_TLS, use_ssl=settings.EMAIL_USE_SSL,
            timeout=settings.EMAIL_TIMEOUT, fail_silently=False,
        )

    @staticmethod
    def _as_gmail_sender(message):
        """Copie du message avec le compte Gmail comme expéditeur (nom affiché conservé)."""
        msg = copy.copy(message)
        name = parseaddr(message.from_email or '')[0] or 'Easevent'
        msg.from_email = formataddr((name, settings.EMAIL_HOST_USER))
        if not msg.reply_to and message.from_email and parseaddr(message.from_email)[1] != settings.EMAIL_HOST_USER:
            msg.reply_to = [message.from_email]
        return msg

    def send_messages(self, email_messages):
        messages = list(email_messages or [])
        if not messages:
            return 0
        errors = []
        if settings.EMAIL_PROVIDER != 'smtp' and sendgrid_configured():
            try:
                sent = self._sendgrid().send_messages(messages)
                ResilientEmailBackend.last_channel = 'sendgrid'
                return sent
            except Exception as exc:
                errors.append(f'SendGrid : {describe_error(exc)}')
                logger.error("Échec d'envoi par SendGrid : %s", describe_error(exc))
        if smtp_configured():
            try:
                sent = self._smtp().send_messages([self._as_gmail_sender(m) for m in messages])
                ResilientEmailBackend.last_channel = 'smtp'
                return sent
            except Exception as exc:
                errors.append(f'Gmail : {describe_error(exc)}')
                logger.error("Échec d'envoi par Gmail (SMTP) : %s", describe_error(exc))
        if not errors:
            errors.append('aucun service configuré (SENDGRID_API_KEY, ou EMAIL_HOST_USER + EMAIL_HOST_PASSWORD)')
            logger.error("Email non envoyé : %s", errors[0])
        if self.fail_silently:
            return 0
        raise EmailDeliveryError(' | '.join(errors))


class EmailDeliveryError(Exception):
    pass
