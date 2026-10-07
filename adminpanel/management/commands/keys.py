"""
python manage.py keys status            liste des clés : où elles sont (base chiffrée / environnement), 4 derniers caractères
python manage.py keys check             teste chaque clé auprès de son service (lecture seule, aucun envoi)
python manage.py keys set NOM           saisie MASQUÉE d'une clé (rien ne s'affiche, rien dans l'historique du terminal)
python manage.py keys import FICHIER    enregistre toutes les clés d'un fichier NOM=valeur, puis efface le fichier

Les clés sont chiffrées en base (même circuit que Django admin) ; le format est vérifié avant
l'enregistrement et chaque clé est testée juste après.
"""
import getpass
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from adminpanel import key_checks, keys
from adminpanel.models import AdminAction, ServiceKey

NAMES = [c[0] for c in ServiceKey.Name.choices]
LABELS = dict(ServiceKey.Name.choices)


class Command(BaseCommand):
    help = 'Clés de service chiffrées : état, test, saisie masquée, import depuis un fichier.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['status', 'check', 'set', 'import'])
        parser.add_argument('target', nargs='?', help='NOM de la clé (set) ou chemin du fichier (import)')
        parser.add_argument('--keep-file', action='store_true', help="import : ne pas effacer le fichier")

    def handle(self, action, target=None, keep_file=False, **_):
        getattr(self, f'do_{action}')(target, keep_file)

    # ── état ─────────────────────────────────────────────────
    def do_status(self, *_):
        stored = {k.name: k for k in ServiceKey.objects.all()}
        for name in NAMES:
            if name in stored:
                where = f'base chiffrée  •••• {stored[name].last4}'
            elif getattr(settings, name, '') or os.environ.get(name):
                where = 'environnement du serveur'
            else:
                where = '— manquante'
            self.stdout.write(f'{LABELS[name][:48]:<50} {where}')

    # ── test ─────────────────────────────────────────────────
    def do_check(self, *_):
        bad = 0
        for name in NAMES:
            ok, msg = key_checks.live_check(name, keys.get_key)
            mark = {True: self.style.SUCCESS('OK  '), False: self.style.ERROR('ÉCHEC'), None: '  -  '}[ok]
            bad += ok is False
            self.stdout.write(f'{mark} {LABELS[name][:48]:<50} {msg}')
        if bad:
            raise CommandError(f'{bad} clé(s) à corriger.')

    # ── saisie masquée ───────────────────────────────────────
    def do_set(self, name, _keep):
        if name not in NAMES:
            raise CommandError('Nom inconnu. Noms possibles :\n  ' + '\n  '.join(NAMES))
        value = key_checks.clean(getpass.getpass(f'{LABELS[name]} — collez la clé (rien ne s’affiche), puis Entrée : '))
        self._save(name, value)

    # ── import d'un fichier NOM=valeur ───────────────────────
    def do_import(self, path, keep_file):
        if not path or not os.path.isfile(path):
            raise CommandError('Indiquez le fichier : python manage.py keys import /chemin/cles.txt')
        saved, errors = 0, 0
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, value = line.split('=', 1)
            name = name.strip().upper().removeprefix('EXPORT ').strip()
            if name not in NAMES:
                self.stdout.write(self.style.WARNING(f'Ignoré : « {name} » n’est pas une clé connue.'))
                continue
            try:
                self._save(name, key_checks.clean(value))
                saved += 1
            except CommandError as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(str(exc)))
        if not keep_file:
            # Le fichier contenait des secrets en clair : on l'écrase puis on le supprime
            size = os.path.getsize(path)
            with open(path, 'r+b') as fh:
                fh.write(b'\0' * size)
            os.remove(path)
            self.stdout.write('Fichier effacé.')
        self.stdout.write(self.style.SUCCESS(f'{saved} clé(s) enregistrée(s), {errors} refusée(s).'))

    def _save(self, name, value):
        error = key_checks.check_format(name, value)
        if error:
            raise CommandError(f'{LABELS[name]} : {error}')
        obj, _ = ServiceKey.objects.get_or_create(name=name, defaults={'encrypted_value': ''})
        obj.encrypted_value, obj.last4 = keys.encrypt(value), value[-4:]
        obj.save()
        AdminAction.objects.create(actor=None, action='servicekey.save', target_type='servicekey', target_id=name,
                                   detail={'changed': True, 'via': 'manage.py keys'})
        for warning in key_checks.warnings(name, value):
            self.stdout.write(self.style.WARNING(f'  ! {warning}'))
        ok, msg = key_checks.live_check(name, lambda n: value if n == name else keys.get_key(n))
        mark = {True: self.style.SUCCESS('OK'), False: self.style.ERROR('ÉCHEC'), None: '-'}[ok]
        self.stdout.write(f'{LABELS[name]} : enregistrée (•••• {value[-4:]}) — test : {mark} {msg}')
