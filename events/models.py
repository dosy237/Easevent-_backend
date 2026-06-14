"""
events/models.py
═══════════════════════════════════════════════════════════════
Modèles de l'application "events".
Contient 4 tables :
  - Event               → les événements créés par les organisateurs
  - EventMedia          → les photos/vidéos uploadées
  - TemplateGeneration  → les designs générés par l'API Claude
  - EventCollaborator   → les droits délégués aux collaborateurs
═══════════════════════════════════════════════════════════════
"""

import uuid
from django.db import models


# ─────────────────────────────────────────────────────────────
# TABLE : events
# ─────────────────────────────────────────────────────────────
class Event(models.Model):
    """
    Table centrale d'Easevent. Représente un événement.

    Points techniques importants :
    ──────────────────────────────
    ① template_config → JSONField (PostgreSQL JSONB)
       Stocke toute la configuration du mini-site (composants,
       animations, couleurs) en un seul champ JSON. PostgreSQL
       JSONB permet de faire des requêtes directement dans le JSON.

    ② latitude/longitude → DECIMAL(9,6)
       9 chiffres au total, 6 après la virgule.
       Exemple : 48.856614 (Paris) → précision au centimètre.

    ③ DateTimeField → toujours stocké en UTC
       USE_TZ = True dans settings.py → Django gère
       automatiquement la conversion selon le fuseau horaire.

    ④ soft delete via deleted_at
       On ne supprime jamais une ligne en base. deleted_at non
       null signifie "événement supprimé". Les événements publics
       passés restent visibles avec "Événement supprimé".
    """

    # ── Statuts possibles de l'événement ─────────────────────
    class EventType(models.TextChoices):
        MARIAGE      = 'mariage',      'Mariage'
        CONFERENCE   = 'conference',   'Conférence'
        ANNIVERSAIRE = 'anniversaire', 'Anniversaire'
        SOIREE       = 'soiree',       'Soirée'
        CONCERT      = 'concert',      'Concert'
        AUTRE        = 'autre',        'Autre'

    class Visibility(models.TextChoices):
        PUBLIC  = 'public',  'Public'
        PRIVATE = 'private', 'Privé'
        DRAFT   = 'draft',   'Brouillon'

    class EventStatus(models.TextChoices):
        DRAFT     = 'draft',     'Brouillon'
        PUBLISHED = 'published', 'Publié'
        LIVE      = 'live',      'En cours'
        ENDED     = 'ended',     'Terminé'
        SOUVENIR  = 'souvenir',  'Espace souvenir'
        ARCHIVED  = 'archived',  'Archivé'

    class Ambiance(models.TextChoices):
        ELEGANT       = 'elegant',       'Élégant'
        FESTIF        = 'festif',        'Festif'
        MINIMALISTE   = 'minimaliste',   'Minimaliste'
        COLORE        = 'colore',        'Coloré'
        PROFESSIONNEL = 'professionnel', 'Professionnel'

    # ── Clé primaire ──────────────────────────────────────────
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ── Relation vers l'organisateur ──────────────────────────
    # ForeignKey = clé étrangère → relation "un User peut avoir
    # plusieurs Events" (relation 1:N dans le MLD)
    # on_delete=CASCADE → si le User est supprimé, ses événements
    # sont supprimés automatiquement
    # related_name → permet d'écrire user.organized_events.all()
    organizer = models.ForeignKey(
        'users.User',
        on_delete    = models.CASCADE,
        related_name = 'organized_events',
        verbose_name = "Organisateur"
    )

    # ── Informations de base ──────────────────────────────────
    title       = models.CharField(max_length=100, verbose_name="Titre")
    event_type  = models.CharField(max_length=15, choices=EventType.choices, verbose_name="Type")
    description = models.TextField(blank=True, default='', verbose_name="Description")

    # ── Dates ─────────────────────────────────────────────────
    start_date = models.DateTimeField(verbose_name="Date et heure de début")
    end_date   = models.DateTimeField(verbose_name="Date et heure de fin")

    # ── Lieu ──────────────────────────────────────────────────
    location_address = models.CharField(max_length=500, blank=True, default='', verbose_name="Adresse")
    latitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_online = models.BooleanField(default=False, verbose_name="Événement en ligne")
    online_link = models.URLField(max_length=512, blank=True, null=True, verbose_name="Lien de la réunion")

    # ── Statut et visibilité ──────────────────────────────────
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.DRAFT)
    status     = models.CharField(max_length=10, choices=EventStatus.choices, default=EventStatus.DRAFT)

    # ── Configuration du mini-site (JSONB) ───────────────────
    # JSONField utilise le type JSONB de PostgreSQL
    # Le contenu ressemble à :
    # {
    #   "palette": {"primary": "#C4A882", ...},
    #   "zones": {
    #     "header": {"component": "hero_3", "animation": "fade_parallax"},
    #     "corps": [...],
    #     "rsvp": {...},
    #     "footer": {...}
    #   }
    # }
    template_config = models.JSONField(
        null         = True,
        blank        = True,
        verbose_name = "Configuration JSONB du mini-site"
    )
    palette = models.JSONField(
        null         = True,
        blank        = True,
        verbose_name = "Palette de couleurs (générée par OpenCV)"
    )
    ambiance = models.CharField(max_length=15, choices=Ambiance.choices, blank=True, default='')

    # ── Sous-domaine et domaine personnalisé ─────────────────
    subdomain = models.CharField(
        max_length   = 100,
        unique       = True,
        null         = True,
        blank        = True,
        verbose_name = "Sous-domaine (ex: mon-mariage.easevent.app)"
    )
    # SET_NULL → si le domaine est supprimé, l'événement perd juste
    # son domaine perso mais n'est pas supprimé lui-même
    custom_domain = models.ForeignKey(
        'users.Domain',
        on_delete    = models.SET_NULL,
        null         = True,
        blank        = True,
        related_name = 'events',
        verbose_name = "Domaine personnalisé (Plan Pro)"
    )

    # ── Images de couverture ─────────────────────────────────
    # cover_image stocke l'URL ou le chemin de l'image de couverture
    # de l'événement (celle affichée dans la HomeScreen)
    cover_image = models.CharField(
        max_length   = 512,
        blank        = True,
        null         = True,
        verbose_name = "Image de couverture de l'événement"
    )

    # ── Métriques ─────────────────────────────────────────────
    view_count = models.PositiveIntegerField(default=0, verbose_name="Nombre de vues")

    # ── Soft delete et timestamps ─────────────────────────────
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Date suppression")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'events'
        ordering = ['-created_at']   # tri par défaut : plus récent en premier
        indexes  = [
            models.Index(fields=['organizer', 'status']),
            models.Index(fields=['visibility', 'status']),
            models.Index(fields=['start_date']),
            models.Index(fields=['subdomain']),
            models.Index(fields=['latitude', 'longitude']),   # pour les requêtes géographiques
        ]

    def __str__(self):
        return f"[{self.event_type}] {self.title} — {self.status}"


