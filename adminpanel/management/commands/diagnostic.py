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

        # Emails : Gmail (SMTP) ; SendGrid seulement si EMAIL_PROVIDER=auto
        from adminpanel.key_checks import smtp_login_check
        from easevent.mail import ResilientEmailBackend, smtp_configured, smtp_password
        backend = settings.EMAIL_BACKEND
        gmail_only = settings.EMAIL_PROVIDER == 'smtp'
        self.line(True if 'Resilient' in backend else None, 'Emails : moteur',
                  f'{backend.rsplit(".", 1)[-1]} · ' + ('Gmail uniquement' if gmail_only else 'SendGrid puis Gmail'))
        if 'console' in backend.lower() or 'locmem' in backend.lower():
            self.line(False, 'Emails', "EMAIL_BACKEND ne fait que simuler l'envoi : retirez EMAIL_BACKEND du .env")
        if smtp_configured():
            ok, msg = smtp_login_check(smtp_password())
            self.line(ok, 'Emails : Gmail', f'{settings.EMAIL_HOST}:{settings.EMAIL_PORT} · {msg}')
        else:
            missing = [n for n, v in (('EMAIL_HOST_USER', settings.EMAIL_HOST_USER), ('EMAIL_HOST_PASSWORD', smtp_password())) if not v]
            self.line(False if gmail_only else None, 'Emails : Gmail', 'manquant dans le .env : ' + ', '.join(missing))
        if gmail_only:
            self.line(None, 'Emails : SendGrid', 'non utilisé')
        else:
            key = get_key('SENDGRID_API_KEY')
            self.line(bool(key) or None, 'Emails : SendGrid', f'clé présente (…{key[-4:]})' if key else 'clé absente')
        if email:
            from django.core.mail import send_mail
            try:
                n = send_mail('Easevent — email de test', 'Si vous lisez ceci, les emails du serveur fonctionnent.',
                              settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
                via = {'sendgrid': 'SendGrid', 'smtp': 'Gmail'}.get(ResilientEmailBackend.last_channel, '')
                self.line(bool(n), f'Email de test → {email}',
                          f'envoyé{" par " + via if via else ""} : vérifiez la boîte de réception ET les indésirables')
            except Exception as exc:
                from easevent.mail import describe_error
                self.line(False, f'Email de test → {email}', str(exc)[:400] if 'Delivery' in type(exc).__name__
                          else describe_error(exc))

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

        # Paiements et autres services : d'où vient chaque clé (administration, .env, alias)
        from adminpanel.keys import ALIASES, env_value, stored_key, usable
        for name in ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'NOTCHPAY_PUBLIC_KEY', 'NOTCHPAY_HASH_KEY',
                     'GOOGLE_MAPS_API_KEY', 'GEMINI_API_KEY', 'GROQ_API_KEY', 'OPENROUTER_API_KEY'):
            raw, source = (stored_key(name), 'administration') if stored_key(name) else env_value(name)
            if raw and not usable(name, raw):
                self.line(False, name, f'lue dans {source} mais c’est la clé PUBLIQUE (pk_…) : il faut la clé secrète sk_…')
                continue
            mode = ' (TEST)' if '_test_' in raw else (' (RÉEL)' if '_live_' in raw else '')
            where = '' if source in (name, '') else f' · lue dans {source}'
            self.line(bool(raw) or None, name, f'présente (…{raw[-4:]}){mode}{where}' if raw else 'absente')
        self.line(bool(get_key('STRIPE_SECRET_KEY')), 'Paiement par carte',
                  'actif' if get_key('STRIPE_SECRET_KEY') else 'inactif : STRIPE_SECRET_KEY (sk_live_… ou sk_test_…) manquante')
        from tickets import mobile_money
        self.line(mobile_money.available() or None, 'Mobile Money (Notch Pay)',
                  'actif' if mobile_money.available() else 'inactif : NOTCHPAY_PUBLIC_KEY manquante')

        # Noms des variables du .env (jamais les valeurs) : reconnues, alias, ou inconnues
        known = set(ALIASES) | {a for names in ALIASES.values() for a in names} | {
            'STRIPE_CONNECT_COUNTRY', 'STRIPE_PRICE_STANDARD_MONTHLY', 'STRIPE_PRICE_STANDARD_ANNUAL',
            'STRIPE_PRICE_PRO_MONTHLY', 'STRIPE_PRICE_PRO_ANNUAL', 'STRIPE_PUBLISHABLE_KEY', 'STRIPE_PUBLIC_KEY',
            'TWILIO_MESSAGING_SERVICE_SID', 'NOTCHPAY_API', 'SENDGRID_API_KEY', 'EMAIL_BACKEND', 'EMAIL_PROVIDER',
            'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS', 'EMAIL_USE_SSL', 'EMAIL_HOST_USER', 'EMAIL_TIMEOUT',
            'EMAIL_USER', 'GMAIL_USER', 'SMTP_USER', 'SENDGRID_SANDBOX_MODE_IN_DEBUG'}
        alias_of = {a: n for n, names in ALIASES.items() for a in names}
        prefixes = ('STRIPE', 'TWILIO', 'NOTCH', 'EMAIL', 'GMAIL', 'SMTP', 'SENDGRID')
        names = sorted(n for n in set(os.environ) | set(_dotenv_names()) if n.startswith(prefixes))
        self.stdout.write('      Variables du .env (noms seulement) :')
        from decouple import config
        for n in names:
            if not str(config(n, default='')).strip().strip('"\''):
                self.line(None, f'  {n}', 'présente mais VIDE')
            elif n in alias_of:
                self.stdout.write(f'        {n}  → lue comme {alias_of[n]}')
            elif n in known:
                self.stdout.write(f'        {n}  ✓')
            else:
                self.line(None, f'  {n}', 'nom inconnu : non utilisé (vérifiez l’orthographe)')
        self.stdout.write('Test en direct de chaque clé : python manage.py keys check')


def _dotenv_names():
    """Noms des variables du fichier .env du projet (les valeurs ne sont pas lues)."""
    path = os.path.join(settings.BASE_DIR, '.env')
    try:
        with open(path, encoding='utf-8') as f:
            return [line.split('=', 1)[0].strip().removeprefix('export ').strip()
                    for line in f if '=' in line and not line.lstrip().startswith('#')]
    except OSError:
        return []
