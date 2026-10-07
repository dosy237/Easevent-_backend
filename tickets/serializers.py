"""tickets/serializers.py"""
from rest_framework import serializers

from easevent.media import public_url
from .models import Ticket


class TicketSerializer(serializers.ModelSerializer):
    event       = serializers.SerializerMethodField()
    participant = serializers.SerializerMethodField()
    qr_payload  = serializers.SerializerMethodField()
    invitation_id = serializers.UUIDField(read_only=True)

    class Meta:
        model  = Ticket
        fields = [
            'id', 'number', 'status', 'payment_status', 'price', 'currency',
            'dress_code', 'generated_at', 'created_at', 'invitation_id',
            'event', 'participant', 'qr_payload',
        ]

    def get_event(self, obj):
        e = obj.event
        return {
            'id':                 str(e.id),
            'title':              e.title,
            'event_type_display': e.event_type_label if e.event_type == 'autre' and e.event_type_label
                                  else e.get_event_type_display(),
            'start_date':         e.start_date.isoformat() if e.start_date else None,
            'end_date':           e.end_date.isoformat() if e.end_date else None,
            'location_address':   e.location_address,
            'is_online':          e.is_online,
            'cover_image':        public_url(e.cover_image, self.context.get('request')),
            'dress_code':         e.dress_code,
            'organizer_name':     e.organizer.full_name,
            'map':                _map(e),
            'has_rsvp':           e.rsvp_questions.exists(),     # questions RSVP : « Mes réponses » (M19)
        }

    def get_participant(self, obj):
        return obj.user.full_name

    def get_qr_payload(self, obj):
        # Le QR n'existe que pour un ticket généré
        return obj.qr_payload if obj.status == Ticket.Status.GENERATED else None


def _map(e):
    """Liens Google Maps du lieu (bouton « Itinéraire » du ticket)."""
    from events.geo import maps_links
    if e.is_online or not e.location_address:
        return None
    lat = float(e.latitude) if e.latitude is not None else None
    lng = float(e.longitude) if e.longitude is not None else None
    return maps_links(e.location_address, lat, lng)