# ─────────────────────────────────────────────────────────────
# TABLE : event_media
# ─────────────────────────────────────────────────────────────
class EventMedia(models.Model):
    """
    Médias uploadés pour un événement (photos principalement).

    Les fichiers ne sont PAS stockés en base de données.
    On stocke uniquement la "clé" du fichier dans Cloudflare R2
    (le stockage cloud), c'est-à-dire le chemin du fichier.

    Le traitement (compression, suppression de fond) est
    asynchrone via Celery — processing_status suit l'avancement.
    """

    class MediaType(models.TextChoices):
        PHOTO = 'photo', 'Photo'
        VIDEO = 'video', 'Vidéo'

    # Statuts du traitement automatique par Celery
    class ProcessingStatus(models.TextChoices):
        PENDING    = 'pending',    'En attente de traitement'
        PROCESSING = 'processing', 'Traitement en cours'
        DONE       = 'done',       'Traitement terminé'
        FAILED     = 'failed',     'Échec du traitement'

    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='media')
    uploader = models.ForeignKey(
        'users.User',
        on_delete    = models.SET_NULL,
        null         = True,
        related_name = 'uploaded_media',
        verbose_name = "Personne qui a uploadé"
    )

    # Chemin dans Cloudflare R2 (pas l'URL complète, juste le chemin)
    # Exemple : "events/mariage-sarah/hero_photo.jpg"
    r2_key     = models.CharField(max_length=512, verbose_name="Clé Cloudflare R2")
    media_type = models.CharField(max_length=10, choices=MediaType.choices, default='photo')

    # Suivi du traitement asynchrone (Celery)
    processing_status = models.CharField(
        max_length = 15,
        choices    = ProcessingStatus.choices,
        default    = ProcessingStatus.PENDING
    )
    original_url  = models.URLField(max_length=512, blank=True, null=True, verbose_name="URL originale")
    processed_url = models.URLField(max_length=512, blank=True, null=True, verbose_name="URL après traitement")

    # Couleurs extraites par Colorthief — liste de tuples RGB
    # Exemple : [[255, 107, 74], [78, 175, 122], [196, 168, 130]]
    dominant_colors = models.JSONField(
        null  = True,
        blank = True,
        verbose_name = "5 couleurs dominantes (RGB)"
    )

    # True par défaut, False pour les photos souvenir à valider
    is_approved = models.BooleanField(default=True, verbose_name="Approuvée par l'organisateur")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'event_media'
        ordering = ['created_at']

    def __str__(self):
        return f"Media {self.media_type} — {self.event.title} ({self.processing_status})"


