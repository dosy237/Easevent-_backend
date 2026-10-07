"""
messaging/services.py — règles de la messagerie (parcours H)

Deux sortes de conversations :
- d'événement : l'organisateur et une personne invitée (invitation non révoquée),
  qui a un ticket, ou qui contacte l'organisateur d'un événement public (M24) ;
- directe : deux amis (amitié acceptée), par exemple pour partager un événement.
"""
from datetime import datetime, timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Conversation, Message

EPOCH = datetime(2000, 1, 1, tzinfo=dt_timezone.utc)
ONLINE_WINDOW = 30      # secondes : « En ligne » si actif dans la conversation
TYPING_WINDOW = 6       # secondes : « en train d'écrire »
KIND_PREVIEW = {'image': 'Photo', 'location': 'Itinéraire', 'event': 'Événement partagé'}
PREVIEW = {
    'invitation_sent': 'Invitation envoyée',
    'invitation_accepted': 'A accepté votre invitation',
    'invitation_declined': 'A décliné votre invitation',
    'ticket_generated': 'Inscription confirmée',
}


class MessagingError(Exception):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def may_converse(event, participant):
    from tickets.models import Ticket

    if event.deleted_at is not None or participant.id == event.organizer_id:
        return False
    if event.invitations.filter(invited_user=participant).exclude(status='revoked').exists():
        return True
    if Ticket.objects.filter(event=event, user=participant, status__in=Ticket.ACTIVE).exists():
        return True
    return event.status == 'published' and event.visibility == 'public'


def are_friends(a, b):
    from social.models import Friendship
    return Friendship.objects.filter(
        Q(requester=a, addressee=b) | Q(requester=b, addressee=a), status='accepted').exists()


def may_write(conv, user):
    """Peut encore écrire dans cette conversation (amitié retirée, accès à l'événement perdu…)."""
    if conv.is_direct:
        other = conv.participant if user.id == conv.organizer_id else conv.organizer
        return other.is_active and (are_friends(user, other) or linked_by_gift(user, other)
                                    or linked_by_team(user, other))
    if user.id == conv.organizer_id:
        return True
    return may_converse(conv.event, user)


def linked_by_gift(a, b):
    """Un billet offert entre deux personnes leur ouvre la conversation (remerciements, détails)."""
    from tickets.models import TicketGift
    return TicketGift.objects.filter(Q(buyer=a, recipient=b) | Q(buyer=b, recipient=a), status='delivered').exists()


def linked_by_team(a, b):
    """Organisateurs d'un même événement : ils s'accordent par message (répartition des invités…)."""
    from events.team import linked_by_team as _linked
    return _linked(a, b)


def get_or_create_pair(a, b):
    """Conversation directe entre deux personnes, sans condition (usage interne : billet offert)."""
    a, b = sorted((a, b), key=lambda u: str(u.id))
    try:
        with transaction.atomic():
            conv, _ = Conversation.objects.get_or_create(
                event=None, organizer=a, participant=b, defaults={'last_message_at': timezone.now()})
    except IntegrityError:
        conv = Conversation.objects.get(event__isnull=True, organizer=a, participant=b)
    return conv


def get_or_create_direct(user, friend):
    """Conversation directe entre deux amis (une seule par paire)."""
    if user.id == friend.id or not friend.is_active or not (are_friends(user, friend) or linked_by_team(user, friend)):
        raise MessagingError('Vous ne pouvez écrire qu’à vos amis.', 'not_friends', 403)
    a, b = sorted((user, friend), key=lambda u: str(u.id))
    try:
        with transaction.atomic():
            conv, _ = Conversation.objects.get_or_create(
                event=None, organizer=a, participant=b, defaults={'last_message_at': timezone.now()})
    except IntegrityError:
        conv = Conversation.objects.get(event__isnull=True, organizer=a, participant=b)
    return conv


def _bump(conv, at):
    Conversation.objects.filter(pk=conv.pk, last_message_at__lt=at).update(last_message_at=at)


def add_system(conv, system_type, at=None):
    """Ajoute un message système (une seule fois par type et par conversation)."""
    at = at or timezone.now()
    try:
        with transaction.atomic():
            Message.objects.create(conversation=conv, kind=Message.Kind.SYSTEM, system_type=system_type, created_at=at)
    except IntegrityError:
        return
    _bump(conv, at)


def _backfill(conv):
    """À la création : rappelle l'historique de l'invitation dans le fil."""
    if conv.is_direct:
        return
    inv = conv.event.invitations.filter(invited_user=conv.participant).exclude(status='revoked').first()
    if inv is None:
        return
    add_system(conv, 'invitation_sent', inv.sent_at)
    if inv.status == 'confirmed' and inv.responded_at:
        add_system(conv, 'invitation_accepted', inv.responded_at)
    elif inv.status == 'declined' and inv.responded_at:
        add_system(conv, 'invitation_declined', inv.responded_at)


