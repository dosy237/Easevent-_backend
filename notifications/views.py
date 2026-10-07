"""
notifications/views.py — API de M17
  GET   /api/notifications/?category=all|events|messages|system&before=<iso>
  GET   /api/notifications/unread-count/
  POST  /api/notifications/<id>/read/
  POST  /api/notifications/read-all/          { category? }
  GET   /api/notifications/preferences/
  PATCH /api/notifications/preferences/       { reminders?, daily_summary? }
Chaque utilisateur ne voit et ne modifie que ses propres notifications.
"""
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from easevent.media import public_url

from .models import Notification
from .services import DEFAULT_PREFS, prefs_of, refresh_scheduled

PAGE_SIZE = 30
CATEGORIES = ('events', 'messages', 'social', 'system')


def _initials(user):
    return ''.join(p[:1] for p in (user.first_name, user.last_name) if p).upper()


def _serialize(n, request):
    inv = n.invitation
    ev = n.event
    return {
        'id':         str(n.id),
        'type':       n.type,
        'category':   n.category,
        'title':      n.title,
        'body':       n.body,
        'read':       n.read_at is not None,
        'created_at': n.created_at.isoformat(),
        'actor':      {'initials': _initials(n.actor), 'avatar_url': n.actor.avatar_url} if n.actor else None,
        'event':      {'id': str(ev.id), 'title': ev.title,
                       'cover_image': public_url(ev.cover_image, request) if ev.cover_image else None} if ev else None,
        # Accepter / Décliner seulement tant que l'invitation attend une réponse
        'invitation': {'id': str(inv.id), 'status': inv.status,
                       'can_answer': inv.status in ('sent', 'opened') and inv.is_valid} if inv else None,
        'ticket_id':  str(n.ticket_id) if n.ticket_id else None,
        # Demande d'ami : Accepter / Refuser tant qu'elle est en attente
        'friendship': _friendship(n),
        'data':       n.data,
    }


def _friendship(n):
    if n.type != 'friend_request' or not n.data.get('friendship_id'):
        return None
    from social.models import Friendship
    f = Friendship.objects.filter(pk=n.data['friendship_id']).first()
    return {'id': str(f.id), 'status': f.status, 'can_answer': f.status == 'pending'} if f else {'status': 'gone', 'can_answer': False}


# Notifications liées à un événement annulé : seules celles qui l'annoncent restent visibles
KEPT_AFTER_CANCEL = ('event_cancelled', 'payment_refunded', 'invitation_revoked')


def visible(qs):
    from django.db.models import Q
    return qs.filter(Q(event__isnull=True) | Q(event__deleted_at__isnull=True) | Q(type__in=KEPT_AFTER_CANCEL))


def _unread(user):
    counts = {c: 0 for c in CATEGORIES}
    for row in (visible(Notification.objects.filter(user=user, read_at__isnull=True))
                .values_list('category', flat=True)):
        counts[row] = counts.get(row, 0) + 1
    counts['all'] = sum(counts[c] for c in CATEGORIES)
    return counts


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_notifications(request):
    refresh_scheduled(request.user)
    qs = (visible(Notification.objects.filter(user=request.user))
          .select_related('actor', 'event', 'invitation'))
    category = request.query_params.get('category', 'all')
    if category in CATEGORIES:
        qs = qs.filter(category=category)
    try:
        before = parse_datetime(request.query_params.get('before') or '')
    except (TypeError, ValueError):
        before = None
    if before:
        qs = qs.filter(created_at__lt=before)
    items = list(qs[:PAGE_SIZE + 1])
    return Response({
        'results':  [_serialize(n, request) for n in items[:PAGE_SIZE]],
        'has_more': len(items) > PAGE_SIZE,
        'unread':   _unread(request.user),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def unread_count(request):
    refresh_scheduled(request.user)
    return Response({'unread': _unread(request.user)['all']})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_read(request, notification_id):
    updated = Notification.objects.filter(id=notification_id, user=request.user, read_at__isnull=True) \
        .update(read_at=timezone.now())
    if not updated and not Notification.objects.filter(id=notification_id, user=request.user).exists():
        raise Http404
    return Response({'unread': _unread(request.user)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_all_read(request):
    qs = Notification.objects.filter(user=request.user, read_at__isnull=True)
    category = request.data.get('category')
    if category in CATEGORIES:
        qs = qs.filter(category=category)
    qs.update(read_at=timezone.now())
    return Response({'unread': _unread(request.user)})


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def preferences(request):
    user = request.user
    if request.method == 'PATCH':
        prefs = prefs_of(user)
        for key in DEFAULT_PREFS:
            if key in request.data:
                value = request.data[key]
                if not isinstance(value, bool):
                    return Response({'detail': f'« {key} » doit valoir true ou false.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                prefs[key] = value
        user.notification_prefs = prefs
        user.save(update_fields=['notification_prefs', 'updated_at'])
    return Response(prefs_of(user))


# ─────────────────────────────────────────────────────────────
# Appareils (notifications push)
#   POST   /api/notifications/devices/  { token, platform }   enregistre le téléphone
#   DELETE /api/notifications/devices/  { token }             à la déconnexion
# ─────────────────────────────────────────────────────────────
import re as _re

_EXPO_TOKEN = _re.compile(r'^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_\-]{10,200}\]$')


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def devices(request):
    from .models import DeviceToken
    data = request.data if isinstance(request.data, dict) else {}
    token = str(data.get('token') or '').strip()
    if not _EXPO_TOKEN.match(token):
        return Response({'detail': 'Jeton de notification invalide.'}, status=status.HTTP_400_BAD_REQUEST)
    if request.method == 'DELETE':
        DeviceToken.objects.filter(token=token, user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    platform = str(data.get('platform') or '')[:10]
    # Un même téléphone peut changer de compte : le jeton suit le compte connecté
    DeviceToken.objects.update_or_create(token=token, defaults={'user': request.user, 'platform': platform})
    return Response({'registered': True}, status=status.HTTP_201_CREATED)