# ─────────────────────────────────────────────────────────────
# TABLE : template_generations
# ─────────────────────────────────────────────────────────────
class TemplateGeneration(models.Model):
    """
    Trace chaque configuration de template générée par l'API Claude.

    Rôle principal : garantir l'unicité absolue des designs.
    ──────────────────────────────────────────────────────────
    Avant d'afficher un design à l'organisateur, on :
    1. Sérialise la config JSON (clés triées pour garantir
       que le même contenu donne toujours le même résultat)
    2. Calcule son empreinte SHA-256 (64 caractères hex)
    3. Cherche cette empreinte dans cette table
    4. Si trouvée → doublon → on régénère avec Claude
    5. Si non trouvée → design unique → on l'enregistre ici

    config_hash est UNIQUE en base → contrainte garantie
    par PostgreSQL, pas seulement par notre code Python.
    """

    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='template_generations')

    # Empreinte SHA-256 de la config JSON sérialisée
    # Exemple : "a3f4b2c1d5e6..." (64 caractères hexadécimaux)
    config_hash = models.CharField(
        max_length   = 64,
        unique       = True,   # UNIQUE → PostgreSQL garantit pas de doublon
        verbose_name = "Empreinte SHA-256"
    )

    # La configuration complète (JSONB)
    config_json = models.JSONField(verbose_name="Configuration JSON du template")

    # True si l'organisateur a choisi ce design → alimente le modèle ML
    was_selected = models.BooleanField(
        default      = False,
        verbose_name = "Sélectionné par l'organisateur"
    )

    # Score de pertinence calculé par le modèle de recommandation (KNN)
    # Valeur entre 0.0 et 1.0 — le design le mieux scoré est affiché en premier
    ml_score = models.FloatField(
        null         = True,
        blank        = True,
        verbose_name = "Score ML de pertinence (0.0 à 1.0)"
    )

    generated_at = models.DateTimeField(auto_now_add=True, verbose_name="Date de génération")

    class Meta:
        db_table = 'template_generations'
        indexes  = [models.Index(fields=['config_hash'])]

    def __str__(self):
        statut = "✓ sélectionné" if self.was_selected else "non sélectionné"
        return f"Template {self.config_hash[:8]}... ({statut})"


# ─────────────────────────────────────────────────────────────
# TABLE : event_collaborators
# ─────────────────────────────────────────────────────────────
class EventCollaborator(models.Model):
    """
    Droits délégués sur un événement.

    Un organisateur peut inviter des collaborateurs
    (photographe, co-organisateur) avec des droits précis.

    Le champ permissions est un JSONB avec 4 booléens :
    {
      "can_read":            true,
      "can_add_media":       true,
      "can_edit_components": false,
      "can_manage_guests":   false
    }

    Avantage du JSONB ici : les droits sont indépendants
    et cumulables sans multiplier les colonnes en base.
    """

    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='collaborators')
    user  = models.ForeignKey(
        'users.User',
        on_delete    = models.CASCADE,
        related_name = 'collaborations',
        verbose_name = "Collaborateur"
    )

    permissions = models.JSONField(
        default      = dict,
        verbose_name = "Droits accordés",
        help_text    = '{"can_read": true, "can_add_media": false, "can_edit_components": false, "can_manage_guests": false}'
    )

    invited_at  = models.DateTimeField(auto_now_add=True, verbose_name="Date d'invitation")
    accepted_at = models.DateTimeField(null=True, blank=True, verbose_name="Date d'acceptation")

    class Meta:
        db_table        = 'event_collaborators'
        unique_together = [['event', 'user']]   # un seul rôle par couple événement/utilisateur

    def __str__(self):
        return f"{self.user.full_name} collabore sur '{self.event.title}'"
