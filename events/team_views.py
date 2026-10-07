"""
events/team_views.py — équipe d'un événement et message de diffusion

  GET    /api/events/<id>/team/                    équipe, répartition des invités
  POST   /api/events/<id>/team/                    { user_id, role } inviter un co-organisateur / photographe
  DELETE /api/events/<id>/team/<collab_id>/        retirer (organisateur) ou quitter (soi-même)
  PUT    /api/events/<id>/team/split/              { split: {user_id: n} } | { apply: "proposed" }
  GET    /api/events/team/invitations/             propositions reçues
  POST   /api/events/team/<collab_id>/respond/     { accept: true|false }
  GET    /api/events/<id>/broadcast/               messages déjà envoyés + audience
  POST   /api/events/<id>/broadcast/               { message } à tous les invités
"""
import uuid

from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .models import EventCollaborator
from .team import (
    TeamError, invite_member, managed_event, pending_for, remove_member, respond, role_of, set_split,
    proposed_split, team_payload,
)


class TeamThrottle(UserRateThrottle):
    scope = 'team'


def _error(exc):
    return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)


def _managed(request, event_id, **kw):
    event = managed_event(request.user, event_id, **kw)
    if event is None:
        raise Http404
    return event


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TeamThrottle])
def team(request, event_id):
    event = _managed(request, event_id)
    if request.method == 'GET':
        return Response(team_payload(event, request.user))
    from users.models import User
    data = request.data if isinstance(request.data, dict) else {}
    try:
        uid = uuid.UUID(str(data.get('user_id')))
    except (TypeError, ValueError):
        return Response({'detail': 'Choisissez une personne.', 'code': 'user_required'},
                        status=status.HTTP_400_BAD_REQUEST)
    user = User.objects.filter(pk=uid, is_active=True, is_verified=True, deleted_at__isnull=True).first()
    if user is None:
        raise Http404
    try:
        invite_member(event, request.user, user, str(data.get('role') or 'cohost'))
    except TeamError as exc:
        return _error(exc)
    return Response(team_payload(event, request.user), status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def team_member(request, event_id, collab_id):
    collab = EventCollaborator.objects.select_related('event').filter(
        pk=collab_id, event_id=event_id, event__deleted_at__isnull=True).first()
    if collab is None or not (role_of(collab.event, request.user) or collab.user_id == request.user.id):
        raise Http404
    try:
        remove_member(collab, request.user)
    except TeamError as exc:
        return _error(exc)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def team_split(request, event_id):
    """Seul l'organisateur répartit ; la répartition reste modifiable à tout moment."""
    event = _managed(request, event_id, organizer_only=True)
    data = request.data if isinstance(request.data, dict) else {}
    split = proposed_split(event) if data.get('apply') == 'proposed' else data.get('split')
    try:
        set_split(event, split if split is not None else {})
    except TeamError as exc:
        return _error(exc)
    return Response(team_payload(event, request.user))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def team_invitations(request):
    rows = []
    for c in pending_for(request.user):
        by = c.invited_by or c.event.organizer
        rows.append({'id': str(c.id), 'role': c.role, 'event_id': str(c.event_id), 'event_title': c.event.title,
                     'start_date': c.event.start_date.isoformat() if c.event.start_date else None,
                     'invited_by': by.full_name, 'invited_at': c.invited_at.isoformat()})
    return Response({'results': rows})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TeamThrottle])
def team_respond(request, collab_id):
    collab = EventCollaborator.objects.select_related('event', 'invited_by', 'event__organizer').filter(
        pk=collab_id, user=request.user).first()
    if collab is None:
        raise Http404
    accept = bool((request.data or {}).get('accept')) if isinstance(request.data, dict) else False
    try:
        respond(collab, request.user, accept)
    except TeamError as exc:
        return _error(exc)
    return Response({'status': collab.status, 'role': collab.role, 'event_id': str(collab.event_id)})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TeamThrottle])
def broadcast(request, event_id):
    from messaging.broadcast import audience, history, send_broadcast
    from messaging.services import MessagingError
    event = _managed(request, event_id)
    if request.method == 'GET':
        users, outsiders = audience(event)
        return Response({'recipients': len(users), 'without_account': outsiders, 'history': history(event)})
    data = request.data if isinstance(request.data, dict) else {}
    try:
        result = send_broadcast(event, request.user, str(data.get('message', '')))
    except MessagingError as exc:
        return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
    n = result['recipients']
    detail = f"Message envoyé à {n} invité{'s' if n > 1 else ''}."
    if result['without_account']:
        w = result['without_account']
        detail += f" {w} invité{'s' if w > 1 else ''} sans compte ne l'{'ont' if w > 1 else 'a'} pas reçu."
    return Response({'detail': detail, **result}, status=status.HTTP_201_CREATED)