def get_or_create(event, participant):
    if not may_converse(event, participant):
        raise MessagingError("Cette conversation n'est pas possible pour cet événement.", 'forbidden', 403)
    try:
        with transaction.atomic():
            conv, created = Conversation.objects.get_or_create(
                event=event, participant=participant,
                defaults={'organizer_id': event.organizer_id, 'last_message_at': timezone.now()},
            )
    except IntegrityError:
        conv, created = Conversation.objects.get(event=event, participant=participant), False
    if created:
        _backfill(conv)
    return conv


def record_invitation_event(invitation, system_type):
    """Invitation acceptée / déclinée, ticket généré : visible dans M15 / M16 pour l'organisateur."""
    if not invitation or not invitation.invited_user_id:
        return
    try:
        conv = get_or_create(invitation.event, invitation.invited_user)
    except MessagingError:
        return
    add_system(conv, system_type)


IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_MAX_SIDE = 1600


def _after_send(conv, sender, preview, now):
    """Dernier message, lecture de l'expéditeur, notification du destinataire (une par conversation)."""
    from notifications.models import Notification
    from notifications.services import notify

    side = conv.side(sender)
    Conversation.objects.filter(pk=conv.pk).update(
        last_message_at=now, **{f'{side}_read_at': now, f'{side}_seen_at': now, f'{side}_typing_at': None})
    recipient = conv.participant if side == 'organizer' else conv.organizer
    preview = preview if len(preview) <= 120 else preview[:117] + '…'
    existing = Notification.objects.filter(user=recipient, type='message_received', read_at__isnull=True,
                                           data__conversation_id=str(conv.id)).first()
    from events.wording import de
    text = f'{de(sender.first_name)} : « {preview} »'
    if existing:
        existing.body, existing.created_at, existing.actor = text, now, sender
        existing.save(update_fields=['body', 'created_at', 'actor'])
    else:
        # notify() pousse déjà la première notification de la conversation
        notify(recipient, Notification.Type.MESSAGE_RECEIVED, 'Nouveau message', text, actor=sender,
               event=conv.event if conv.event_id else None, data={'conversation_id': str(conv.id)})
    if existing:
        # Chaque nouveau message sonne sur le téléphone, comme une messagerie classique
        from notifications.push import push_to_user
        push_to_user(recipient, 'message_received', sender.full_name or 'Nouveau message', preview,
                     data={'conversation_id': str(conv.id), **({'event_id': str(conv.event_id)} if conv.event_id else {})},
                     thread=f'conv:{conv.id}')



def _broadcast(conv, msg):
    # Temps réel : les deux côtés reçoivent le message tout de suite (WebSocket)
    from .realtime import broadcast_message
    broadcast_message(conv, msg)


def send(conv, sender, body):
    body = (body or '').strip()
    if not body:
        raise MessagingError('Le message est vide.', 'empty')
    if len(body) > 2000:
        raise MessagingError('Le message est limité à 2000 caractères.', 'too_long')
    now = timezone.now()
    msg = Message.objects.create(conversation=conv, sender=sender, body=body, created_at=now)
    _after_send(conv, sender, body, now)
    _broadcast(conv, msg)
    if conv.event_id:
        from .assistant import organizer_replied
        if sender.id == conv.organizer_id:
            organizer_replied(conv, body)
        elif conv.event.assistant_enabled:
            # Réponse automatique éventuelle, en arrière-plan
            from easevent.dispatch import dispatch
            from .tasks import answer_question
            dispatch(answer_question, str(msg.id))
    return msg


def send_image(conv, sender, upload, caption=''):
    """
    Photo ou capture d'écran : réencodée en JPEG (1600 px max), sans
    métadonnées (position GPS, appareil…), rangée hors du dossier public.
    """
    import io
    import uuid as _uuid
    from pathlib import Path
    from django.conf import settings
    from PIL import Image, ImageOps, UnidentifiedImageError

    if upload is None:
        raise MessagingError('Aucune image reçue.', 'empty')
    if upload.size > IMAGE_MAX_BYTES:
        raise MessagingError('Image trop lourde (8 Mo maximum).', 'too_large')
    caption = (caption or '').strip()[:500]
    try:
        img = Image.open(upload)
        img.verify()
        upload.seek(0)
        img = ImageOps.exif_transpose(Image.open(upload))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise MessagingError("Ce fichier n'est pas une image valide.", 'invalid_image')
    img = img.convert('RGB')
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=82, optimize=True, progressive=True)     # sans EXIF

    rel = f'messages/{conv.id}/{_uuid.uuid4().hex}.jpg'
    path = Path(settings.PRIVATE_MEDIA_ROOT) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())

    now = timezone.now()
    msg = Message.objects.create(conversation=conv, sender=sender, kind=Message.Kind.IMAGE, body=caption,
                                 attachment=rel, meta={'width': img.width, 'height': img.height}, created_at=now)
    _after_send(conv, sender, caption or 'Photo', now)
    _broadcast(conv, msg)
    return msg


