from django.core.management.base import BaseCommand
from django.utils import timezone
from users.models import User
from events.models import Event, EventCollaborator
from invitations.models import Invitation
from datetime import timedelta


class Command(BaseCommand):
    help = 'Seeds the database with realistic production-quality data'

    def handle(self, *args, **kwargs):
        self.stdout.write("🌱 Starting database seeding...")

        # ─────────────────────────────────────────────────
        # 1. USERS
        # ─────────────────────────────────────────────────
        users_data = [
            {
                'email': 'jean.mbarga@gmail.com',
                'first_name': 'Jean',
                'last_name': 'Mbarga',
                'bio': 'Organisateur événementiel basé à Douala.',
                'subscription_plan': 'pro',
            },
            {
                'email': 'fatou.diallo@outlook.com',
                'first_name': 'Fatou',
                'last_name': 'Diallo',
                'bio': 'Passionnée de mariages et de décoration.',
                'subscription_plan': 'standard',
            },
            {
                'email': 'paul.nguesso@yahoo.fr',
                'first_name': 'Paul',
                'last_name': 'Nguesso',
                'bio': 'Photographe professionnel et vidéaste.',
                'subscription_plan': 'free',
            },
            {
                'email': 'amina.bello@gmail.com',
                'first_name': 'Amina',
                'last_name': 'Bello',
                'bio': 'Coordinatrice de conférences tech en Afrique.',
                'subscription_plan': 'pro',
            },
            {
                'email': 'olivier.kamga@hotmail.com',
                'first_name': 'Olivier',
                'last_name': 'Kamga',
                'bio': 'DJ et organisateur de soirées à Yaoundé.',
                'subscription_plan': 'standard',
            },
        ]

        users = []
        for u_data in users_data:
            user, created = User.objects.get_or_create(
                email=u_data['email'],
                defaults={
                    'first_name': u_data['first_name'],
                    'last_name': u_data['last_name'],
                    'bio': u_data.get('bio', ''),
                    'subscription_plan': u_data.get('subscription_plan', 'free'),
                    'is_verified': True,
                }
            )
            if created:
                user.set_password('Easevent2026!')
                user.save()
            users.append(user)
            self.stdout.write(f"  {'✅ Created' if created else '⏭️  Found'} user: {user.full_name} <{user.email}>")

        jean, fatou, paul, amina, olivier = users

        # ─────────────────────────────────────────────────
        # 2. EVENTS
        # ─────────────────────────────────────────────────
        now = timezone.now()

        events_data = [
            # ── Mariage ──────────────────────────────────
            {
                'organizer': jean,
                'title': 'Mariage de Fatou & Abdou',
                'event_type': Event.EventType.MARIAGE,
                'description': (
                    "Nous avons l'immense joie de vous inviter à célébrer notre union. "
                    "La cérémonie sera suivie d'un cocktail et d'une réception dansante "
                    "dans les jardins du Domaine La Falaise à Douala. Dress code : tenue traditionnelle."
                ),
                'start_date': now + timedelta(days=45),
                'end_date': now + timedelta(days=45, hours=12),
                'location_address': 'Domaine La Falaise, Bonanjo, Douala, Cameroun',
                'latitude': 4.048056,
                'longitude': 9.705278,
                'status': Event.EventStatus.PUBLISHED,
                'visibility': Event.Visibility.PUBLIC,
                'ambiance': Event.Ambiance.ELEGANT,
                'template_config': {
                    'palette': {'primary': '#C4A882', 'secondary': '#2C3E50', 'accent': '#E8D5B7'},
                    'zones': {
                        'header': {'component': 'hero_3', 'animation': 'fade_parallax'},
                        'rsvp': {'component': 'rsvp_elegant', 'fields': ['name', 'email', 'guests', 'dietary']},
                        'footer': {'component': 'footer_minimal'},
                    }
                },
            },
            # ── Anniversaire ─────────────────────────────
            {
                'organizer': olivier,
                'title': 'Anniversaire surprise de Diane — 30 ans',
                'event_type': Event.EventType.ANNIVERSAIRE,
                'description': (
                    "Chut, c'est une surprise ! Rejoignez-nous pour fêter les 30 ans de Diane. "
                    "Ambiance tropicale, musique live et buffet camerounais. "
                    "Rendez-vous à partir de 20h — soyez à l'heure pour la surprise !"
                ),
                'start_date': now + timedelta(days=10),
                'end_date': now + timedelta(days=10, hours=7),
                'location_address': 'Le Rooftop Bastos, Yaoundé, Cameroun',
                'latitude': 3.882778,
                'longitude': 11.516667,
                'status': Event.EventStatus.PUBLISHED,
                'visibility': Event.Visibility.PRIVATE,
                'ambiance': Event.Ambiance.FESTIF,
                'template_config': {
                    'palette': {'primary': '#FF6B4A', 'secondary': '#1A1A2E', 'accent': '#FFD93D'},
                    'zones': {
                        'header': {'component': 'hero_party', 'animation': 'confetti_burst'},
                        'rsvp': {'component': 'rsvp_fun', 'fields': ['name', 'phone', 'song_request']},
                    }
                },
            },
            # ── Conférence ───────────────────────────────
            {
                'organizer': amina,
                'title': 'Africa Tech Summit 2026',
                'event_type': Event.EventType.CONFERENCE,
                'description': (
                    "La plus grande conférence tech d'Afrique centrale réunit développeurs, "
                    "entrepreneurs et investisseurs pendant 3 jours. Keynotes, ateliers pratiques, "
                    "hackathon et networking. Plus de 500 participants attendus."
                ),
                'start_date': now + timedelta(days=90),
                'end_date': now + timedelta(days=92),
                'location_address': 'Palais des Congrès de Yaoundé, Cameroun',
                'latitude': 3.866667,
                'longitude': 11.516667,
                'status': Event.EventStatus.DRAFT,
                'visibility': Event.Visibility.PUBLIC,
                'ambiance': Event.Ambiance.PROFESSIONNEL,
                'is_online': True,
                'online_link': 'https://meet.google.com/abc-defg-hij',
                'template_config': {
                    'palette': {'primary': '#0066FF', 'secondary': '#0D0D0D', 'accent': '#00D4AA'},
                    'zones': {
                        'header': {'component': 'hero_tech', 'animation': 'gradient_shift'},
                        'schedule': {'component': 'timeline_vertical'},
                        'speakers': {'component': 'speaker_grid', 'columns': 3},
                    }
                },
            },
            # ── Concert ──────────────────────────────────
            {
                'organizer': olivier,
                'title': 'Afrobeats Night — Douala Edition',
                'event_type': Event.EventType.CONCERT,
                'description': (
                    "Une nuit 100% Afrobeats avec les meilleurs artistes de la scène camerounaise. "
                    "Line-up : Locko, Salatiel, Nabila & guests. Pré-ventes disponibles."
                ),
                'start_date': now + timedelta(days=20),
                'end_date': now + timedelta(days=20, hours=6),
                'location_address': 'Stade Omnisports de Douala, Cameroun',
                'latitude': 4.023611,
                'longitude': 9.695833,
                'status': Event.EventStatus.PUBLISHED,
                'visibility': Event.Visibility.PUBLIC,
                'ambiance': Event.Ambiance.FESTIF,
                'template_config': {
                    'palette': {'primary': '#9B59B6', 'secondary': '#1C1C1C', 'accent': '#F39C12'},
                },
            },
            # ── Soirée privée ────────────────────────────
            {
                'organizer': fatou,
                'title': 'Gala de charité — Fondation Espoir',
                'event_type': Event.EventType.SOIREE,
                'description': (
                    "Soirée de gala au profit de la Fondation Espoir pour l'éducation des jeunes filles. "
                    "Dîner 5 services, vente aux enchères et spectacle live. Tenue de soirée exigée."
                ),
                'start_date': now + timedelta(days=60),
                'end_date': now + timedelta(days=60, hours=5),
                'location_address': 'Hôtel Hilton, Yaoundé, Cameroun',
                'latitude': 3.870556,
                'longitude': 11.518611,
                'status': Event.EventStatus.PUBLISHED,
                'visibility': Event.Visibility.PRIVATE,
                'ambiance': Event.Ambiance.ELEGANT,
                'template_config': {
                    'palette': {'primary': '#D4AF37', 'secondary': '#1B1B2F', 'accent': '#E8E8E8'},
                },
            },
            # ── Événement passé (souvenir) ───────────────
            {
                'organizer': jean,
                'title': 'Conférence DevFest Douala 2025',
                'event_type': Event.EventType.CONFERENCE,
                'description': (
                    "Retour sur le DevFest Douala 2025 — une journée de conférences et d'ateliers "
                    "dédiée aux développeurs. Merci aux 300 participants !"
                ),
                'start_date': now - timedelta(days=180),
                'end_date': now - timedelta(days=180) + timedelta(hours=10),
                'location_address': 'Université de Douala, Cameroun',
                'latitude': 4.036944,
                'longitude': 9.7375,
                'status': Event.EventStatus.SOUVENIR,
                'visibility': Event.Visibility.PUBLIC,
                'ambiance': Event.Ambiance.PROFESSIONNEL,
                'view_count': 342,
            },
        ]

        created_events = []
        for e_data in events_data:
            event, created = Event.objects.get_or_create(
                title=e_data['title'],
                organizer=e_data['organizer'],
                defaults=e_data
            )
            created_events.append(event)
            self.stdout.write(f"  {'✅ Created' if created else '⏭️  Found'} event: {event.title}")

        # ─────────────────────────────────────────────────
        # 3. COLLABORATORS
        # ─────────────────────────────────────────────────
        collabs_data = [
            {
                'event': created_events[0],  # Mariage
                'user': paul,
                'permissions': {
                    'can_read': True,
                    'can_add_media': True,
                    'can_edit_components': False,
                    'can_manage_guests': False,
                },
            },
            {
                'event': created_events[2],  # Africa Tech Summit
                'user': jean,
                'permissions': {
                    'can_read': True,
                    'can_add_media': True,
                    'can_edit_components': True,
                    'can_manage_guests': True,
                },
            },
        ]

        for c_data in collabs_data:
            collab, created = EventCollaborator.objects.get_or_create(
                event=c_data['event'],
                user=c_data['user'],
                defaults={'permissions': c_data['permissions']}
            )
            self.stdout.write(
                f"  {'✅ Created' if created else '⏭️  Found'} collaborator: "
                f"{collab.user.full_name} on '{collab.event.title}'"
            )

        # ─────────────────────────────────────────────────
        # DONE
        # ─────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f"🎉 Database seeded successfully!\n"
            f"   → {len(users)} users\n"
            f"   → {len(created_events)} events\n"
            f"   → {len(collabs_data)} collaborators"
        ))
