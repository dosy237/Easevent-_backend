"""
invitations/models.py
═══════════════════════════════════════════════════════════════
Modèles de l'application "invitations".
Contient la table :
  - Invitation    → les invitations personnelles
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
        EMAIL         = 'email',                 'Email (M30)'
        PLATFORM_NOTIF= 'platform_notification',  'Notification in-app'

    class DeliveryStatus(models.TextChoices):
        PENDING        = 'pending',        'En cours'
        SENT           = 'sent',           'Envoyé'
        FAILED         = 'failed',         'Échec'
        NOT_CONFIGURED = 'not_configured', 'Canal non configuré'
        IN_APP         = 'in_app',         "Dans l'application"

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
    # Numéro chiffré (voir invitations/crypto.py) + empreinte pour les doublons.
    # Utiliser inv.phone (lecture) et inv.set_phone() (écriture).
    phone_number = models.CharField(
        max_length   = 255,
        blank        = True,
        null         = True,
        verbose_name = "Numéro de téléphone (chiffré)"
    )
    phone_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True,
                                  verbose_name="Empreinte HMAC du numéro")

    # Invitation par email d'une personne sans compte (M29 / M30)
    email = models.EmailField(max_length=254, null=True, blank=True, verbose_name="Email invité")
    # Nom facultatif (colonne « name » de l'import CSV)
    contact_name = models.CharField(max_length=80, blank=True, default='', verbose_name="Nom du contact")
    # Message personnalisé de l'organisateur (100 caractères, M12)
    message = models.CharField(max_length=100, blank=True, default='', verbose_name="Message personnalisé")

    # ── Token d'accès unique ──────────────────────────────────
    # Seule l'empreinte SHA-256 est stockée : une fuite de la base ne
    # donne accès à aucune invitation. Le jeton en clair n'existe que
    # dans le lien envoyé (email / SMS) ; une relance en crée un nouveau.
    token = models.CharField(
        max_length   = 64,
        unique       = True,
        verbose_name = "Empreinte SHA-256 du token (256 bits)",
        help_text    = "Jeton généré avec secrets.token_urlsafe(32), stocké haché"
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

    # ── Envoi et relances ─────────────────────────────────────
    delivery_status = models.CharField(max_length=15, choices=DeliveryStatus.choices, default='pending')
    reminded_at  = models.DateTimeField(null=True, blank=True, verbose_name="Dernière relance")
    remind_count = models.PositiveSmallIntegerField(default=0)

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
        dest = self.invited_user.full_name if self.invited_user else (self.email or 'SMS')
        return f"Invitation → {dest} | {self.event.title} ({self.status})"

    @property
    def phone(self):
        from .crypto import decrypt
        return decrypt(self.phone_number)

    def set_phone(self, e164):
        from .crypto import blind_index, encrypt
        self.phone_number = encrypt(e164) if e164 else None
        self.phone_hash = blind_index(e164) if e164 else None

    @property
    def is_valid(self):
        """True si le token est encore valide (non expiré, non révoqué)."""
        from django.utils import timezone
        return (
            self.status not in ['revoked', 'expired']
            and self.expires_at > timezone.now()
        )


# Les questions RSVP et les réponses des invités sont dans l'app « rsvp ».
