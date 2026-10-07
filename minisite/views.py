"""
minisite/views.py
  GET   /api/events/<id>/minisite/             mini-site à afficher (mêmes règles d'accès que l'événement)
  PATCH /api/events/<id>/minisite/             retouches (organisateur)
  POST  /api/events/<id>/minisite/generate/    lance une génération (6 propositions)
  GET   /api/events/<id>/minisite/generation/  dernière génération : étape, propositions
  POST  /api/events/<id>/minisite/choose/      choisir une proposition
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from events.models import Event
from events.serializers import EventPublicSerializer
from events.team import is_manager

from . import services
from .models import MiniSiteGeneration


class GenerateThrottle(UserRateThrottle):
    scope = 'minisite'


def _error(exc):
    return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)


def _own(request, event_id):
    from events.team import managed_event
    return managed_event(request.user, event_id)


def _can_view(request, event):
    if event.deleted_at is not None:
        return False
    user = request.user
    from events.team import role_of
    if role_of(event, user):
        return True
    if event.status != 'published':
        return False
    if event.visibility == 'public':
        return True
    if not user.is_authenticated:
        return False
    from tickets.models import Ticket
    from tickets.services import can_access_event
    return can_access_event(event, user) or Ticket.objects.filter(event=event, user=user, status__in=Ticket.ACTIVE).exists()


@api_view(['GET', 'PATCH'])
@permission_classes([AllowAny])
def minisite(request, event_id):
    if request.method == 'PATCH':
        if not request.user.is_authenticated:
            return Response({'detail': 'Connexion requise.'}, status=status.HTTP_401_UNAUTHORIZED)
        event = _own(request, event_id)
        if not event:
            return Response({'detail': 'Événement introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            cfg = services.edit(event, request.data if isinstance(request.data, dict) else {})
        except services.MiniSiteError as exc:
            return _error(exc)
        return Response({'spec': cfg['spec'], 'edited': cfg.get('edited', False)})

    event = Event.objects.select_related('organizer').filter(pk=event_id).first()
    if not event or not _can_view(request, event):
        return Response({'detail': 'Mini-site indisponible.', 'code': 'private'}, status=status.HTTP_404_NOT_FOUND)
    cfg = event.minisite_config or {}
    if not cfg.get('spec'):
        return Response({'detail': "Cet événement n'a pas encore de mini-site.", 'code': 'no_minisite'},
                        status=status.HTTP_404_NOT_FOUND)
    return Response({
        'spec': cfg['spec'],
        'event': EventPublicSerializer(event, context={'request': request, 'with_my_ticket': True}).data,
        'is_organizer': is_manager(event, request.user),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([GenerateThrottle])
def generate(request, event_id):
    event = _own(request, event_id)
    if not event:
        return Response({'detail': 'Événement introuvable.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        gen = services.start(event, request.user)
    except services.MiniSiteError as exc:
        return _error(exc)
    gen.refresh_from_db()
    return Response(services.serialize(gen), status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def generation(request, event_id):
    event = _own(request, event_id)
    if not event:
        return Response({'detail': 'Événement introuvable.'}, status=status.HTTP_404_NOT_FOUND)
    gen = event.minisite_generations.prefetch_related('proposals').first()
    if not gen:
        return Response({'status': 'none', 'quota': services.usage(event), 'proposals': []})
    if gen.status in (MiniSiteGeneration.Status.PENDING, MiniSiteGeneration.Status.RUNNING) \
            and gen.created_at < services.timezone.now() - services.STALE:
        MiniSiteGeneration.objects.filter(pk=gen.pk).update(status=MiniSiteGeneration.Status.FAILED, step='failed')
        gen.refresh_from_db()
    return Response(services.serialize(gen))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def choose(request, event_id):
    event = _own(request, event_id)
    if not event:
        return Response({'detail': 'Événement introuvable.'}, status=status.HTTP_404_NOT_FOUND)
    data = request.data if isinstance(request.data, dict) else {}
    try:
        cfg = services.choose(event, str(data.get('proposal_id') or ''))
    except services.MiniSiteError as exc:
        return _error(exc)
    return Response({'spec': cfg['spec'], 'message': 'Mini-site choisi. Vos invités le verront dans l’application.'})
