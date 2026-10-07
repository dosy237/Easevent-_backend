"""
events/engagement.py — partager et aimer un événement public
════════════════════════════════════════════════════════════════
  POST/DELETE /api/events/<id>/like/     « J'aime » (compteur diffusé en direct à tous)
  POST        /api/events/<id>/share/    { user_ids: [...], message? } → carte de l'événement
                                         dans la conversation directe avec chaque ami
  GET         /e/<id>/                   page de partage : aperçu (Open Graph) quand le lien
                                         est collé dans WhatsApp, Facebook, un SMS… et
                                         ouverture de l'application (ou du store)
Seuls les événements publics et publiés se partagent et s'aiment ; un lien vers un
événement privé ne révèle rien.
════════════════════════════════════════════════════════════════
"""
import logging
import uuid

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .models import Event, EventLike

logger = logging.getLogger(__name__)
SHARE_MAX_RECIPIENTS = 20


class LikeThrottle(UserRateThrottle):
    scope = 'likes'


class ShareThrottle(UserRateThrottle):
    scope = 'share'


def _public_event(event_id):
    return Event.objects.select_related('organizer').filter(
        pk=event_id, status='published', visibility='public', deleted_at__isnull=True).first()


# ── « J'aime » ──────────────────────────────────────────────────────────────
def broadcast_likes_now(event_id):
    from messaging.realtime import broadcast_public
    broadcast_public({'type': 'likes', 'event_id': str(event_id),
                      'count': EventLike.objects.filter(event_id=event_id).count()})


def schedule_likes_broadcast(event_id):
    """Regroupe les « J'aime » d'une même seconde en une seule diffusion (le dernier total)."""
    if not cache.add(f'likes-bcast:{event_id}', 1, timeout=1):
        return                                           # une diffusion est déjà prévue
    def later():
        try:
            from .tasks import broadcast_likes
            broadcast_likes.apply_async((str(event_id),), countdown=1)
        except Exception:                                # file de tâches indisponible : tout de suite
            broadcast_likes_now(event_id)
    transaction.on_commit(later)


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
@throttle_classes([LikeThrottle])
def like(request, event_id):
    event = _public_event(event_id)
    if event is None:
        return Response({'detail': 'Événement introuvable.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                EventLike.objects.create(event=event, user=request.user)
        except IntegrityError:
            pass                                         # déjà aimé : idempotent
    else:
        EventLike.objects.filter(event=event, user=request.user).delete()
    schedule_likes_broadcast(event.id)
    return Response({'liked': request.method == 'POST', 'likes_count': event.likes.count()})


# ── Partage à des amis ──────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ShareThrottle])
def share(request, event_id):
    from messaging import services as messaging
    from users.models import User

    event = _public_event(event_id)
    if event is None:
        return Response({'detail': 'Seuls les événements publics peuvent être partagés.'}, status=status.HTTP_404_NOT_FOUND)
    data = request.data if isinstance(request.data, dict) else {}
    raw = data.get('user_ids')
    if not isinstance(raw, list) or not raw:
        return Response({'detail': 'Choisissez au moins un ami.'}, status=status.HTTP_400_BAD_REQUEST)
    ids = []
    for value in raw[:SHARE_MAX_RECIPIENTS]:
        try:
            ids.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    message = str(data.get('message') or '')[:500]
    sent, skipped = [], 0
    for friend in User.objects.filter(pk__in=set(ids), is_active=True):
        try:
            conv = messaging.get_or_create_direct(request.user, friend)
            messaging.send_event(conv, request.user, event, message, request)
            sent.append(str(conv.id))
        except messaging.MessagingError:
            skipped += 1                                  # pas (ou plus) amis
    if not sent:
        return Response({'detail': 'Vous ne pouvez partager qu’avec vos amis.', 'code': 'not_friends'},
                        status=status.HTTP_403_FORBIDDEN)
    return Response({'sent': len(sent), 'skipped': skipped, 'conversations': sent,
                     'message': f"Envoyé à {len(sent)} ami{'s' if len(sent) > 1 else ''}."})


# ── Page de partage (aperçu du lien) ────────────────────────────────────────
def share_page(request, event_id):
    from django.conf import settings
    from urllib.parse import quote
    from easevent.media import absolute_url, public_url
    from invitations.services import fr_datetime, price_label
    from invitations.public_views import _platform

    event = _public_event(event_id)
    platform = _platform(request)
    store_url = {'android': settings.ANDROID_STORE_URL, 'ios': settings.IOS_STORE_URL}.get(platform, '') \
        or settings.APP_DOWNLOAD_URL
    if event is None:
        response = render(request, 'events/share.html', {'missing': True, 'store_url': store_url}, status=404)
    else:
        cover = event.cover_image or (event.template_config or {}).get('cover_image')
        page = absolute_url(f'/e/{event.id}/', request)
        path = f'evenement/{event.id}'
        open_url = f'easevent://{path}'
        if platform == 'android':
            fallback = store_url or page
            open_url = (f'intent://{path}#Intent;scheme=easevent;package={settings.ANDROID_PACKAGE};'
                        f"S.browser_fallback_url={quote(fallback, safe='')};end")
        where = 'En ligne' if event.is_online and not event.location_address else (event.location_address or '')
        response = render(request, 'events/share.html', {
            'event': event, 'page_url': page,
            'cover_url': public_url(cover, request) if cover else absolute_url('/static/app/share-default.png', request),
            'date': fr_datetime(event.start_date, event) if event.start_date else '',
            'where': where, 'price': price_label(event),
            'description': ' · '.join(x for x in (fr_datetime(event.start_date, event) if event.start_date else '', where) if x),
            'open_url': open_url, 'store_url': store_url, 'platform': platform,
            'likes': event.likes.count(),
        })
    response['X-Robots-Tag'] = 'noindex'
    response['Cache-Control'] = 'public, max-age=300'
    return response
