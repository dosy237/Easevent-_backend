"""
messaging/views.py — API de M15 / M16
  GET  /api/conversations/?event=&q=            mes conversations (organisateur ou invité)
  POST /api/conversations/  { event_id, participant_id? } | { friend_id }   crée ou retrouve
  GET  /api/conversations/unread-count/
  GET  /api/conversations/<id>/                 en-tête (interlocuteur, événement, statut)
  GET  /api/conversations/<id>/messages/?after=&before=     messages (+ marque lu)
  POST /api/conversations/<id>/messages/  { body }
  POST /api/conversations/<id>/typing/
Seuls l'organisateur et le participant de la conversation y ont accès.
"""
from django.db.models import Q
from django.http import Http404
from django.utils.dateparse import parse_datetime
from rest_framework import status

from events.wording import pass_word
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from django.core import signing
from django.http import FileResponse

from easevent.media import absolute_url, public_url
from events.models import Event

from . import services
from .models import Conversation, Message

PAGE = 50


def _uuid(value):
    import uuid
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _dt(value):
    try:
        return parse_datetime(value or '')
    except (TypeError, ValueError):
        return None


class MessageThrottle(UserRateThrottle):
    scope = 'messages'


def _initials(user):
    return ''.join(p[:1] for p in (user.first_name, user.last_name) if p).upper()


def _own(request, conversation_id):
    conv = (Conversation.objects.select_related('event', 'organizer', 'participant')
            .filter(pk=conversation_id).filter(Q(organizer=request.user) | Q(participant=request.user)).first())
    if conv is None or (conv.event_id and conv.event.deleted_at is not None):
        raise Http404
    return conv


def _person(user):
    return {'id': str(user.id), 'name': user.full_name, 'first_name': user.first_name,
            'initials': _initials(user), 'avatar_url': user.avatar_url}


def _guest_status(conv):
    """Statut de l'invité affiché dans la carte de l'événement (M16)."""
    from tickets.models import Ticket
    ticket = Ticket.objects.filter(event=conv.event, user=conv.participant, status__in=Ticket.ACTIVE).first()
    if ticket and ticket.status == Ticket.Status.GENERATED:
        return 'confirmed'
    inv = conv.event.invitations.filter(invited_user=conv.participant).exclude(status='revoked').first()
    if inv is None:
        return 'ticket_pending' if ticket else 'contact'
    return {'confirmed': 'to_validate', 'declined': 'declined', 'opened': 'opened', 'expired': 'expired'}.get(inv.status, 'invited')


def _summary(conv, user, request):
    side = conv.side(user)
    other = conv.participant if side == 'organizer' else conv.organizer
    last = conv.messages.order_by('-created_at').first()
    unread = conv.org_unread if side == 'organizer' else conv.part_unread
    return {
        'id':     str(conv.id),
        'role':   side,
        'other':  _person(other),
        'direct': conv.is_direct,
        'event':  {'id': str(conv.event.id), 'title': conv.event.title,
                   'cover_image': public_url(conv.event.cover_image, request) if conv.event.cover_image else None}
                  if conv.event_id else None,
        'last_message': {
            'text':       (services.PREVIEW.get(last.system_type, '') if last.kind == 'system'
                           else (last.body[:140] or services.KIND_PREVIEW.get(last.kind, '')) if last.kind == 'text'
                           else (last.body[:140] or f"Événement partagé : {last.meta.get('title', '')}") if last.kind == 'event'
                           else services.KIND_PREVIEW.get(last.kind, '')),
            'kind':       last.kind,
            'is_system':  last.kind == 'system',
            'system_type': last.system_type,
            'from_me':    last.sender_id == user.id,
            'created_at': last.created_at.isoformat(),
        } if last else None,
        'unread': unread,
        'online': services.is_recent(getattr(conv, f'{conv.other_side(side)}_seen_at'), services.ONLINE_WINDOW),
        'last_message_at': conv.last_message_at.isoformat(),
    }


ATTACHMENT_SALT = 'easevent.message.attachment'
ATTACHMENT_TTL = 60 * 60 * 24