def send_location(conv, sender):
    """« Envoyer l'itinéraire » : le lieu de l'événement, avec carte et lien d'itinéraire."""
    from events.geo import maps_links
    event = conv.event
    if event is None or event.is_online or not event.location_address:
        raise MessagingError("Cet événement n'a pas d'adresse.", 'no_address')
    lat = float(event.latitude) if event.latitude is not None else None
    lng = float(event.longitude) if event.longitude is not None else None
    now = timezone.now()
    msg = Message.objects.create(
        conversation=conv, sender=sender, kind=Message.Kind.LOCATION, body=event.location_address, created_at=now,
        meta={'address': event.location_address, 'title': event.title, 'lat': lat, 'lng': lng,
              **maps_links(event.location_address, lat, lng)},
    )
    _after_send(conv, sender, f'Itinéraire : {event.location_address}', now)
    _broadcast(conv, msg)
    return msg


def event_card(event, request=None):
    """Aperçu d'un événement public partagé dans une conversation (titre, photo, date, lieu)."""
    from easevent.media import public_url
    cover = event.cover_image or (event.template_config or {}).get('cover_image')
    return {
        'event_id': str(event.id), 'title': event.title, 'type': event.get_event_type_display(),
        'cover_image': public_url(cover, request) if cover else None,
        'start_date': event.start_date.isoformat() if event.start_date else None,
        'location': 'En ligne' if event.is_online and not event.location_address else (event.location_address or ''),
        'is_paid': bool(event.is_paid),
    }


def send_event(conv, sender, event, comment='', request=None):
    """Partage d'un événement public : une carte cliquable dans la conversation, avec un mot facultatif."""
    comment = (comment or '').strip()[:500]
    now = timezone.now()
    msg = Message.objects.create(conversation=conv, sender=sender, kind=Message.Kind.EVENT, body=comment,
                                 meta=event_card(event, request), created_at=now)
    _after_send(conv, sender, comment or f'a partagé « {event.title} »', now)
    _broadcast(conv, msg)
    return msg


def mark_seen(conv, side, read=True):
    now = timezone.now()
    fields = {f'{side}_seen_at': now}
    if read:
        fields[f'{side}_read_at'] = now
    Conversation.objects.filter(pk=conv.pk).update(**fields)
    if read:
        # Accusé de lecture instantané chez l'interlocuteur, badges à jour chez soi
        from .realtime import broadcast_badge, broadcast_read
        broadcast_read(conv, side, now)
        broadcast_badge(conv.organizer_id if side == 'organizer' else conv.participant_id)
    if read:
        from notifications.models import Notification
        recipient = conv.organizer_id if side == 'organizer' else conv.participant_id
        Notification.objects.filter(user_id=recipient, type='message_received', read_at__isnull=True,
                                    data__conversation_id=str(conv.id)).update(read_at=now)


def set_typing(conv, side):
    Conversation.objects.filter(pk=conv.pk).update(**{f'{side}_typing_at': timezone.now(), f'{side}_seen_at': timezone.now()})


def with_unread(queryset, user):
    """Annote « unread » : messages reçus après ma dernière lecture.
    L'organisateur compte aussi les événements système (« A accepté… »)."""
    as_org = Q(organizer=user)
    org_unread = Count('messages', filter=Q(messages__created_at__gt=Coalesce(F('organizer_read_at'), Value(EPOCH)))
                       & ~Q(messages__sender=user))
    part_unread = Count('messages', filter=Q(messages__created_at__gt=Coalesce(F('participant_read_at'), Value(EPOCH)))
                        & ~Q(messages__kind='system') & ~Q(messages__sender=user))
    return queryset.filter(as_org | Q(participant=user)).annotate(org_unread=org_unread, part_unread=part_unread)


def unread_total(user):
    total = 0
    for conv in with_unread(Conversation.objects.filter(event__deleted_at__isnull=True), user):
        total += conv.org_unread if conv.organizer_id == user.id else conv.part_unread
    return total


def is_recent(dt, seconds):
    return bool(dt) and (timezone.now() - dt).total_seconds() <= seconds
