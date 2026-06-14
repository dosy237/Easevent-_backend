from django.core.management.base import BaseCommand
from django.utils import timezone
from users.models import User
from events.models import Event
from datetime import timedelta
import random

class Command(BaseCommand):
    help = 'Seeds the database with initial realistic data'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting database seeding...")

        # 1. Create Users
        users_data = [
            {'email': 'alice@example.com', 'first_name': 'Alice', 'last_name': 'Dupont'},
            {'email': 'bob@example.com', 'first_name': 'Bob', 'last_name': 'Martin'},
            {'email': 'charlie@example.com', 'first_name': 'Charlie', 'last_name': 'Bernard'},
        ]
        
        users = []
        for u_data in users_data:
            user, created = User.objects.get_or_create(
                email=u_data['email'],
                defaults={
                    'first_name': u_data['first_name'],
                    'last_name': u_data['last_name'],
                    'is_verified': True
                }
            )
            if created:
                user.set_password('password123')
                user.save()
            users.append(user)
            self.stdout.write(f"{'Created' if created else 'Found'} user {user.email}")

        # 2. Create Events
        now = timezone.now()
        events_data = [
            {
                'organizer': users[0],
                'title': 'Mariage de Sarah & Marc',
                'event_type': Event.EventType.MARIAGE,
                'description': 'Rejoignez-nous pour célébrer notre union dans ce lieu magnifique.',
                'start_date': now + timedelta(days=30),
                'end_date': now + timedelta(days=30, hours=10),
                'location_address': 'Domaine des Oliviers, Provence',
                'status': Event.EventStatus.PUBLISHED,
                'visibility': Event.Visibility.PUBLIC,
                'ambiance': Event.Ambiance.ELEGANT,
            },
            {
                'organizer': users[1],
                'title': 'Anniversaire surprise de Tom (30 ans)',
                'event_type': Event.EventType.ANNIVERSAIRE,
                'description': 'Chut, c\'est une surprise ! Rendez-vous à partir de 20h.',
                'start_date': now + timedelta(days=5),
                'end_date': now + timedelta(days=6, hours=3),
                'location_address': 'Le RoofTop de Paris, 75011 Paris',
                'status': Event.EventStatus.LIVE,
                'visibility': Event.Visibility.PRIVATE,
                'ambiance': Event.Ambiance.FESTIF,
            },
            {
                'organizer': users[2],
                'title': 'Tech Innovators Conference 2026',
                'event_type': Event.EventType.CONFERENCE,
                'description': 'La grande conférence annuelle dédiée aux innovations tech.',
                'start_date': now + timedelta(days=60),
                'end_date': now + timedelta(days=62),
                'location_address': 'Palais des Congrès, Lyon',
                'status': Event.EventStatus.PUBLISHED,
                'visibility': Event.Visibility.PUBLIC,
                'ambiance': Event.Ambiance.PROFESSIONNEL,
            }
        ]

        for e_data in events_data:
            event, created = Event.objects.get_or_create(
                title=e_data['title'],
                organizer=e_data['organizer'],
                defaults=e_data
            )
            self.stdout.write(f"{'Created' if created else 'Found'} event '{event.title}'")

        self.stdout.write(self.style.SUCCESS("Successfully seeded database with realistic data!"))
