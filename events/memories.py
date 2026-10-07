"""
events/memories.py — espace souvenirs

  GET/POST  /api/events/<id>/comments/                commentaires (après l'événement)
  DELETE    /api/events/<id>/comments/<comment_id>/   l'auteur ou un organisateur
  GET/POST  /api/events/<id>/memories/                photos (organisateurs et photographes)
  DELETE    /api/events/<id>/memories/<media_id>/     l'auteur de la photo ou un organisateur
  GET       /api/events/memories/photo/<jeton>/       fichier (lien signé 24 h)

Qui voit et commente :
- événement public : tout membre connecté ;
- événement privé : les invités (invitation en cours ou acceptée, ou billet) et l'équipe.
Les photos ne sont jamais publiques : réencodées en JPEG sans métadonnées
(position GPS…), rangées hors du dossier public, servies par lien signé.
"""
import io
import uuid
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from easevent.media import absolute_url

from .insights import phase
from .models import Event, EventComment, EventMedia
from .team import can_add_photos, is_manager, manager_ids, role_of

PHOTO_SALT = 'easevent.memories.photo'
PHOTO_TTL = 24 * 3600
PHOTO_MAX_BYTES = 12 * 1024 * 1024
PHOTO_MAX_SIDE = 2048
MAX_PHOTOS = 500
COMMENT_MAX = 1000


class MemoriesThrottle(UserRateThrottle):
    scope = 'memories'


def _event(event_id):
    event = Event.objects.select_related('organizer').filter(pk=event_id, deleted_at__isnull=True).first()
    if event is None:
        raise Http404
    return event


def is_guest(event, user):
    from tickets.models import Ticket
    if event.invitations.filter(invited_user=user).exclude(status__in=('revoked', 'declined')).exists():
        return True
    return Ticket.objects.filter(event=event, user=user, status__in=Ticket.ACTIVE).exists()


def can_view(event, user):
    if role_of(event, user):
        return True
    if event.status not in ('published', 'live', 'ended', 'souvenir', 'archived'):
        return False
    if event.visibility == 'public':
        return True
    return is_guest(event, user)


def _photo(media, request, user, event):
    token = signing.dumps(str(media.id), salt=PHOTO_SALT)
    uploader = media.uploader
    return {
        'id': str(media.id),
        'url': absolute_url(f'/api/events/memories/photo/{token}/', request),
        'width': media.width, 'height': media.height, 'caption': media.caption,
        'uploader': {'id': str(uploader.id), 'name': uploader.full_name} if uploader else None,
        'created_at': media.created_at.isoformat(),
        'can_delete': bool(uploader and uploader.id == user.id) or is_manager(event, user),
    }


def _comment(c, user, manager, team):
    from invitations.services import initials
    a = c.author
    return {
        'id': str(c.id), 'body': c.body, 'created_at': c.created_at.isoformat(),
        'author': {'id': str(a.id), 'name': a.full_name, 'initials': initials(a.first_name, a.last_name),
                   'avatar_url': a.avatar_url},
        'is_team': a.id in team,
        'can_delete': a.id == user.id or manager,
    }


# ─────────────────────────────────────────────────────────────
# Commentaires
# ─────────────────────────────────────────────────────────────
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([MemoriesThrottle])
def comments(request, event_id):
    event = _event(event_id)
    user = request.user
    if not can_view(event, user):
        raise Http404
    manager = is_manager(event, user)
    open_ = phase(event) == 'past'
    if request.method == 'GET':
        team = set(manager_ids(event))
        qs = event.comments.select_related('author')[:200]
        return Response({'open': open_, 'count': event.comments.count(),
                         'comments': [_comment(c, user, manager, team) for c in qs]})
    if not open_:
        return Response({'detail': "Les commentaires s'ouvrent à la fin de l'événement.", 'code': 'not_yet'},
                        status=status.HTTP_409_CONFLICT)
    body = str((request.data or {}).get('body', '') if isinstance(request.data, dict) else '').strip()
    if not body:
        return Response({'detail': 'Écrivez votre commentaire.', 'code': 'empty'}, status=status.HTTP_400_BAD_REQUEST)
    if len(body) > COMMENT_MAX:
        return Response({'detail': f'{COMMENT_MAX} caractères au maximum.', 'code': 'too_long'},
                        status=status.HTTP_400_BAD_REQUEST)
    c = EventComment.objects.create(event=event, author=user, body=body)
    if user.id != event.organizer_id:
        _notify_comment(event, user, body)
    return Response(_comment(c, user, manager, set(manager_ids(event))), status=status.HTTP_201_CREATED)


