"""
easevent/mail.py — envoi des emails par SendGrid avec la clé de l'administration
La clé SENDGRID_API_KEY est lue à chaque connexion : celle saisie (chiffrée) dans
l'administration Django, sinon celle de l'environnement.
"""
from sendgrid_backend import SendgridBackend

from adminpanel.keys import get_key


class DynamicSendgridBackend(SendgridBackend):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('api_key', get_key('SENDGRID_API_KEY'))
        super().__init__(*args, **kwargs)
