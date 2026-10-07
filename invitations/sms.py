"""
invitations/sms.py
═══════════════════════════════════════════════════════════════
Envoi de SMS via l'API REST de Twilio (pas de SDK : une seule requête).

Configuration (.env du serveur) :
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
  TWILIO_FROM_NUMBER (numéro expéditeur)  ou  TWILIO_MESSAGING_SERVICE_SID

Sans configuration, send_sms() renvoie 'not_configured' : l'invitation
reste créée et l'organisateur voit que le SMS n'est pas parti.
Les numéros ne sont jamais écrits dans les journaux (RGPD).
═══════════════════════════════════════════════════════════════
"""
import logging

import requests
from django.conf import settings

from adminpanel.keys import get_key

logger = logging.getLogger(__name__)

API_URL = 'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json'
TIMEOUT = 8


def is_configured():
    return bool(
        get_key('TWILIO_ACCOUNT_SID') and get_key('TWILIO_AUTH_TOKEN')
        and (get_key('TWILIO_FROM_NUMBER') or get_key('TWILIO_MESSAGING_SERVICE_SID'))
    )


def send_sms(to, body):
    """Envoie un SMS. Retourne 'sent', 'failed' ou 'not_configured'."""
    if not is_configured():
        return 'not_configured'
    data = {'To': to, 'Body': body}
    if get_key('TWILIO_MESSAGING_SERVICE_SID'):
        data['MessagingServiceSid'] = get_key('TWILIO_MESSAGING_SERVICE_SID')
    else:
        data['From'] = get_key('TWILIO_FROM_NUMBER')
    try:
        resp = requests.post(
            API_URL.format(sid=get_key('TWILIO_ACCOUNT_SID')),
            data=data,
            auth=(get_key('TWILIO_ACCOUNT_SID'), get_key('TWILIO_AUTH_TOKEN')),
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning('SMS non envoyé (réseau) : %s', type(exc).__name__)
        return 'failed'
    if resp.status_code >= 400:
        try:
            code = resp.json().get('code')
        except ValueError:
            code = None
        logger.warning('SMS refusé par Twilio : HTTP %s, code %s', resp.status_code, code)
        return 'failed'
    return 'sent'
