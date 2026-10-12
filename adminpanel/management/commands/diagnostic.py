"""
python manage.py diagnostic                          état du serveur : base, Redis, Celery, emails, SMS, clés
python manage.py diagnostic --email vous@exemple.fr  envoie un vrai email de test et affiche l'erreur exacte s'il échoue
python manage.py diagnostic --sms +237690000000      envoie un vrai SMS de test

À lancer sur le serveur : docker compose exec web python manage.py diagnostic
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from adminpanel.keys import get_key


class Command(BaseCommand):
    help = "Diagnostic du serveur (base, Redis, Celery, emails, SMS) avec envoi de test facultatif."

    def add_arguments(self, parser):
        parser.add_argument('--email', help='adresse qui recevra un email de test')
        parser.add_argument('--sms', help='numéro international qui recevra un SMS de test')

    def line(self, ok, label, detail=''):
        mark = {True: self.style.SUCCESS('OK   '), False: self.style.ERROR('ÉCHEC'), None: self.style.WARNING('ATTN ')}[ok]
        self.stdout.write(f'{mark} {label:<28} {detail}')

    def handle(self, email=None, sms=None, **_):
        self.stdout.write(f'Docker : {"oui" if os.path.exists("/.dockerenv") else "non"} · DEBUG={settings.DEBUG}')

        # Base de données
        try:
            from django.db import connection
            connection.ensure_connection()
            self.line(True, 'Base de données', connection.settings_dict.get('HOST') or 'local')
        except Exception as exc:
            self.line(False, 'Base de données', str(exc)[:160])

        # Redis (cache, limites de débit, temps réel)
        self.stdout.write(f'      Redis visé : {settings.REDIS_URL}')
        try:
            from django.core.cache import cache
            cache.set('diagnostic', 'ok', 10)
            ok = cache.get('diagnostic') == 'ok'
            self.line(ok, 'Redis (cache)', '' if ok else 'écriture puis lecture impossible')
        except Exception as exc:
            self.line(False, 'Redis (cache)', f'{type(exc).__name__} : définissez REDIS_URL=redis://redis:6379/0 dans .env')

        # Celery
        try:
            from easevent.celery import app
            replies = app.control.ping(timeout=2) or []
            self.line(bool(replies) or None, 'Celery (tâches de fond)',
                      f'{len(replies)} worker(s)' if replies else 'aucun worker ne répond : les envois partent directement (plus lent)')
        except Exception as exc:
            self.line(None, 'Celery (tâches de fond)', f'{type(exc).__name__} : envois faits directement')

        # Emails
        backend = settings.EMAIL_BACKEND
        key = get_key('SENDGRID_API_KEY')
        self.line(bool(key) if 'Sendgrid' in backend else None, 'Emails',
                  f'{backend.rsplit(".", 1)[-1]} · expéditeur {settings.DEFAULT_FROM_EMAIL} · clé '
                  + (f'présente (…{key[-4:]})' if key else 'ABSENTE'))
        if 'console' in backend.lower():
            self.line(False, 'Emails', "EMAIL_BACKEND = console : les emails sont écrits dans le journal, jamais envoyés. Retirez cette ligne du .env")
        if email:
            from django.core.mail import send_mail
            try:
                n = send_mail('Easevent — email de test', 'Si vous lisez ceci, les emails du serveur fonctionnent.',
                              settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
                self.line(bool(n), f'Email de test → {email}', 'envoyé : vérifiez la boîte de réception et les indésirables')
            except Exception as exc:
                msg = str(exc)
                hint = ''
                if 'Tunnel' in msg or 'urlopen' in msg or 'timed out' in msg:
                    hint = ' → le serveur ne joint pas SendGrid (réseau ou pare-feu)'
                elif '403' in msg or 'Forbidden' in msg:
                    hint = " → l'adresse d'expédition n'est pas vérifiée dans SendGrid (Settings › Sender Authentication)"
                elif '401' in msg or 'Unauthorized' in msg:
                    hint = ' → clé SendGrid invalide ou sans le droit « Mail Send »'
                self.line(False, f'Email de test → {email}', msg[:200] + hint)

        # SMS
        from invitations import sms as sms_mod
        sid, token = get_key('TWILIO_ACCOUNT_SID'), get_key('TWILIO_AUTH_TOKEN')
        sender = get_key('TWILIO_MESSAGING_SERVICE_SID') or get_key('TWILIO_FROM_NUMBER')
        missing = [n for n, v in (('TWILIO_ACCOUNT_SID', sid), ('TWILIO_AUTH_TOKEN', token),
                                  ('TWILIO_FROM_NUMBER ou TWILIO_MESSAGING_SERVICE_SID', sender)) if not v]
        self.line(sms_mod.is_configured(), 'SMS (Twilio)',
                  f'expéditeur {sender}' if not missing else 'manquant : ' + ', '.join(missing))
        if sms:
            import requests
            data = {'To': sms, 'Body': 'Easevent : SMS de test. Les SMS du serveur fonctionnent.'}
            if get_key('TWILIO_MESSAGING_SERVICE_SID'):
                data['MessagingServiceSid'] = get_key('TWILIO_MESSAGING_SERVICE_SID')
            else:
                data['From'] = get_key('TWILIO_FROM_NUMBER')
            try:
                r = requests.post(sms_mod.API_URL.format(sid=sid), data=data, auth=(sid, token), timeout=15)
                body = r.json() if r.content else {}
                if r.status_code < 400:
                    self.line(True, f'SMS de test → {sms}', f"accepté par Twilio (statut {body.get('status')})")
                else:
                    # Message de Twilio : numéro non vérifié (compte d'essai), pays non autorisé, expéditeur invalide…
                    self.line(False, f'SMS de test → {sms}',
                              f"Twilio {r.status_code}, code {body.get('code')} : {body.get('message', '')[:200]}")
            except Exception as exc:
                self.line(False, f'SMS de test → {sms}', f'{type(exc).__name__} : {str(exc)[:160]}')

        # Autres services
        for name in ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'NOTCHPAY_PUBLIC_KEY', 'GOOGLE_MAPS_API_KEY',
                     'GEMINI_API_KEY', 'GROQ_API_KEY', 'OPENROUTER_API_KEY'):
            v = get_key(name)
            mode = ' (TEST)' if '_test_' in v else (' (RÉEL)' if '_live_' in v else '')
            self.line(bool(v) or None, name, f'présente (…{v[-4:]}){mode}' if v else 'absente')
        self.stdout.write('Test en direct de chaque clé : python manage.py keys check')
