"""
minisite/models.py
  MiniSiteGeneration : une demande de génération (6 propositions), son état,
                       les modèles d'IA utilisés (pour le suivi et l'entraînement futur)
  MiniSiteProposal   : une proposition (plan JSON) et son empreinte de disposition
Le mini-site choisi est recopié dans Event.minisite_config (affiché dans l'application).
"""
import uuid

from django.conf import settings
from django.db import models


class MiniSiteGeneration(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'En attente'
        RUNNING = 'running', 'En cours'
        DONE = 'done', 'Terminée'
        FAILED = 'failed', 'Échouée'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='minisite_generations')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    step = models.CharField(max_length=30, blank=True, default='')       # étape affichée pendant la génération
    engine = models.JSONField(default=dict, blank=True)                  # modèles utilisés, durées, replis
    error = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    chosen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['event', '-created_at'])]


class MiniSiteProposal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation = models.ForeignKey(MiniSiteGeneration, on_delete=models.CASCADE, related_name='proposals')
    index = models.PositiveSmallIntegerField()
    direction = models.CharField(max_length=20)
    spec = models.JSONField()
    fingerprint = models.CharField(max_length=64, db_index=True)
    chosen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['index']
        constraints = [models.UniqueConstraint(fields=['generation', 'index'], name='minisite_unique_index')]
