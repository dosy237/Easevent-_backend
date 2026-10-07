"""
social/models.py — amis dans Easevent

Une demande d'amitié va de requester à addressee. Acceptée, elle fait
des deux personnes des amis (relation symétrique) : chacun peut alors
inviter l'autre en un geste depuis sa liste d'amis.
"""
import uuid

from django.db import models
from django.db.models import F, Q


class Friendship(models.Model):

    class Status(models.TextChoices):
        PENDING  = 'pending',  'En attente'
        ACCEPTED = 'accepted', 'Amis'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requester    = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='friend_requests_sent')
    addressee    = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='friend_requests_received')
    status       = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at   = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'friendships'
        constraints = [
            models.UniqueConstraint(fields=['requester', 'addressee'], name='friendship_unique_pair'),
            models.CheckConstraint(condition=~Q(requester=F('addressee')), name='friendship_not_self'),
        ]
        indexes = [models.Index(fields=['addressee', 'status']), models.Index(fields=['requester', 'status'])]

    def other(self, user):
        return self.addressee if self.requester_id == user.id else self.requester
