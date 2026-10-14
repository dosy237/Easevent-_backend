"""
python manage.py seed_demo                      crée (ou recrée) le compte de démonstration
python manage.py seed_demo --email moi@x.fr --password '...'
python manage.py seed_demo --delete             supprime le compte de démonstration et ses données

Compte prêt pour les tests et la soutenance :
- plan Pro (générations de mini-site et événements illimités), email et téléphone vérifiés ;
- 4 événements organisés (mariage privé avec invités, conférence, gala payant, brouillon) ;
- invités qui ont déjà leur billet, d'autres en attente de réponse ;
- 2 billets « en attente » à générer soi-même (bouton « Valider » dans Mes billets)
  et 1 invitation à accepter (crée un 3e billet en attente).
Relancer la commande remet tout à zéro. Les comptes invités (@demo.easevent.test) n'ont pas
de mot de passe utilisable.
"""
import secrets
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

GUEST_DOMAIN = '@demo.easevent.test'
COVER = '/static/app/covers/{}.jpg'
PLACES = {
    'douala': ('Hôtel Akwa Palace, Boulevard de la Liberté, Douala', 4.0511, 9.6966),
    'yaounde': ('Palais des Congrès, Yaoundé', 3.8853, 11.5021),
    'kribi': ('Plage de Grand Batanga, Kribi', 2.8781, 9.9049),
}


def _verify_phone(user, e164):
    from invitations.crypto import blind_index, encrypt
    user.phone_number, user.phone_hash, user.phone_verified_at = encrypt(e164), blind_index(e164), timezone.now()