def _notify_comment(event, author, body):
    """Une notification par événement tant qu'elle n'est pas lue (mise à jour à chaque commentaire)."""
    from notifications.models import Notification
    from notifications.services import notify
    preview = body if len(body) <= 100 else body[:97] + '…'
    text = f'{author.first_name} : « {preview} »'
    existing = Notification.objects.filter(user=event.organizer, type=Notification.Type.EVENT_COMMENT,
                                           event=event, read_at__isnull=True).first()
    if existing:
        existing.body, existing.actor, existing.created_at = text[:255], author, timezone.now()
        existing.save(update_fields=['body', 'actor', 'created_at'])
        return
    notify(event.organizer, Notification.Type.EVENT_COMMENT, f'Commentaire sur {event.title}'[:160], text,
           actor=author, event=event)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def comment_detail(request, event_id, comment_id):
    event = _event(event_id)
    c = EventComment.objects.filter(pk=comment_id, event=event).first()
    if c is None or not (c.author_id == request.user.id or is_manager(event, request.user)):
        raise Http404
    c.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────
# Photos
# ─────────────────────────────────────────────────────────────
def _store(event, upload):
    from PIL import Image, ImageOps, UnidentifiedImageError
    if upload is None:
        raise ValueError('Aucune photo reçue.')
    if upload.size > PHOTO_MAX_BYTES:
        raise ValueError('Photo trop lourde (12 Mo maximum).')
    try:
        img = Image.open(upload)
        img.verify()
        upload.seek(0)
        img = ImageOps.exif_transpose(Image.open(upload))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValueError("Ce fichier n'est pas une image valide.")
    img = img.convert('RGB')
    img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=84, optimize=True, progressive=True)       # sans EXIF
    rel = f'memories/{event.id}/{uuid.uuid4().hex}.jpg'
    path = Path(settings.PRIVATE_MEDIA_ROOT) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return rel, img.width, img.height


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([MemoriesThrottle])
def memories(request, event_id):
    event = _event(event_id)
    user = request.user
    if not can_view(event, user):
        raise Http404
    if request.method == 'GET':
        qs = event.media.select_related('uploader').filter(is_approved=True, media_type='photo') \
            .exclude(r2_key='').order_by('-created_at')[:MAX_PHOTOS]
        return Response({
            'can_add': can_add_photos(event, user) and phase(event) != 'upcoming',
            'role': role_of(event, user),
            'phase': phase(event),
            'photos': [_photo(m, request, user, event) for m in qs],
        })
    if not can_add_photos(event, user):
        return Response({'detail': "Seuls les organisateurs et les photographes de l'événement ajoutent des photos.",
                         'code': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)
    if phase(event) == 'upcoming':
        return Response({'detail': "Les photos souvenirs s'ajoutent à partir du début de l'événement.",
                         'code': 'not_yet'}, status=status.HTTP_409_CONFLICT)
    if event.media.count() >= MAX_PHOTOS:
        return Response({'detail': f'{MAX_PHOTOS} photos au maximum par événement.', 'code': 'too_many'},
                        status=status.HTTP_409_CONFLICT)
    try:
        rel, width, height = _store(event, request.FILES.get('image'))
    except ValueError as exc:
        return Response({'detail': str(exc), 'code': 'invalid_image'}, status=status.HTTP_400_BAD_REQUEST)
    caption = str(request.data.get('caption', '') or '').strip()[:300]
    media = EventMedia.objects.create(event=event, uploader=user, r2_key=rel, media_type='photo',
                                      processing_status='done', width=width, height=height, caption=caption)
    _notify_guests(event, user)
    return Response(_photo(media, request, user, event), status=status.HTTP_201_CREATED)


def _notify_guests(event, uploader):
    """Une notification par jour et par événement aux invités : « De nouvelles photos souvenirs ».
    Pour un événement public, seuls les participants (billet ou invitation) sont prévenus."""
    from messaging.broadcast import audience
    from notifications.models import Notification
    from notifications.services import notify
    day = timezone.localdate().isoformat()
    users, _ = audience(event)
    for u in users:
        notify(u, Notification.Type.MEMORIES_ADDED, event.title, 'De nouvelles photos souvenirs ont été ajoutées.',
               actor=uploader, event=event, dedupe_key=f'memories:{event.id}:{day}')


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def memory_detail(request, event_id, media_id):
    event = _event(event_id)
    media = EventMedia.objects.filter(pk=media_id, event=event).first()
    if media is None or not (media.uploader_id == request.user.id or is_manager(event, request.user)):
        raise Http404
    path = Path(settings.PRIVATE_MEDIA_ROOT) / media.r2_key
    media.delete()
    try:
        if path.is_file() and str(path.resolve()).startswith(str(Path(settings.PRIVATE_MEDIA_ROOT).resolve())):
            path.unlink()
    except OSError:
        pass
    return Response(status=status.HTTP_204_NO_CONTENT)


def photo_file(request, token):
    try:
        media = EventMedia.objects.select_related('event').get(
            pk=signing.loads(token, salt=PHOTO_SALT, max_age=PHOTO_TTL))
    except (signing.BadSignature, EventMedia.DoesNotExist, ValueError):
        raise Http404
    if media.event.deleted_at is not None or not media.r2_key.startswith('memories/'):
        raise Http404
    path = Path(settings.PRIVATE_MEDIA_ROOT) / media.r2_key
    if not path.is_file():
        raise Http404
    response = FileResponse(open(path, 'rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'private, max-age=86400'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
