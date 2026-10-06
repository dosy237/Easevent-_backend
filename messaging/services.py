"""
messaging/services.py — règles de la messagerie (parcours H)

Qui peut échanger : l'organisateur d'un événement et une personne qui y
est invitée (invitation non révoquée), qui a un ticket, ou qui contacte
l'organisateur d'un événement public publié (M24 « Contacter »).
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
PREVIEW = {
    'invitation_sent': 'Invitation envoyée',
    'invitation_accepted': 'A accepté votre invitation',
    'invitation_declined': 'A décliné votre invitation',
    'ticket_generated': 'Ticket généré',
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


def send(conv, sender, body):
    from notifications.models import Notification
    from notifications.services import notify

    body = (body or '').strip()
    if not body:
        raise MessagingError('Le message est vide.', 'empty')
    if len(body) > 2000:
        raise MessagingError('Le message est limité à 2000 caractères.', 'too_long')
    side = conv.side(sender)
    now = timezone.now()
    msg = Message.objects.create(conversation=conv, sender=sender, body=body, created_at=now)
    Conversation.objects.filter(pk=conv.pk).update(
        last_message_at=now, **{f'{side}_read_at': now, f'{side}_seen_at': now, f'{side}_typing_at': None})

    # Une notification par conversation non lue, mise à jour à chaque message
    recipient = conv.participant if side == 'organizer' else conv.organizer
    preview = body if len(body) <= 120 else body[:117] + '…'
    existing = Notification.objects.filter(user=recipient, type='message_received', read_at__isnull=True,
                                           data__conversation_id=str(conv.id)).first()
    if existing:
        existing.body, existing.created_at, existing.actor = f'de {sender.first_name} : « {preview} »', now, sender
        existing.save(update_fields=['body', 'created_at', 'actor'])
    else:
        notify(recipient, Notification.Type.MESSAGE_RECEIVED, 'Nouveau message',
               f'de {sender.first_name} : « {preview} »', actor=sender, event=conv.event,
               data={'conversation_id': str(conv.id)})
    return msg


def mark_seen(conv, side, read=True):
    now = timezone.now()
    fields = {f'{side}_seen_at': now}
    if read:
        fields[f'{side}_read_at'] = now
    Conversation.objects.filter(pk=conv.pk).update(**fields)
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
                        & Q(messages__kind='text') & ~Q(messages__sender=user))
    return queryset.filter(as_org | Q(participant=user)).annotate(org_unread=org_unread, part_unread=part_unread)


def unread_total(user):
    total = 0
    for conv in with_unread(Conversation.objects.all(), user):
        total += conv.org_unread if conv.organizer_id == user.id else conv.part_unread
    return total


def is_recent(dt, seconds):
    return bool(dt) and (timezone.now() - dt).total_seconds() <= seconds
