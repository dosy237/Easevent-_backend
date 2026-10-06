"""
notifications/models.py
═══════════════════════════════════════════════════════════════
Notifications dans l'application (M17, MVP §5 « Notifications créées
par le backend »).

Le texte est enregistré à la création (« title » en gras + « body ») :
la liste s'affiche sans recalcul. dedupe_key garantit qu'une
notification planifiée (rappel J-1, bilan du jour…) n'est créée
qu'une fois, même si plusieurs appareils la déclenchent.
═══════════════════════════════════════════════════════════════
"""
import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class Notification(models.Model):

    class Type(models.TextChoices):
        INVITATION_RECEIVED = 'invitation_received', 'Invitation reçue'
        TICKET_TO_VALIDATE  = 'ticket_to_validate',  'Ticket à valider'
        TICKET_GENERATED    = 'ticket_generated',    'Ticket généré'
        MESSAGE_RECEIVED    = 'message_received',    'Nouveau message'
        DAILY_SUMMARY       = 'daily_summary',       'Bilan du jour'
        REMINDER            = 'reminder',            'Rappel'
        PAYMENT_SUCCEEDED   = 'payment_succeeded',   'Paiement confirmé'
        PAYMENT_FAILED      = 'payment_failed',      'Paiement échoué'
        FRIEND_REQUEST      = 'friend_request',      "Demande d'ami"
        FRIEND_ACCEPTED     = 'friend_accepted',     'Demande acceptée'

    class Category(models.TextChoices):
        EVENTS   = 'events',   'Événements'
        MESSAGES = 'messages', 'Messages'
        SYSTEM   = 'system',   'Système'
        SOCIAL   = 'social',   'Amis'

    CATEGORY_OF = {
        'invitation_received': 'events', 'ticket_to_validate': 'events', 'ticket_generated': 'events',
        'daily_summary': 'events', 'reminder': 'events', 'message_received': 'messages',
        'payment_succeeded': 'system', 'payment_failed': 'system',
        'friend_request': 'social', 'friend_accepted': 'social',
    }

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='notifications')
    type       = models.CharField(max_length=30, choices=Type.choices)
    category   = models.CharField(max_length=10, choices=Category.choices)
    title      = models.CharField(max_length=160, verbose_name='Début en gras')
    body       = models.CharField(max_length=255, blank=True, default='')
    actor      = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    event      = models.ForeignKey('events.Event', on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    invitation = models.ForeignKey('invitations.Invitation', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    ticket     = models.ForeignKey('tickets.Ticket', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    data       = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=120, null=True, blank=True)
    read_at    = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['user', 'read_at']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['user', 'dedupe_key'], condition=Q(dedupe_key__isnull=False),
                                    name='notification_unique_dedupe_key'),
        ]

    def __str__(self):
        return f'{self.type} → {self.user_id}'