def _message(msg, user, other_read_at, request=None):
    data = {
        'id':          str(msg.id),
        'kind':        msg.kind,
        'system_type': msg.system_type,
        'body':        msg.body,
        'from_me':     msg.sender_id == user.id,
        'created_at':  msg.created_at.isoformat(),
        'read':        bool(other_read_at and msg.sender_id == user.id and other_read_at >= msg.created_at),
        # Réponse automatique de l'assistant (au nom de l'organisateur)
        'assistant':   bool(msg.meta.get('assistant')) if isinstance(msg.meta, dict) else False,
        # Message envoyé à tous les invités (signé par le co-organisateur qui l'a écrit)
        'broadcast':   bool(msg.meta.get('broadcast')) if isinstance(msg.meta, dict) else False,
        'by':          (msg.meta.get('by') or '') if isinstance(msg.meta, dict) else '',
    }
    if msg.kind == 'image' and msg.attachment:
        # Lien signé valable 24 h : l'image d'une conversation n'est jamais publique
        token = signing.dumps(str(msg.id), salt=ATTACHMENT_SALT)
        data['image'] = {'url': absolute_url(f'/api/conversations/attachments/{token}/', request),
                         'width': msg.meta.get('width'), 'height': msg.meta.get('height')}
    elif msg.kind == 'event':
        data['event'] = msg.meta
    elif msg.kind == 'location':
        from events.geo import static_map_url
        data['location'] = {**msg.meta, 'map_image': static_map_url(msg.meta.get('lat'), msg.meta.get('lng'), request)}
    return data


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def conversations(request):
    if request.method == 'POST':
        return _open(request)
    qs = services.with_unread(Conversation.objects.select_related('event', 'organizer', 'participant')
                              .filter(event__deleted_at__isnull=True), request.user)
    event_id = request.query_params.get('event')
    if event_id:
        qs = qs.filter(event_id=_uuid(event_id)) if _uuid(event_id) else qs.none()
    q = (request.query_params.get('q') or '').strip()[:60]
    if q:
        terms = Q()
        for word in q.split()[:3]:
            terms &= (Q(participant__first_name__icontains=word) | Q(participant__last_name__icontains=word)
                      | Q(organizer__first_name__icontains=word) | Q(organizer__last_name__icontains=word)
                      | Q(event__title__icontains=word))
        qs = qs.filter(terms)
    items = [_summary(c, request.user, request) for c in qs.order_by('-last_message_at')[:100]]
    # Événements proposés en filtres (ceux qui ont des conversations)
    events = {}
    for c in items:
        if c['event']:
            events.setdefault(c['event']['id'], c['event']['title'])
    return Response({
        'results': items,
        'unread_conversations': sum(1 for c in items if c['unread']),
        'events': [{'id': k, 'title': v} for k, v in events.items()],
    })


