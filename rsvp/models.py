"""
rsvp/models.py — Questions RSVP (M14) et réponses des invités (M19)

L'organisateur pose jusqu'à 5 questions ; l'invité y répond avant
d'accepter l'invitation ou de prendre son ticket.
Valeur d'une réponse selon le type :
  text → "texte"   single → "option"   multiple → ["option", …]   yesno → true / false
"""
import uuid

from django.conf import settings
from django.db import models


class RsvpQuestion(models.Model):
    class Kind(models.TextChoices):
        TEXT     = 'text',     'Texte libre'
        SINGLE   = 'single',   'Choix unique'
        MULTIPLE = 'multiple', 'Choix multiple'
        YESNO    = 'yesno',    'Oui / Non'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event      = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='rsvp_questions')
    position   = models.PositiveSmallIntegerField(default=0)
    kind       = models.CharField(max_length=10, choices=Kind.choices)
    label      = models.CharField(max_length=200)
    options    = models.JSONField(default=list, blank=True)
    required   = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'created_at']
        verbose_name = 'Question RSVP'
        verbose_name_plural = 'Questions RSVP'

    def __str__(self):
        return self.label


class RsvpAnswer(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question   = models.ForeignKey(RsvpQuestion, on_delete=models.CASCADE, related_name='answers')
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rsvp_answers')
    value      = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['question', 'user'], name='rsvp_one_answer_per_question')]
        verbose_name = 'Réponse RSVP'
        verbose_name_plural = 'Réponses RSVP'
