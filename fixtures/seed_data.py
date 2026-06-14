"""
fixtures/seed_data.py
═══════════════════════════════════════════════════════════════════════════════
Script d'injection de données de test pour Easevent (Version corrigée)
═══════════════════════════════════════════════════════════════════════════════
"""

from datetime import timedelta
from django.utils import timezone
from django.db import transaction
import secrets

from users.models import User, UserPreferences
from events.models import Event
from invitations.models import Invitation, RSVPQuestion, RSVPResponse
from analytics.models import Feedback
from subscriptions.models import Subscription


print("\n" + "═" * 75)
print("SEED EASEVENT - VERSION CORRIGÉE")
print("═" * 75)

def dt(days=0):
    return timezone.now() + timedelta(days=days)

with transaction.atomic():

    # === 1. NETTOYAGE ===
    print("\n[1/5] Nettoyage des données...")
    RSVPResponse.objects.all().delete()
    RSVPQuestion.objects.all().delete()
    Feedback.objects.all().delete()
    Invitation.objects.all().delete()
    Event.objects.all().delete()
    Subscription.objects.all().delete()
    UserPreferences.objects.all().delete()
    User.objects.all().delete()
    print("      ✓ Nettoyage terminé.")

    # === 2. UTILISATEURS ===
    print("\n[2/5] Création des utilisateurs...")

    admin = User.objects.create_superuser(
        email="admin@easevent.app", password="Admin@2025!",
        first_name="Admin", last_name="Easevent"
    )

    sarah = User.objects.create_user(
        email="sarah.dupont@gmail.com", password="Sarah@2025!",
        first_name="Sarah", last_name="Dupont", is_verified=True, subscription_plan="pro"
    )

    julien = User.objects.create_user(
        email="julien.martin@coaching-life.fr", password="Julien@2025!",
        first_name="Julien", last_name="Martin", is_verified=True, subscription_plan="standard"
    )

    marie = User.objects.create_user(
        email="marie.leclerc@hotmail.fr", password="Marie@2025!",
        first_name="Marie", last_name="Leclerc", is_verified=True
    )

    thomas = User.objects.create_user(
        email="thomas.bernard@outlook.com", password="Thomas@2025!",
        first_name="Thomas", last_name="Bernard", is_verified=True
    )

    for user in User.objects.all():
        UserPreferences.objects.get_or_create(user=user)

    Subscription.objects.create(user=sarah, plan="pro", status="active")
    Subscription.objects.create(user=julien, plan="standard", status="active")

    print(f"      ✓ {User.objects.count()} utilisateurs créés.")

    # === 3. ÉVÉNEMENTS ===
    print("\n[3/5] Création des événements...")

    mariage = Event.objects.create(
        organizer=sarah,
        title="Mariage de Sarah & Thomas",
        event_type="mariage",
        description="Célébration de notre union au Château de Vaux-le-Vicomte.",
        start_date=dt(120),
        end_date=dt(120) + timedelta(hours=9),
        location_address="Château de Vaux-le-Vicomte, 77950 Maincy",
        visibility="private", status="published",
        cover_image="https://images.pexels.com/photos/1024993/pexels-photo-1024993.jpeg",
    )

    conference = Event.objects.create(
        organizer=julien,
        title="Masterclass Personal Branding 2025",
        event_type="conference",
        description="Apprenez à construire votre marque personnelle.",
        start_date=dt(30),
        end_date=dt(30) + timedelta(hours=3, minutes=30),
        location_address="WeWork Nation, Paris",
        visibility="public", status="published",
        cover_image="https://images.pexels.com/photos/1181406/pexels-photo-1181406.jpeg",
    )

    print(f"      ✓ {Event.objects.count()} événements créés.")

    # === 4. INVITATIONS + RSVP (CORRIGÉ) ===
    print("\n[4/5] Création des invitations et RSVP...")

    # Invitation avec expires_at (obligatoire)
    inv_marie = Invitation.objects.create(
        event=mariage,
        invited_user=marie,
        token=secrets.token_urlsafe(32),
        status="confirmed",
        channel="platform_notification",
        expires_at=dt(30),                    # ← Correction ici
    )

    q_alim = RSVPQuestion.objects.create(
        event=mariage, order=1,
        question_text="Avez-vous des restrictions alimentaires ?",
        question_type="text", is_required=False
    )

    RSVPResponse.objects.create(
        question=q_alim, invitation=inv_marie, answer="Allergie aux fruits à coque"
    )

    print(f"      ✓ {Invitation.objects.count()} invitations créées.")

    # === 5. FEEDBACKS ===
    print("\n[5/5] Création des feedbacks...")

    Feedback.objects.create(
        event=conference, author=thomas, rating=5,
        comment="Masterclass excellente et très concrète.",
        is_anonymous=False, sentiment="positive", sentiment_score=0.95
    )

    print(f"      ✓ {Feedback.objects.count()} feedbacks créés.")

print("\n" + "═" * 75)
print("SEED TERMINÉ AVEC SUCCÈS")
print("═" * 75)
print("\nComptes de test disponibles :")
for u in User.objects.all():
    print(f"   {u.email} → {u.subscription_plan}")
print()