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
        # Organisateur
        GUEST_RESPONSE      = 'guest_response',      'Réponse d\'un invité'
        EVENT_FULL          = 'event_full',          'Événement complet'
        PAYOUTS_READY       = 'payouts_ready',       'Paiements activés'
        # Participant
        EVENT_UPDATED       = 'event_updated',       'Événement modifié'
        EVENT_CANCELLED     = 'event_cancelled',     'Événement annulé'
        INVITATION_REVOKED  = 'invitation_revoked',  'Invitation retirée'
        PAYMENT_REFUNDED    = 'payment_refunded',    'Remboursement'
        # Compte
        SUBSCRIPTION        = 'subscription',        'Abonnement'
        MINISITE_READY      = 'minisite_ready',      'Mini-site prêt'
        TICKET_GIFT         = 'ticket_gift',         'Billet offert'
        QUESTION_TO_ANSWER  = 'question_to_answer',  'Question à laquelle répondre'
        TEAM_INVITE         = 'team_invite',         "Invitation à co-organiser"
        TEAM_RESPONSE       = 'team_response',       "Réponse d'un co-organisateur"
        EVENT_BROADCAST     = 'event_broadcast',     "Message à tous les invités"
        EVENT_COMMENT       = 'event_comment',       'Nouveau commentaire'
        MEMORIES_ADDED      = 'memories_added',      'Nouvelles photos souvenirs'
        BASKET_OPEN         = 'basket_open',         'Panier ouvert'
        BASKET_CONTRIBUTION = 'basket_contribution', 'Ajout au panier'

    class Category(models.TextChoices):
        EVENTS   = 'events',   'Événements'
        MESSAGES = 'messages', 'Messages'
        SYSTEM   = 'system',   'Système'
        SOCIAL   = 'social',   'Amis'

    CATEGORY_OF = {
        'minisite_ready': 'events',
        'invitation_received': 'events', 'ticket_to_validate': 'events', 'ticket_generated': 'events',
        'daily_summary': 'events', 'reminder': 'events', 'message_received': 'messages',
        'payment_succeeded': 'system', 'payment_failed': 'system',
        'friend_request': 'social', 'friend_accepted': 'social',
        'guest_response': 'events', 'event_full': 'events', 'event_updated': 'events',
        'event_cancelled': 'events', 'invitation_revoked': 'events',
        'payouts_ready': 'system', 'payment_refunded': 'system', 'subscription': 'system',
        'ticket_gift': 'events', 'question_to_answer': 'messages',
        'team_invite': 'events', 'team_response': 'events', 'event_broadcast': 'messages',
        'event_comment': 'events', 'memories_added': 'events',
        'basket_open': 'events', 'basket_contribution': 'events',
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


class DeviceToken(models.Model):
    """
    Jeton de notification push d'un appareil (Expo Push → FCM / APNs).
    Un jeton appartient à un seul compte : à la connexion d'un autre compte
    sur le même téléphone, il est réattribué.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='device_tokens')
    token      = models.CharField(max_length=255, unique=True)
    platform   = models.CharField(max_length=10, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'device_tokens'

    def __str__(self):
        return f'{self.platform} → {self.user_id}'
