"""
users/models.py
═══════════════════════════════════════════════════════════════
Modèles de l'application "users".
Contient 3 tables :
  - User            → les comptes utilisateurs
  - UserPreferences → les préférences de notifications
  - Domain          → les domaines personnalisés (Plan Pro)
═══════════════════════════════════════════════════════════════
"""

import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


# ─────────────────────────────────────────────────────────────
# MANAGER PERSONNALISÉ
# ─────────────────────────────────────────────────────────────
# Django utilise normalement un "username" comme identifiant.
# On redéfinit le manager pour utiliser l'email à la place.
# Un manager, c'est l'objet qui sait comment créer un User.
# ─────────────────────────────────────────────────────────────
class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        """Crée et enregistre un utilisateur normal."""
        if not email:
            raise ValueError("L'adresse email est obligatoire")
        # normalize_email met le domaine en minuscules
        # ex: Sarah@GMAIL.COM → Sarah@gmail.com
        email = self.normalize_email(email)
        user  = self.model(email=email, **extra_fields)
        # set_password hache automatiquement le mot de passe avec bcrypt
        # JAMAIS stocker un mot de passe en clair
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Crée un superutilisateur (accès à l'interface admin Django)."""
        extra_fields.setdefault('is_staff',        True)
        extra_fields.setdefault('is_superuser',    True)
        extra_fields.setdefault('is_verified',     True)
        extra_fields.setdefault('subscription_plan', 'pro')
        return self.create_user(email, password, **extra_fields)


# ─────────────────────────────────────────────────────────────
# TABLE : users
# ─────────────────────────────────────────────────────────────
class User(AbstractBaseUser, PermissionsMixin):
    """
    Modèle utilisateur central d'Easevent.

    Pourquoi AbstractBaseUser et pas le User Django par défaut ?
    ─────────────────────────────────────────────────────────────
    Le User Django standard impose un champ "username".
    On veut l'email comme identifiant de connexion.
    AbstractBaseUser permet de repartir de zéro et de tout
    contrôler — c'est plus de travail mais plus de liberté.

    Pourquoi UUID comme clé primaire ?
    ────────────────────────────────────
    Un entier auto-incrémenté (1, 2, 3...) est devinable dans
    l'URL : /users/1/, /users/2/... Un UUID est impossible
    à deviner : /users/550e8400-e29b-41d4-a716-446655440000/
    C'est une mesure de sécurité fondamentale.
    """

    # ── Choix possibles pour les champs ENUM ──────────────────
    # TextChoices génère automatiquement les valeurs valides
    class SubscriptionPlan(models.TextChoices):
        FREE     = 'free',     'Gratuit'
        STANDARD = 'standard', 'Standard'
        PRO      = 'pro',      'Pro'

    class OAuthProvider(models.TextChoices):
        GOOGLE = 'google', 'Google'
        APPLE  = 'apple',  'Apple'

    # ── Identité ──────────────────────────────────────────────
    id = models.UUIDField(
        primary_key = True,
        default     = uuid.uuid4,   # génère un UUID automatiquement
        editable    = False,         # ne peut pas être modifié manuellement
        verbose_name = "Identifiant UUID"
    )
    email = models.EmailField(
        max_length = 254,
        unique     = True,   # deux utilisateurs ne peuvent pas avoir le même email
        db_index   = True,   # index PostgreSQL pour les recherches rapides
        verbose_name = "Adresse email"
    )
    first_name = models.CharField(max_length=50, verbose_name="Prénom")
    last_name  = models.CharField(max_length=50, verbose_name="Nom")
    avatar_url = models.URLField(
        max_length   = 512,
        blank        = True,
        null         = True,
        verbose_name = "URL photo de profil (Cloudflare R2)"
    )
    bio = models.CharField(
        max_length   = 250,
        blank        = True,
        default      = '',
        verbose_name = "Biographie publique"
    )

    # ── Statut du compte ──────────────────────────────────────
    is_verified = models.BooleanField(
        default      = False,
        verbose_name = "Email vérifié",
        help_text    = "Passe à True après clic sur le lien de confirmation"
    )

    # ── Abonnement actuel ─────────────────────────────────────
    subscription_plan = models.CharField(
        max_length   = 10,
        choices      = SubscriptionPlan.choices,
        default      = SubscriptionPlan.FREE,
        verbose_name = "Plan d'abonnement"
    )

    # ── Stripe ────────────────────────────────────────────────
    # On stocke UNIQUEMENT l'ID client Stripe — jamais de CB
    stripe_customer_id = models.CharField(
        max_length   = 64,
        blank        = True,
        null         = True,
        unique       = True,
        verbose_name = "ID client Stripe"
    )

    # ── Connexion sociale (Google / Apple) ────────────────────
    oauth_provider = models.CharField(
        max_length   = 10,
        choices      = OAuthProvider.choices,
        blank        = True,
        null         = True,
        verbose_name = "Fournisseur OAuth"
    )
    oauth_uid = models.CharField(
        max_length   = 128,
        blank        = True,
        null         = True,
        verbose_name = "Identifiant retourné par Google/Apple"
    )

    # ── Champs requis par Django (AbstractBaseUser) ───────────
    is_active = models.BooleanField(default=True)
    is_staff  = models.BooleanField(default=False)

    # ── Soft delete RGPD ──────────────────────────────────────
    # On ne supprime JAMAIS une ligne physiquement.
    # deleted_at = null  → compte actif
    # deleted_at = date  → compte supprimé (mais données conservées
    #                       pour l'intégrité des événements passés)
    deleted_at = models.DateTimeField(
        null         = True,
        blank        = True,
        verbose_name = "Date de suppression (soft delete)"
    )

    # ── Timestamps automatiques ───────────────────────────────
    # auto_now_add = True → rempli automatiquement à la création
    # auto_now = True     → mis à jour automatiquement à chaque sauvegarde
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Configuration du manager et de l'identifiant ─────────
    USERNAME_FIELD  = 'email'                        # identifiant de connexion
    REQUIRED_FIELDS = ['first_name', 'last_name']    # champs obligatoires en plus
    objects         = UserManager()                  # notre manager personnalisé

    class Meta:
        db_table            = 'users'       # nom exact de la table en PostgreSQL
        verbose_name        = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['subscription_plan']),
            models.Index(fields=['deleted_at']),
        ]

    def __str__(self):
        """Représentation lisible dans l'admin Django."""
        return f"{self.first_name} {self.last_name} <{self.email}>"

    @property
    def full_name(self):
        """Propriété calculée — pas stockée en base."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_deleted(self):
        """True si le compte a été supprimé (soft delete)."""
        return self.deleted_at is not None


# ─────────────────────────────────────────────────────────────
# TABLE : user_preferences
# ─────────────────────────────────────────────────────────────
class UserPreferences(models.Model):
    """
    Préférences de notifications et d'interface de chaque utilisateur.

    Relation OneToOne avec User :
    → Un utilisateur a exactement un ensemble de préférences.
    → Si l'utilisateur est supprimé (CASCADE), ses préférences
      sont supprimées automatiquement.

    Cette table est créée automatiquement via un signal Django
    (voir users/signals.py) - on n'a jamais besoin de l'appeler
    manuellement.
    """

    id   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete    = models.CASCADE,   # si le User est supprimé → supprime aussi les prefs
        related_name = 'preferences',    # permet d'écrire user.preferences depuis le code
        verbose_name = "Utilisateur"
    )

    # ── Notifications (chaque type peut être activé/désactivé) ──
    notif_invitation     = models.BooleanField(default=True,  verbose_name="Invitation reçue")
    notif_confirmation   = models.BooleanField(default=True,  verbose_name="Quelqu'un confirme mon événement")
    notif_event_modified = models.BooleanField(default=True,  verbose_name="Un événement modifié")
    notif_comment        = models.BooleanField(default=True,  verbose_name="Nouveau commentaire")
    notif_message        = models.BooleanField(default=True,  verbose_name="Nouveau message direct")
    notif_reminder       = models.BooleanField(default=True,  verbose_name="Rappels J-7 / J-1 / Jour J")
    notif_feedback_req   = models.BooleanField(default=True,  verbose_name="Demande de feedback")
    notif_daily_report   = models.BooleanField(default=True,  verbose_name="Bilan quotidien à 20h")
    notif_final_report   = models.BooleanField(default=True,  verbose_name="Rapport final disponible")
    notif_payment        = models.BooleanField(default=True,  verbose_name="Paiement confirmé/échoué")

    # ── Interface ─────────────────────────────────────────────
    geolocation_enabled = models.BooleanField(
        default      = False,
        verbose_name = "Géolocalisation activée pour le fil d'actualité"
    )
    language = models.CharField(
        max_length   = 5,
        default      = 'fr',
        verbose_name = "Langue de l'interface (code ISO)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'user_preferences'
        verbose_name = 'Préférences utilisateur'

    def __str__(self):
        return f"Préférences de {self.user.full_name}"


# ─────────────────────────────────────────────────────────────
# TABLE : domains  (Plan Pro uniquement)
# ─────────────────────────────────────────────────────────────
class Domain(models.Model):
    """
    Domaine personnalisé acheté automatiquement via l'API Namecheap
    quand un utilisateur souscrit au Plan Pro.

    Exemple : l'utilisateur choisit "mes-events.com"
    → Easevent achète le domaine automatiquement
    → Chaque événement créé sera sur : mon-event.mes-events.com
    """

    class DomainStatus(models.TextChoices):
        PENDING = 'pending', 'En attente d\'achat'
        ACTIVE  = 'active',  'Actif'
        EXPIRED = 'expired', 'Expiré'
        FAILED  = 'failed',  'Échec d\'achat'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user        = models.ForeignKey(
        User,
        on_delete    = models.CASCADE,
        related_name = 'domains',
        verbose_name = "Propriétaire"
    )
    domain_name = models.CharField(
        max_length   = 253,
        unique       = True,
        verbose_name = "Nom de domaine (ex: mes-events.com)"
    )
    status = models.CharField(
        max_length = 10,
        choices    = DomainStatus.choices,
        default    = DomainStatus.PENDING
    )
    namecheap_order_id = models.CharField(
        max_length   = 64,
        blank        = True,
        null         = True,
        verbose_name = "ID de commande Namecheap"
    )
    purchased_at = models.DateTimeField(null=True, blank=True, verbose_name="Date d'achat")
    expires_at   = models.DateTimeField(null=True, blank=True, verbose_name="Date d'expiration")
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'domains'
        verbose_name = 'Domaine personnalisé'

    def __str__(self):
        return f"{self.domain_name} ({self.get_status_display()})"
# ─────────────────────────────────────────────────────────────────
# TABLE : email_verifications
# Stocke les tokens de vérification email temporaires.
# Chaque token expire après 24 heures.
# ─────────────────────────────────────────────────────────────────
class EmailVerification(models.Model):
    """
    Token de vérification email.
    Créé à l'inscription, supprimé après vérification.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.OneToOneField(
        User,
        on_delete    = models.CASCADE,
        related_name = 'email_verification'
    )
    # Token aléatoire de 64 caractères — envoyé dans le lien email
    token      = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = 'email_verifications'

    def is_expired(self):
        """Retourne True si le token a expiré."""
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"Vérification email pour {self.user.email}"
    