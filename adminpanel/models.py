"""
adminpanel/models.py — annonces de l'équipe et journal des actions d'administration
════════════════════════════════════════════════════════════════
Announcement : message de l'équipe Easevent (texte, vidéo de 45 s au plus, lien),
  affiché EN TÊTE du fil de tous les utilisateurs pendant sa période de diffusion,
  du plus prioritaire au moins prioritaire.
AdminAction  : chaque modification faite depuis l'administration est tracée
  (qui, quoi, sur quoi, quand) — indispensable pour un outil aussi puissant.
════════════════════════════════════════════════════════════════
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AnnouncementQuerySet(models.QuerySet):
    def live(self, now=None):
        now = now or timezone.now()
        return self.filter(is_active=True, starts_at__lte=now).filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now)).order_by('-priority', '-starts_at')


class Announcement(models.Model):
    class Priority(models.IntegerChoices):
        NORMAL = 10, 'Normale'
        HIGH = 50, 'Haute'
        URGENT = 90, 'Urgente'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=90)
    body = models.CharField(max_length=400, blank=True)
    link_url = models.URLField(max_length=300, blank=True)
    link_label = models.CharField(max_length=30, blank=True)
    # Même format que la vidéo d'un événement (events/video.py)
    video_public_id = models.CharField(max_length=200, blank=True)
    video = models.JSONField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(choices=Priority.choices, default=Priority.NORMAL)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ['-priority', '-starts_at']
        indexes = [models.Index(fields=['is_active', 'starts_at'])]

    def __str__(self):
        return self.title


class AdminAction(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    action = models.CharField(max_length=40)
    target_type = models.CharField(max_length=20)
    target_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
