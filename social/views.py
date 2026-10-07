"""
social/views.py — amis (inviter plus facilement)
  GET    /api/friends/                         amis, demandes reçues, demandes envoyées
  POST   /api/friends/requests/  { user_id }   demander (ou accepter si l'autre a déjà demandé)
  POST   /api/friends/requests/<id>/accept/
  DELETE /api/friends/requests/<id>/           refuser, annuler ou retirer un ami
"""
from django.db.models import Q
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from . import services
from .models import Friendship


class FriendRequestThrottle(UserRateThrottle):
    scope = 'friend_requests'


def _person(user):
    return {'id': str(user.id), 'first_name': user.first_name, 'last_name': user.last_name,
            'name': user.full_name, 'avatar_url': user.avatar_url,
            'initials': ''.join(p[:1] for p in (user.first_name, user.last_name) if p).upper()}


def _row(f, user):
    return {'id': str(f.id), 'status': f.status, 'user': _person(f.other(user)),
            'created_at': f.created_at.isoformat()}


def _own(request, friendship_id):
    f = (Friendship.objects.select_related('requester', 'addressee')
         .filter(pk=friendship_id).filter(Q(requester=request.user) | Q(addressee=request.user)).first())
    if f is None:
        raise Http404
    return f


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def friends(request):
    user = request.user
    rows = (Friendship.objects.select_related('requester', 'addressee')
            .filter(Q(requester=user) | Q(addressee=user))
            .filter(requester__is_active=True, addressee__is_active=True).order_by('-created_at'))
    out = {'friends': [], 'incoming': [], 'outgoing': []}
    for f in rows:
        key = 'friends' if f.status == 'accepted' else ('outgoing' if f.requester_id == user.id else 'incoming')
        out[key].append(_row(f, user))
    out['friends'].sort(key=lambda r: (r['user']['first_name'].lower(), r['user']['last_name'].lower()))
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([FriendRequestThrottle])
def send_request(request):
    from users.models import User
    import uuid
    data = request.data if isinstance(request.data, dict) else {}
    try:
        target_id = uuid.UUID(str(data.get('user_id')))
    except (TypeError, ValueError):
        target_id = None
    target = User.objects.filter(pk=target_id, is_active=True, is_verified=True,
                                 deleted_at__isnull=True).first() if target_id else None
    if target is None:
        raise Http404
    try:
        f = services.request(request.user, target)
    except services.FriendError as exc:
        return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
    return Response(_row(f, request.user), status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def accept(request, friendship_id):
    f = _own(request, friendship_id)
    try:
        services.accept(request.user, f)
    except services.FriendError as exc:
        return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
    return Response(_row(f, request.user))


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove(request, friendship_id):
    services.decline(request.user, _own(request, friendship_id))
    return Response(status=status.HTTP_204_NO_CONTENT)
