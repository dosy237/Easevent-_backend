"""
messaging/models.py
═══════════════════════════════════════════════════════════════
Messagerie organisateur ↔ invité (parcours H, M15 / M16).

Une conversation = un événement + son organisateur + un participant.
Les messages « système » (invitation envoyée, acceptée, déclinée,
ticket généré) s'affichent au milieu des bulles.

Lecture et présence : chaque côté a sa date de dernière lecture
(accusés de lecture, non lus) et sa dernière activité (« En ligne »,
« en train d'écrire »). Aucune donnée n'est conservée au-delà.
═══════════════════════════════════════════════════════════════
"""
import uuid

from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Conversation d'événement (organisateur ↔ invité) ou directe entre amis (event vide)
    event       = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='conversations',
                                    null=True, blank=True)
    organizer   = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='organized_conversations')
    participant = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='conversations')

    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    organizer_read_at   = models.DateTimeField(null=True, blank=True)
    participant_read_at = models.DateTimeField(null=True, blank=True)
    organizer_seen_at   = models.DateTimeField(null=True, blank=True)
    participant_seen_at = models.DateTimeField(null=True, blank=True)
    organizer_typing_at   = models.DateTimeField(null=True, blank=True)
    participant_typing_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'conversations'
        ordering = ['-last_message_at']
        constraints = [
            models.UniqueConstraint(fields=['event', 'participant'], name='conversation_unique_event_participant'),
            # Une seule conversation directe par paire d'amis (organizer = identifiant le plus petit)
            models.UniqueConstraint(fields=['organizer', 'participant'], condition=models.Q(event__isnull=True),
                                    name='conversation_unique_direct'),
        ]

    @property
    def is_direct(self):
        return self.event_id is None

    def __str__(self):
        return f'{self.event_id} · {self.participant_id}'

    def side(self, user):
        """'organizer' ou 'participant' (None si l'utilisateur n'en fait pas partie)."""
        if user.id == self.organizer_id:
            return 'organizer'
        if user.id == self.participant_id:
            return 'participant'
        return None

    def other_side(self, side):
        return 'participant' if side == 'organizer' else 'organizer'


class Message(models.Model):

    class Kind(models.TextChoices):
        TEXT     = 'text',     'Message'
        IMAGE    = 'image',    'Photo / capture d’écran'
        LOCATION = 'location', 'Itinéraire'
        EVENT    = 'event',    'Événement partagé'
        SYSTEM   = 'system',   'Événement'

    class SystemType(models.TextChoices):
        INVITATION_SENT     = 'invitation_sent',     'Invitation envoyée'
        INVITATION_ACCEPTED = 'invitation_accepted', 'Invitation acceptée'
        INVITATION_DECLINED = 'invitation_declined', 'Invitation déclinée'
        TICKET_GENERATED    = 'ticket_generated',    'Ticket généré'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender       = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    kind         = models.CharField(max_length=10, choices=Kind.choices, default=Kind.TEXT)
    system_type  = models.CharField(max_length=30, choices=SystemType.choices, blank=True, default='')
    body         = models.TextField(max_length=2000, blank=True, default='')
    # Photo : chemin relatif dans PRIVATE_MEDIA_ROOT (jamais servi publiquement)
    attachment   = models.CharField(max_length=255, blank=True, default='')
    # Photo : largeur / hauteur ; itinéraire : adresse, coordonnées
    meta         = models.JSONField(default=dict, blank=True)
    created_at   = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'messages'
        ordering = ['created_at']
        indexes = [models.Index(fields=['conversation', 'created_at'])]
        constraints = [
            # Un même événement système n'apparaît qu'une fois par conversation
            models.UniqueConstraint(fields=['conversation', 'system_type'],
                                    condition=~models.Q(system_type=''), name='message_unique_system_event'),
        ]


# Une photo supprimée (compte effacé, événement supprimé…) disparaît aussi du disque
from django.db.models.signals import post_delete  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_delete, sender=Message)
def _delete_attachment(sender, instance, **kwargs):
    if instance.attachment:
        from pathlib import Path
        from django.conf import settings
        try:
            (Path(settings.PRIVATE_MEDIA_ROOT) / instance.attachment).unlink(missing_ok=True)
        except OSError:
            pass


class EventQuestion(models.Model):
    """
    Question d'un participant à l'organisateur, vue par l'assistant :
      auto     — répondue automatiquement à partir des informations de l'événement ;
      pending  — transmise à l'organisateur (notifié) ;
      answered — l'organisateur a répondu : sa réponse enrichit les suivantes.
    """
    class Status(models.TextChoices):
        AUTO = 'auto', 'Réponse automatique'
        PENDING = 'pending', "En attente de l'organisateur"
        ANSWERED = 'answered', "Répondue par l'organisateur"

    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='questions')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='questions')
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='question')
    question = models.CharField(max_length=500)
    answer = models.TextField(max_length=2000, blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices)
    source = models.CharField(max_length=20, blank=True, default='')       # ia / règles / cache / organisateur
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['event', 'status'])]
