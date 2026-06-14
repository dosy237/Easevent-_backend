"""
analytics/models.py
═══════════════════════════════════════════════════════════════
Modèles de l'application "analytics".
Contient 2 tables :
  - Feedback        → les retours des participants
  - EventAnalytics  → les agrégats quotidiens de métriques
═══════════════════════════════════════════════════════════════
"""

import uuid
from django.db import models


# ─────────────────────────────────────────────────────────────
# TABLE : feedbacks
# ─────────────────────────────────────────────────────────────
class Feedback(models.Model):
    """
    Retour d'un participant sur un événement.

    Cycle de vie du feedback :
    ───────────────────────────
    1. Envoi automatique (Celery) : 2h après la fin de l'événement,
       une notification est envoyée à chaque participant
    2. Soumission : l'utilisateur note de 1 à 5 et laisse un commentaire
    3. Analyse NLP (Celery) : CamemBERT analyse automatiquement le texte
       → rempli les champs sentiment, sentiment_score, topics
    4. Affichage : les résultats apparaissent dans le dashboard

    Anonymisation irréversible (obligation RGPD) :
    ───────────────────────────────────────────────
    Si is_anonymous = True, author est mis à NULL immédiatement
    à la soumission. Impossible de retrouver l'auteur ensuite —
    même pour l'administrateur de la plateforme.
    """

    class SentimentLabel(models.TextChoices):
        POSITIVE = 'positive', 'Positif'
        NEUTRAL  = 'neutral',  'Neutre'
        NEGATIVE = 'negative', 'Négatif'

    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        'events.Event',
        on_delete    = models.CASCADE,
        related_name = 'feedbacks',
        verbose_name = "Événement évalué"
    )

    # Auteur — mis à NULL si l'utilisateur choisit l'anonymat
    # SET_NULL → ne supprime pas le feedback si l'user est supprimé
    author = models.ForeignKey(
        'users.User',
        on_delete    = models.SET_NULL,
        null         = True,
        blank        = True,
        related_name = 'feedbacks',
        verbose_name = "Auteur (null si anonyme)"
    )

    # ── Contenu du feedback ───────────────────────────────────
    rating = models.PositiveSmallIntegerField(
        verbose_name = "Note",
        help_text    = "1 à 5 (correspond aux 5 niveaux visuels)"
    )
    comment = models.TextField(
        blank        = True,
        default      = '',
        verbose_name = "Commentaire libre (optionnel)"
    )
    is_anonymous = models.BooleanField(
        default      = False,
        verbose_name = "Anonyme",
        help_text    = "Si True, author_id est NULL — irréversible à la soumission"
    )

    # ── Résultats ML (remplis par Celery après soumission) ────
    # Ces champs sont NULL quand le feedback est soumis.
    # Un worker Celery les remplit en arrière-plan via CamemBERT.
    sentiment = models.CharField(
        max_length   = 10,
        choices      = SentimentLabel.choices,
        blank        = True,
        null         = True,
        verbose_name = "Sentiment analysé par CamemBERT"
    )
    sentiment_score = models.FloatField(
        null         = True,
        blank        = True,
        verbose_name = "Score de confiance NLP (0.0 à 1.0)"
    )
    # Thèmes extraits par LDA (Latent Dirichlet Allocation)
    # Exemple : ["organisation", "lieu", "ambiance"]
    topics = models.JSONField(
        null         = True,
        blank        = True,
        verbose_name = "Thèmes extraits automatiquement"
    )

    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="Date de soumission")
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'feedbacks'
        indexes  = [
            models.Index(fields=['event', 'sentiment']),
            models.Index(fields=['event', 'rating']),
        ]

    def __str__(self):
        auteur = "anonyme" if self.is_anonymous else (self.author.full_name if self.author else "?")
        return f"Feedback {self.rating}/5 par {auteur} — {self.event.title}"


# ─────────────────────────────────────────────────────────────
# TABLE : event_analytics
# ─────────────────────────────────────────────────────────────
class EventAnalytics(models.Model):
    """
    Agrégat quotidien des métriques d'un événement.

    Ce n'est pas une table temps réel — c'est un snapshot
    quotidien calculé chaque soir à 20h par Celery Beat.
    Les données sont ensuite mises en cache Redis (TTL 1h)
    pour que le dashboard s'affiche rapidement.

    nlp_summary (JSONB) contient la synthèse des feedbacks :
    {
      "global_score":   84,
      "positive_pct":   72,
      "neutral_pct":    18,
      "negative_pct":   10,
      "top_topics":     ["organisation", "ambiance", "lieu"],
      "best_comments":  ["Super soirée...", "Très bien organisé..."],
      "worst_comments": ["Trop bruyant...", "Manque de places..."]
    }

    attendance_predicted (prédiction du taux de présence réel) :
    Calculé par notre modèle Gradient Boosting Regressor.
    Exemple : 0.78 → 78% des personnes ayant confirmé
    seront réellement présentes. Affiché dans le dashboard
    avec l'intervalle de confiance.
    """

    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        'events.Event',
        on_delete    = models.CASCADE,
        related_name = 'analytics',
        verbose_name = "Événement"
    )

    # Un enregistrement par jour — unique_together garantit ça
    date = models.DateField(verbose_name="Date de l'agrégat")

    # ── Métriques de base ─────────────────────────────────────
    view_count         = models.PositiveIntegerField(default=0, verbose_name="Vues du mini-site")
    confirmation_count = models.PositiveIntegerField(default=0, verbose_name="Confirmations RSVP")
    feedback_count     = models.PositiveIntegerField(default=0, verbose_name="Feedbacks reçus")

    # ── Score de satisfaction global (0 à 100) ────────────────
    satisfaction_score = models.FloatField(
        null         = True,
        blank        = True,
        verbose_name = "Score global de satisfaction (0 à 100)"
    )

    # ── Prédiction ML de présence ─────────────────────────────
    attendance_predicted = models.FloatField(
        null         = True,
        blank        = True,
        verbose_name = "Taux de présence prédit (0.0 à 1.0)"
    )
    attendance_confidence = models.FloatField(
        null         = True,
        blank        = True,
        verbose_name = "Intervalle de confiance à 80%"
    )

    # ── Synthèse NLP (JSONB) ──────────────────────────────────
    nlp_summary = models.JSONField(
        null         = True,
        blank        = True,
        verbose_name = "Synthèse NLP des feedbacks"
    )

    computed_at = models.DateTimeField(auto_now=True, verbose_name="Dernière mise à jour")

    class Meta:
        db_table        = 'event_analytics'
        unique_together = [['event', 'date']]   # un seul agrégat par événement/jour
        ordering        = ['-date']

    def __str__(self):
        score = f"{self.satisfaction_score:.0f}/100" if self.satisfaction_score else "N/A"
        return f"Analytics {self.event.title} — {self.date} — Score: {score}"
