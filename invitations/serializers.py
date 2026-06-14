# invitations/serializers.py
from rest_framework import serializers
from .models  import Invitation
from events.serializers import EventPublicSerializer


class InvitationSerializer(serializers.ModelSerializer):
    """
    Serializer pour les invitations.
    Inclut les détails de l'événement associé.
    """
    # Inclure les détails complets de l'événement dans la réponse
    # au lieu de juste l'ID — le front-end a besoin de tout
    event = EventPublicSerializer(read_only=True)

    class Meta:
        model  = Invitation
        fields = [
            'id',
            'event',
            'status',
            'channel',
            'sent_at',
            'opened_at',
            'responded_at',
            'expires_at',
        ]