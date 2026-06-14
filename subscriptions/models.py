"""
subscriptions/models.py
═══════════════════════════════════════════════════════════════
Modèle de l'application "subscriptions".
Contient 1 table :
  - Subscription → les abonnements aux plans payants
═══════════════════════════════════════════════════════════════
"""

import uuid
from django.db import models


# ─────────────────────────────────────────────────────────────
# TABLE : subscriptions
# ─────────────────────────────────────────────────────────────
class Subscription(models.Model):
    """
    Abonnement d'un utilisateur à un plan payant.

    IMPORTANT — Comment ça fonctionne avec Stripe :
    ─────────────────────────────────────────────────
    Easevent ne gère JAMAIS les paiements directement.
    On ne stocke JAMAIS de numéro de carte.

    Le flux est le suivant :
    1. L'utilisateur clique "S'abonner" dans l'app
    2. Stripe Payment Sheet s'ouvre (interface native Stripe)
    3. L'utilisateur entre sa CB directement chez Stripe
    4. Stripe traite le paiement
    5. Stripe envoie un webhook HTTP à notre endpoint
       /api/stripe/webhook/
    6. Notre code met à jour le statut dans cette table

    Les webhooks Stripe qui modifient cette table :
    ────────────────────────────────────────────────
    - customer.subscription.created  → INSERT (status = inactive)
    - invoice.payment_succeeded      → UPDATE status = 'active'
    - invoice.payment_failed         → UPDATE status = 'past_due'
    - customer.subscription.paused   → UPDATE status = 'paused'
    - customer.subscription.deleted  → UPDATE status = 'canceled'

    stripe_sub_id :
    ────────────────
    C'est l'identifiant de l'abonnement côté Stripe.
    On ne stocke QUE cet ID — ça suffit pour interroger
    Stripe si on a besoin de plus d'informations.
    """

    class SubscriptionStatus(models.TextChoices):
        INACTIVE  = 'inactive',  'Inactif'
        ACTIVE    = 'active',    'Actif'
        PAUSED    = 'paused',    'En pause'
        PAST_DUE  = 'past_due',  'Paiement en retard'
        CANCELED  = 'canceled',  'Annulé'

    class Plan(models.TextChoices):
        FREE     = 'free',     'Gratuit'
        STANDARD = 'standard', 'Standard (9,99€/mois)'
        PRO      = 'pro',      'Pro (24,99€/mois)'

    id   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User',
        on_delete    = models.CASCADE,
        related_name = 'subscriptions',
        verbose_name = "Utilisateur abonné"
    )

    # ── Plan et statut ────────────────────────────────────────
    plan   = models.CharField(max_length=10, choices=Plan.choices, default=Plan.FREE)
    status = models.CharField(
        max_length = 10,
        choices    = SubscriptionStatus.choices,
        default    = SubscriptionStatus.INACTIVE
    )

    # ── Référence Stripe ──────────────────────────────────────
    # Exemple : "sub_1PkA3mExxxxxLVXz"
    stripe_sub_id = models.CharField(
        max_length   = 64,
        unique       = True,
        null         = True,
        blank        = True,
        verbose_name = "ID abonnement Stripe"
    )

    # ── Période de facturation ────────────────────────────────
    # current_period_end est important : même après annulation,
    # l'utilisateur garde l'accès jusqu'à cette date
    current_period_start = models.DateTimeField(null=True, blank=True, verbose_name="Début de la période")
    current_period_end   = models.DateTimeField(
        null         = True,
        blank        = True,
        verbose_name = "Fin de la période payée",
        help_text    = "L'accès reste actif jusqu'à cette date même après annulation"
    )

    # ── Annulation ────────────────────────────────────────────
    cancel_at   = models.DateTimeField(null=True, blank=True, verbose_name="Date d'annulation programmée")
    canceled_at = models.DateTimeField(null=True, blank=True, verbose_name="Date d'annulation effective")

    # ── Fréquence de facturation ──────────────────────────────
    billing_interval = models.CharField(
        max_length = 10,
        choices    = [('monthly', 'Mensuel'), ('annual', 'Annuel')],
        default    = 'monthly',
        verbose_name = "Fréquence de facturation"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'subscriptions'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['stripe_sub_id']),
        ]

    def __str__(self):
        return f"{self.user.full_name} — Plan {self.plan} ({self.status})"

    @property
    def is_active(self):
        """True si l'abonnement est en cours de validité."""
        return self.status == self.SubscriptionStatus.ACTIVE
