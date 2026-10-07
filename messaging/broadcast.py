"""
messaging/broadcast.py — message de diffusion à tous les invités

Le message arrive dans la conversation de chaque invité qui a un compte
(invitation en cours ou acceptée, ou ticket), avec une notification.
Les invités sans compte (email, SMS) sont comptés à part : l'organisateur
sait combien ne l'ont pas reçu.
"""
from datetime import timedelta

from django.utils import timezone

from .models import Conversation, EventBroadcast, Message
from .services import MessagingError, get_or_create

MAX_PER_DAY = 5
MAX_LENGTH = 1000


def audience(event):
    """(comptes à prévenir, nombre d'invités sans compte)."""
    from events.team import manager_ids
    from tickets.models import Ticket
    from users.models import User

    team = set(manager_ids(event))
    live = event.invitations.exclude(status__in=('revoked', 'declined', 'expired'))
    ids = set(live.filter(invited_user__isnull=False).values_list('invited_user_id', flat=True))
    ids |= set(Ticket.objects.filter(event=event, status__in=Ticket.ACTIVE).values_list('user_id', flat=True))
    ids -= team
    users = list(User.objects.filter(id__in=ids, is_active=True, deleted_at__isnull=True))
    outsiders = live.filter(invited_user__isnull=True).count()
    return users, outsiders


def send_broadcast(event, sender, body, meta=None):
    from notifications.models import Notification
    from notifications.services import notify
    from .realtime import broadcast_message

    body = (body or '').replace('\r', '').strip()
    if not body:
        raise MessagingError('Écrivez votre message.', 'empty')
    if len(body) > MAX_LENGTH:
        raise MessagingError(f'Le message est limité à {MAX_LENGTH} caractères.', 'too_long')
    since = timezone.now() - timedelta(days=1)
    if EventBroadcast.objects.filter(event=event, created_at__gte=since).count() >= MAX_PER_DAY:
        raise MessagingError(f'{MAX_PER_DAY} messages à tous les invités par jour au maximum.', 'too_many', 429)
    users, outsiders = audience(event)
    if not users:
        raise MessagingError("Aucun invité n'a encore de compte pour recevoir ce message.", 'no_audience', 409)

    record = EventBroadcast.objects.create(event=event, sent_by=sender, body=body, recipients=len(users))
    signature = '' if sender.id == event.organizer_id else sender.full_name
    preview = body if len(body) <= 120 else body[:117] + '…'
    now = timezone.now()
    sent = 0
    for user in users:
        try:
            conv = get_or_create(event, user)
        except MessagingError:
            continue
        # Côté « organisateur » de la conversation (l'événement), signé par le co-organisateur s'il y a lieu
        msg = Message.objects.create(conversation=conv, sender=event.organizer, body=body, created_at=now,
                                     meta={'broadcast': str(record.id), **({'by': signature} if signature else {}), **(meta or {})})
        Conversation.objects.filter(pk=conv.pk).update(last_message_at=now, organizer_read_at=now,
                                                       organizer_seen_at=now)
        broadcast_message(conv, msg)
        notify(user, Notification.Type.EVENT_BROADCAST, event.title, preview, actor=sender, event=event,
               data={'conversation_id': str(conv.id)}, dedupe_key=f'broadcast:{record.id}:{user.id}')
        sent += 1
    if sent != record.recipients:
        EventBroadcast.objects.filter(pk=record.pk).update(recipients=sent)
    return {'id': str(record.id), 'recipients': sent, 'without_account': outsiders}


def history(event):
    return [{'id': str(b.id), 'body': b.body, 'recipients': b.recipients,
             'sent_by': b.sent_by.full_name if b.sent_by else '', 'created_at': b.created_at.isoformat()}
            for b in event.broadcasts.select_related('sent_by')[:20]]
