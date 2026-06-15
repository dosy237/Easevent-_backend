from django.core.management.base import BaseCommand
from django.utils import timezone
from users.models import User
from events.models import Event, EventCollaborator
from invitations.models import Invitation
from django.utils.text import slugify
from datetime import timedelta


class Command(BaseCommand):
    help = 'Seeds the database with realistic production-quality data'

    def handle(self, *args, **kwargs):
        self.stdout.write("🌱 Starting database seeding...")

        # ─────────────────────────────────────────────────
        # 1. USERS
        # ─────────────────────────────────────────────────
        users_data = [
            {'email': 'jean.mbarga@gmail.com', 'first_name': 'Jean', 'last_name': 'Mbarga', 'bio': 'Organisateur Douala', 'plan': 'pro'},
            {'email': 'fatou.diallo@outlook.com', 'first_name': 'Fatou', 'last_name': 'Diallo', 'bio': 'Passionnée Déco', 'plan': 'standard'},
            {'email': 'paul.nguesso@yahoo.fr', 'first_name': 'Paul', 'last_name': 'Nguesso', 'bio': 'Photographe Pro', 'plan': 'free'},
            {'email': 'amina.bello@gmail.com', 'first_name': 'Amina', 'last_name': 'Bello', 'bio': 'Tech Afrique', 'plan': 'pro'},
            {'email': 'olivier.kamga@hotmail.com', 'first_name': 'Olivier', 'last_name': 'Kamga', 'bio': 'DJ Yaoundé', 'plan': 'standard'},
        ]

        users = []
        for u_data in users_data:
            user, created = User.objects.get_or_create(
                email=u_data['email'],
                defaults={
                    'first_name': u_data['first_name'],
                    'last_name': u_data['last_name'],
                    'bio': u_data['bio'],
                    'subscription_plan': u_data['plan'],
                    'is_verified': True,
                }
            )
            if created:
                user.set_password('Easevent2026!')
                user.save()
            users.append(user)
        
        # ─────────────────────────────────────────────────
        # 2. IMAGES & TYPES
        # ─────────────────────────────────────────────────
        import os
        import shutil
        from django.conf import settings
        
        img_dir = os.path.join(settings.BASE_DIR, 'seed', 'images')
        media_events_dir = os.path.join(settings.MEDIA_ROOT, 'events')
        os.makedirs(media_events_dir, exist_ok=True)
        
        images = [f for f in os.listdir(img_dir) if f.endswith(('.png', '.jpg', '.jpeg'))]
        
        event_types = [
            (Event.EventType.MARIAGE, "Cérémonie de Mariage", ["Sarah & Marc", "Pauline & Kevin", "Aicha & Omar", "Bella & Junior", "Cathy & David"]),
            (Event.EventType.CONFERENCE, "Conférence Tech", ["AI Summit 2026", "Web 3.0 Africa", "Django Masterclass", "Cloud Expo", "Startup Day"]),
            (Event.EventType.ANNIVERSAIRE, "Fête d'Anniversaire", ["Les 30 ans de Carole", "Birthday Bash - Fred", "Garden Party - Lily", "Surprise 25th", "Sweet 16"]),
            (Event.EventType.SOIREE, "Soirée Networking", ["Cocktail Business", "Afterwork Bastos", "Gala des Alumni", "Nuit de l'Entrepreneuriat", "Networking VIP"]),
            (Event.EventType.CONCERT, "Live Concert", ["Afro-Jazz Night", "Rock in Yaoundé", "Rap Contest 2k26", "Acoustic Session", "Festival des Voix"]),
            (Event.EventType.SEMINAIRE, "Séminaire Stratégique", ["Leadership Workshop", "Sales Training", "HR Strategy 2026", "Digital Marketing", "Innovation Sprint"]),
            (Event.EventType.GALA, "Gala de Charité", ["Dîner Foundation", "Bal Masqué", "Gala Lumière", "Espoir pour Demain", "Nuit Rouge et Noir"]),
            (Event.EventType.EXPOSITION, "Exposition d'Art", ["Galerie Douala", "Photo Africa", "Sculpture Expo", "Modern Art Show", "Digital Art"]),
            (Event.EventType.FESTIVAL, "Festival Culturel", ["Ngondo Festival", "Nguon Yaoundé", "Foodies Fest", "Cinema Week", "Heritage Day"]),
            (Event.EventType.ATELIER, "Atelier Créatif", ["Masterclass Cuisine", "Workshop Poterie", "Make-up Class", "Yoga Retreat", "Painting Workshop"]),
        ]

        # ─────────────────────────────────────────────────
        # 3. SEED 50 EVENTS
        # ─────────────────────────────────────────────────
        now = timezone.now()
        count = 0
        import random
        
        for e_type, base_title, suffixes in event_types:
            for i in range(5):
                user = random.choice(users)
                title = f"{base_title} : {suffixes[i]}"
                img_name = images[(count) % len(images)] if images else None
                
                relative_img_path = None
                if img_name:
                    source_path = os.path.join(img_dir, img_name)
                    # Create a unique name to avoid conflicts if needed, but here we just copy
                    dest_name = f"seed_{count}_{img_name}"
                    dest_path = os.path.join(media_events_dir, dest_name)
                    shutil.copy(source_path, dest_path)
                    relative_img_path = f"events/{dest_name}"
                
                event = Event.objects.create(
                    organizer=user,
                    title=title,
                    event_type=e_type,
                    description=f"Description pour l'événement {title}. Un moment inoubliable à ne pas manquer !",
                    start_date=now + timedelta(days=random.randint(1, 30), hours=random.randint(1, 10)),
                    end_date=now + timedelta(days=31, hours=random.randint(11, 24)),
                    location_address=f"Place de l'événement {random.randint(1, 100)}, Cameroun",
                    status=Event.EventStatus.PUBLISHED,
                    visibility=Event.Visibility.PUBLIC,
                    cover_image=relative_img_path,
                    subdomain=slugify(f"{title}-{random.randint(100, 999)}")
                )
                count += 1
        
        self.stdout.write(self.style.SUCCESS(f"🎉 Created {count} realistic events across 10 types with images copied to media!"))

