# invitations/serializers.py
from rest_framework import serializers
from .models  import Invitation
from events.serializers import EventPublicSerializer


class InvitationSerializer(serializers.ModelSerializer):
    """
    Serializer pour les invitations reçues (Mes tickets › En attente).
    Inclut les détails de l'événement associé.
    """
    event = EventPublicSerializer(read_only=True)

    class Meta:
        model  = Invitation
        fields = [
            'id',
            'event',
            'status',
            'channel',
            'message',
            'sent_at',
            'opened_at',
            'responded_at',
            'expires_at',
        ]