class Command(BaseCommand):
    help = 'Crée le compte de démonstration (plan Pro, événements, invités, billets à générer).'

    def add_arguments(self, parser):
        parser.add_argument('--email', default='demo@easevent.test', help='adresse de connexion du compte de démo')
        parser.add_argument('--password', default='', help='mot de passe (sinon un mot de passe est généré et affiché)')
        parser.add_argument('--delete', action='store_true', help='supprime le compte de démo et ses données')

    def handle(self, email, password, delete, **_):
        email = email.strip().lower()
        with transaction.atomic():
            self._cleanup(email)
            if delete:
                self.stdout.write(self.style.SUCCESS('Compte de démonstration supprimé.'))
                return
            password = password or f'Demo-{secrets.token_urlsafe(9)}'
            summary = self._create(email, password)
        self.stdout.write(self.style.SUCCESS('\nCompte de démonstration prêt'))
        self.stdout.write(f'  Email        : {email}')
        self.stdout.write(f'  Mot de passe : {password}')
        self.stdout.write('  Plan         : Pro (mini-sites et événements illimités)')
        for line in summary:
            self.stdout.write(f'  • {line}')

    # ────────────────────────────────────────────────────────────
    def _cleanup(self, email):
        from events.models import Event
        from users.models import User
        users = User.objects.filter(email=email) | User.objects.filter(email__endswith=GUEST_DOMAIN)
        Event.objects.filter(organizer__in=users).delete()
        users.delete()

    def _user(self, email, first, last, phone, password=None, **extra):
        from users.models import User
        user = User.objects.create_user(email=email, password=password, first_name=first, last_name=last,
                                        is_verified=True, accepted_privacy_at=timezone.now(), **extra)
        if not password:
            user.set_unusable_password()
        _verify_phone(user, phone)
        user.save()
        return user

    def _event(self, organizer, title, kind, days, place, cover, **extra):
        from events.models import Event
        address, lat, lng = PLACES[place]
        start = (timezone.now() + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
        return Event.objects.create(
            organizer=organizer, title=title, event_type=kind, start_date=start, end_date=start + timedelta(hours=6),
            location_address=address, latitude=lat, longitude=lng, timezone='Africa/Douala',
            cover_image=COVER.format(cover), status=extra.pop('status', 'published'), **extra)

    def _invite(self, event, guest, status='sent'):
        from invitations.models import Invitation
        from invitations.services import new_token
        return Invitation.objects.create(
            event=event, invited_user=guest, invited_by=event.organizer, token=new_token()[1], status=status,
            channel='platform_notification', delivery_status='sent', expires_at=event.end_date + timedelta(days=7),
            responded_at=timezone.now() if status != 'sent' else None)

    def _generated_ticket(self, event, guest, invitation=None):
        from tickets.models import Ticket
        return Ticket.objects.create(
            event=event, user=guest, invitation=invitation, status=Ticket.Status.GENERATED,
            payment_status=Ticket.PaymentStatus.NOT_REQUIRED, price=0, generated_at=timezone.now())

    def _create(self, email, password):
        from tickets.services import create_pending_ticket

        demo = self._user(email, 'Synthia', 'Démo', '+237690000100', password=password, subscription_plan='pro',
                          bio='Compte de démonstration Easevent')
        g = {key: self._user(f'{key}{GUEST_DOMAIN}', first, last, phone)
             for key, first, last, phone in (
                 ('marc', 'Marc', 'Etoundi', '+237690000101'),
                 ('aicha', 'Aïcha', 'Bello', '+237690000102'),
                 ('kevin', 'Kevin', 'Mbarga', '+237690000103'),
                 ('lina', 'Lina', 'Ngo', '+237690000104'))}

        # ── Événements organisés par le compte de démo ──
        mariage = self._event(demo, 'Mariage de Sarah & Junior', 'mariage', 21, 'kribi', 'photos/mariage-reception',
                              visibility='private', theme='Élégance tropicale au bord de la mer',
                              description='Cérémonie et réception face à l’océan. Tenue claire souhaitée.',
                              dress_code='Tenue claire', max_guests=150)
        conf = self._event(demo, 'Tech Douala Summit 2026', 'conference', 14, 'douala', 'photos/tech-ia',
                           visibility='public', theme='L’IA au service des entrepreneurs africains',
                           description='Une journée de conférences et d’ateliers sur l’IA et le numérique.')
        gala = self._event(demo, 'Gala des Talents', 'gala', 30, 'yaounde', 'photos/mariage-noir-blanc', visibility='public',
                           theme='Nuit dorée de la création', is_paid=True, price='25.00',
                           description='Dîner de gala et remise de prix. Billet payant.')
        self._event(demo, 'Anniversaire surprise de Lina', 'anniversaire', 40, 'douala', 'anniversaire-1',
                    visibility='private', status='draft', theme='Fiesta années 2000')

        for key in ('marc', 'aicha', 'kevin'):                       # invités qui ont déjà leur billet
            self._generated_ticket(mariage, g[key], self._invite(mariage, g[key], 'confirmed'))
        self._invite(mariage, g['lina'])                             # pas encore répondu
        for key in ('aicha', 'lina'):
            self._generated_ticket(conf, g[key])

        # ── Événements où le compte de démo est invité : billets à générer soi-même ──
        soiree = self._event(g['marc'], 'Soirée de lancement Easevent', 'soiree', 10, 'douala', 'photos/cocktail',
                             visibility='public', theme='Lancement chic et festif',
                             description='Cocktail de lancement. Entrée libre sur billet.')
        atelier = self._event(g['aicha'], 'Atelier photo au coucher du soleil', 'atelier', 12, 'kribi', 'atelier-1',
                              visibility='private', theme='Lumière dorée et portraits',
                              description='Atelier en petit groupe, appareil ou téléphone.')
        concert = self._event(g['kevin'], 'Concert Afro-Jazz', 'concert', 18, 'yaounde', 'photos/concert-stade',
                              visibility='private', theme='Afro-jazz sous les étoiles',
                              description='Concert intimiste en plein air.')
        create_pending_ticket(soiree, demo)                                            # billet en attente n°1
        create_pending_ticket(atelier, demo, invitation=self._invite(atelier, demo, 'confirmed'))   # n°2
        self._invite(concert, demo)                                                    # invitation à accepter
        return [
            'Mes événements : mariage privé (3 invités avec billet, 1 en attente), conférence publique '
            '(2 participants), gala payant 25 €, anniversaire en brouillon',
            'Mes billets : 2 billets EN ATTENTE à générer (« Soirée de lancement Easevent », '
            '« Atelier photo au coucher du soleil ») avec le bouton « Valider »',
            'Invitations : « Concert Afro-Jazz » à accepter (crée un 3e billet en attente)',
            'Mini-site : thème renseigné sur chaque événement, générations illimitées',
        ]
