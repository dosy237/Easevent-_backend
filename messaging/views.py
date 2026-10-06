"""
messaging/views.py — API de M15 / M16
  GET  /api/conversations/?event=&q=            mes conversations (organisateur ou invité)
  POST /api/conversations/  { event_id, participant_id? }   crée ou retrouve
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
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from easevent.media import public_url
from events.models import Event

from . import services
from .models import Conversation, Message

PAGE = 50


class MessageThrottle(UserRateThrottle):
    scope = 'messages'


def _initials(user):
    return ''.join(p[:1] for p in (user.first_name, user.last_name) if p).upper()


def _own(request, conversation_id):
    conv = (Conversation.objects.select_related('event', 'organizer', 'participant')
            .filter(pk=conversation_id).filter(Q(organizer=request.user) | Q(participant=request.user)).first())
    if conv is None or conv.event.deleted_at is not None:
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
        'event':  {'id': str(conv.event.id), 'title': conv.event.title,
                   'cover_image': public_url(conv.event.cover_image, request) if conv.event.cover_image else None},
        'last_message': {
            'text':       services.PREVIEW.get(last.system_type, '') if last.kind == 'system' else last.body[:140],
            'is_system':  last.kind == 'system',
            'system_type': last.system_type,
            'from_me':    last.sender_id == user.id,
            'created_at': last.created_at.isoformat(),
        } if last else None,
        'unread': unread,
        'online': services.is_recent(getattr(conv, f'{conv.other_side(side)}_seen_at'), services.ONLINE_WINDOW),
        'last_message_at': conv.last_message_at.isoformat(),
    }


def _message(msg, user, other_read_at):
    return {
        'id':          str(msg.id),
        'kind':        msg.kind,
        'system_type': msg.system_type,
        'body':        msg.body,
        'from_me':     msg.sender_id == user.id,
        'created_at':  msg.created_at.isoformat(),
        'read':        bool(other_read_at and msg.sender_id == user.id and other_read_at >= msg.created_at),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def conversations(request):
    if request.method == 'POST':
        return _open(request)
    qs = services.with_unread(Conversation.objects.select_related('event', 'organizer', 'participant')
                              .filter(event__deleted_at__isnull=True), request.user)
    event_id = request.query_params.get('event')
    if event_id:
        qs = qs.filter(event_id=event_id)
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
        events.setdefault(c['event']['id'], c['event']['title'])
    return Response({
        'results': items,
        'unread_conversations': sum(1 for c in items if c['unread']),
        'events': [{'id': k, 'title': v} for k, v in events.items()],
    })


def _open(request):
    from users.models import User
    try:
        event = Event.objects.select_related('organizer').get(pk=request.data.get('event_id'), deleted_at__isnull=True)
    except (Event.DoesNotExist, ValueError, TypeError, Exception):
        raise Http404
    participant_id = request.data.get('participant_id')
    if event.organizer_id == request.user.id:
        if not participant_id:
            return Response({'detail': "Choisissez l'invité à contacter.", 'code': 'participant_required'},
                            status=status.HTTP_400_BAD_REQUEST)
        participant = User.objects.filter(pk=participant_id, is_active=True).first()
        if participant is None:
            raise Http404
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
    e = conv.event
    data['event'].update({
        'start_date': e.start_date.isoformat(),
        'location_address': '' if e.is_online else (e.location_address or ''),
        'is_online': e.is_online,
    })
    data['guest_status'] = _guest_status(conv)
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
        if side == 'participant' and not services.may_converse(conv.event, request.user):
            return Response({'detail': "Vous n'avez plus accès à cet événement."}, status=status.HTTP_403_FORBIDDEN)
        try:
            msg = services.send(conv, request.user, request.data.get('body'))
        except services.MessagingError as exc:
            return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
        return Response(_message(msg, request.user, None), status=status.HTTP_201_CREATED)

    qs = conv.messages.all()
    after = parse_datetime(request.query_params.get('after') or '')
    before = parse_datetime(request.query_params.get('before') or '')
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
        'results':  [_message(m, request.user, other_read_at) for m in items],
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
    return Response(status=status.HTTP_204_NO_CONTENT)