def _open(request):
    from users.models import User
    data = request.data if isinstance(request.data, dict) else {}
    # Conversation directe avec un ami : { friend_id }
    if data.get('friend_id'):
        friend = User.objects.filter(pk=_uuid(data.get('friend_id')), is_active=True).first() if _uuid(data.get('friend_id')) else None
        if friend is None:
            raise Http404
        try:
            conv = services.get_or_create_direct(request.user, friend)
        except services.MessagingError as exc:
            return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
        conv = services.with_unread(Conversation.objects.select_related('event', 'organizer', 'participant')
                                    .filter(pk=conv.pk), request.user).get()
        return Response(_summary(conv, request.user, request), status=status.HTTP_201_CREATED)
    event_id = _uuid(data.get('event_id'))
    event = Event.objects.select_related('organizer').filter(pk=event_id, deleted_at__isnull=True).first() if event_id else None
    if event is None:
        raise Http404
    participant_id = data.get('participant_id')
    if event.organizer_id == request.user.id:
        if not participant_id:
            return Response({'detail': "Choisissez l'invité à contacter.", 'code': 'participant_required'},
                            status=status.HTTP_400_BAD_REQUEST)
        participant = User.objects.filter(pk=_uuid(participant_id), is_active=True).first() if _uuid(participant_id) else None
        if participant is None:
            raise Http404
        # L'organisateur n'écrit qu'à ses invités / participants (ou reprend une conversation existante)
        from tickets.models import Ticket
        known = (event.invitations.filter(invited_user=participant).exclude(status='revoked').exists()
                 or Ticket.objects.filter(event=event, user=participant).exists()
                 or Conversation.objects.filter(event=event, participant=participant).exists())
        if not known:
            return Response({'detail': "Vous ne pouvez écrire qu'à vos invités et participants.", 'code': 'forbidden'},
                            status=status.HTTP_403_FORBIDDEN)
    else:
        participant = request.user          # un invité ne peut ouvrir que sa propre conversation
    try:
        conv = services.get_or_create(event, participant)
    except services.MessagingError as exc:
        return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
    conv = services.with_unread(Conversation.objects.select_related('event', 'organizer', 'participant')
                                .filter(pk=conv.pk), request.user).get()
    return Response(_summary(conv, request.user, request), status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def unread_count(request):
    return Response({'unread': services.unread_total(request.user)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def detail(request, conversation_id):
    conv = _own(request, conversation_id)
    conv = services.with_unread(Conversation.objects.select_related('event', 'organizer', 'participant')
                                .filter(pk=conv.pk), request.user).get()
    data = _summary(conv, request.user, request)
    if conv.is_direct:
        data['guest_status'] = None
        data['can_write'] = services.may_write(conv, request.user)
        return Response(data)
    e = conv.event
    data['event'].update({
        'start_date': e.start_date.isoformat(),
        'location_address': '' if e.is_online else (e.location_address or ''),
        'is_online': e.is_online,
        'pass_word': pass_word(e),        # « Invitation générée » ou « Billet généré »
    })
    data['guest_status'] = _guest_status(conv)
    data['can_write'] = services.may_write(conv, request.user)
    return Response(data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def messages(request, conversation_id):
    conv = _own(request, conversation_id)
    side = conv.side(request.user)
    if request.method == 'POST':
        if MessageThrottle().allow_request(request, None) is False:
            return Response({'detail': 'Trop de messages en peu de temps. Patientez un instant.'},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not services.may_write(conv, request.user):
            detail = ("Vous n'êtes plus amis : cette conversation est en lecture seule." if conv.is_direct
                      else "Vous n'avez plus accès à cet événement.")
            return Response({'detail': detail}, status=status.HTTP_403_FORBIDDEN)
        try:
            if request.FILES.get('image'):
                msg = services.send_image(conv, request.user, request.FILES['image'], request.data.get('body', ''))
            elif request.data.get('kind') == 'location':
                msg = services.send_location(conv, request.user)
            else:
                msg = services.send(conv, request.user, request.data.get('body'))
        except services.MessagingError as exc:
            return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
        return Response(_message(msg, request.user, None, request), status=status.HTTP_201_CREATED)

    qs = conv.messages.all()
    after = _dt(request.query_params.get('after'))
    before = _dt(request.query_params.get('before'))
    if after:
        items = list(qs.filter(created_at__gt=after).order_by('created_at')[:PAGE])
        has_more = False
    else:
        if before:
            qs = qs.filter(created_at__lt=before)
        items = list(qs.order_by('-created_at')[:PAGE + 1])
        has_more = len(items) > PAGE
        items = list(reversed(items[:PAGE]))
    services.mark_seen(conv, side)
    conv.refresh_from_db()
    other = conv.other_side(side)
    other_read_at = getattr(conv, f'{other}_read_at')
    return Response({
        'results':  [_message(m, request.user, other_read_at, request) for m in items],
        'has_more': has_more,
        'other_read_at': other_read_at.isoformat() if other_read_at else None,
        'other_typing': services.is_recent(getattr(conv, f'{other}_typing_at'), services.TYPING_WINDOW),
        'other_online': services.is_recent(getattr(conv, f'{other}_seen_at'), services.ONLINE_WINDOW),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def typing(request, conversation_id):
    conv = _own(request, conversation_id)
    services.set_typing(conv, conv.side(request.user))
    from .realtime import broadcast_typing
    broadcast_typing(conv, conv.side(request.user))
    return Response(status=status.HTTP_204_NO_CONTENT)


def attachment(request, token):
    """GET /api/conversations/attachments/<jeton>/ — photo d'une conversation (lien signé 24 h)."""
    from pathlib import Path
    from django.conf import settings
    try:
        msg = Message.objects.select_related('conversation__event').get(
            pk=signing.loads(token, salt=ATTACHMENT_SALT, max_age=ATTACHMENT_TTL), kind='image')
    except (signing.BadSignature, Message.DoesNotExist, ValueError):
        raise Http404
    if msg.conversation.event_id and msg.conversation.event.deleted_at is not None:
        raise Http404
    path = Path(settings.PRIVATE_MEDIA_ROOT) / msg.attachment
    if not path.is_file():
        raise Http404
    response = FileResponse(open(path, 'rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'private, max-age=86400'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
