"""
invitations/models.py
═══════════════════════════════════════════════════════════════
Modèles de l'application "invitations".
Contient 3 tables :
  - Invitation    → les invitations personnelles
  - RSVPQuestion  → les questions personnalisées de confirmation
  - RSVPResponse  → les réponses des invités aux questions
═══════════════════════════════════════════════════════════════
"""

import uuid
from django.db import models


# ─────────────────────────────────────────────────────────────
# TABLE : invitations
# ─────────────────────────────────────────────────────────────
class Invitation(models.Model):
    """
    Invitation personnelle et non transférable.

    Deux canaux possibles :
    ───────────────────────
    1. platform_notification → membre inscrit sur Easevent
       invited_user est renseigné, phone_number est vide

    2. sms → contact externe non inscrit
       phone_number est renseigné, invited_user est vide
       En production : champ ENCRYPTED (AES-256) via
       django-encrypted-fields (obligation RGPD)

    Le token d'accès :
    ───────────────────
    Généré avec secrets.token_urlsafe(32) en Python.
    → 32 octets = 256 bits d'entropie
    → 43 caractères en base64url (URL-safe)
    → Impossible à deviner par force brute
    → Donne accès au mini-site sans compte sur la plateforme
    """

    class InvitationStatus(models.TextChoices):
        SENT      = 'sent',      'Envoyée'
        OPENED    = 'opened',    'Ouverte'
        CONFIRMED = 'confirmed', 'Confirmée'
        DECLINED  = 'declined',  'Déclinée'
        REVOKED   = 'revoked',   'Révoquée'
        EXPIRED   = 'expired',   'Expirée'

    class InvitationChannel(models.TextChoices):
        SMS           = 'sms',                   'SMS (via Twilio)'
        PLATFORM_NOTIF= 'platform_notification',  'Notification in-app'

    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        'events.Event',
        on_delete    = models.CASCADE,
        related_name = 'invitations',
        verbose_name = "Événement concerné"
    )

    # ── Destinataire (l'un ou l'autre, jamais les deux) ───────
    invited_user = models.ForeignKey(
        'users.User',
        on_delete    = models.SET_NULL,
        null         = True,
        blank        = True,
        related_name = 'received_invitations',
        verbose_name = "Membre invité (si inscrit sur Easevent)"
    )
    # NOTE PRODUCTION : remplacer par EncryptedCharField
    # from encrypted_fields.fields import EncryptedCharField
    # pour le chiffrement AES-256 automatique (obligation RGPD)
    phone_number = models.CharField(
        max_length   = 30,
        blank        = True,
        null         = True,
        verbose_name = "Numéro de téléphone (chiffré AES-256 en production)"
    )

    # ── Token d'accès unique ──────────────────────────────────
    token = models.CharField(
        max_length   = 43,
        unique       = True,
        verbose_name = "Token URL-safe 256 bits",
        help_text    = "Généré avec secrets.token_urlsafe(32)"
    )

    # ── Statut et canal ───────────────────────────────────────
    status  = models.CharField(max_length=15, choices=InvitationStatus.choices, default='sent')
    channel = models.CharField(max_length=25, choices=InvitationChannel.choices)

    # ── Traçabilité complète ──────────────────────────────────
    # Ces dates permettent de savoir exactement quand chaque
    # action s'est produite — utile pour les analytics
    sent_at      = models.DateTimeField(auto_now_add=True, verbose_name="Date d'envoi")
    opened_at    = models.DateTimeField(null=True, blank=True, verbose_name="Date d'ouverture")
    responded_at = models.DateTimeField(null=True, blank=True, verbose_name="Date de réponse")
    expires_at   = models.DateTimeField(verbose_name="Date d'expiration (J+7 après la fin de l'événement)")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invitations'
        indexes  = [
            models.Index(fields=['token']),
            models.Index(fields=['event', 'status']),
            models.Index(fields=['invited_user', 'status']),
        ]

    def __str__(self):
        dest = self.invited_user.full_name if self.invited_user else self.phone_number
        return f"Invitation → {dest} | {self.event.title} ({self.status})"

    @property
    def is_valid(self):
        """True si le token est encore valide (non expiré, non révoqué)."""
        from django.utils import timezone
        return (
            self.status not in ['revoked', 'expired']
            and self.expires_at > timezone.now()
        )


# ─────────────────────────────────────────────────────────────
# TABLE : rsvp_questions
# ─────────────────────────────────────────────────────────────
class RSVPQuestion(models.Model):
    """
    Questions personnalisées posées à l'invité lors de sa confirmation.

    Exemples selon le type d'événement :
    ──────────────────────────────────────
    Mariage      → "Avez-vous des restrictions alimentaires ?" (text)
    Mariage      → "Avez-vous besoin de la navette ?" (yes_no)
    Conférence   → "Quelle session vous intéresse ?" (radio)
    Anniversaire → "Choix du menu ?" (checkbox)

    Maximum 5 questions par événement.
    """

    class QuestionType(models.TextChoices):
        TEXT     = 'text',     'Texte libre'
        RADIO    = 'radio',    'Choix unique'
        CHECKBOX = 'checkbox', 'Choix multiple'
        YES_NO   = 'yes_no',   'Oui / Non'

    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        'events.Event',
        on_delete    = models.CASCADE,
        related_name = 'rsvp_questions',
        verbose_name = "Événement"
    )

    question_text = models.TextField(verbose_name="Texte de la question")
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    is_required   = models.BooleanField(default=False, verbose_name="Obligatoire")
    order = models.PositiveSmallIntegerField(default=0, verbose_name="Ordre d'affichage")

    # Options pour les types radio et checkbox
    # Exemple : ["Végétarien", "Vegan", "Sans gluten", "Aucune restriction"]
    options = models.JSONField(
        null  = True,
        blank = True,
        verbose_name = "Options disponibles (pour radio et checkbox)"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rsvp_questions'
        ordering = ['order']

    def __str__(self):
        return f"Q{self.order}: {self.question_text[:50]}... ({self.question_type})"


# ─────────────────────────────────────────────────────────────
# TABLE : rsvp_responses
# ─────────────────────────────────────────────────────────────
class RSVPResponse(models.Model):
    """
    Réponse d'un invité à une question RSVP spécifique.

    Liée à la fois à la question et à l'invitation, ce qui
    permet de faire des statistiques croisées dans le dashboard.

    unique_together garantit qu'un invité ne répond qu'une
    seule fois à chaque question.
    """

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question   = models.ForeignKey(
        RSVPQuestion,
        on_delete    = models.CASCADE,
        related_name = 'responses'
    )
    invitation = models.ForeignKey(
        Invitation,
        on_delete    = models.CASCADE,
        related_name = 'rsvp_responses'
    )

    # La réponse est toujours stockée en texte.
    # Pour les checkboxes (choix multiple), c'est un JSON sérialisé :
    # ex: '["Option A", "Option B"]'
    answer = models.TextField(verbose_name="Réponse de l'invité")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'rsvp_responses'
        unique_together = [['question', 'invitation']]

    def __str__(self):
        return f"Réponse à '{self.question.question_text[:30]}...'"
