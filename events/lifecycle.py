"""
events/lifecycle.py — conséquences des changements d'un événement
═══════════════════════════════════════════════════════════════
- cancel_event()       suppression / annulation : tickets annulés (et
                       remboursés s'ils étaient payés), invitations
                       retirées, participants prévenus.
- revoke_invitation()  l'organisateur retire une invitation : le ticket de
                       l'invité est annulé (remboursé si payé), il est prévenu.
- notify_changes()     date, heure ou lieu modifiés : les participants
                       sont prévenus.
- audience()           personnes concernées par l'événement (ticket actif ou
                       invitation acceptée / en attente avec un compte).
═══════════════════════════════════════════════════════════════
"""
import hashlib
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def audience(event, include_pending_invites=True):
    """Utilisateurs concernés : ticket actif, ou invitation (en attente / acceptée) liée à un compte."""
    from invitations.models import Invitation
    from tickets.models import Ticket
    from users.models import User
    ids = set(Ticket.objects.filter(event=event, status__in=Ticket.ACTIVE).values_list('user_id', flat=True))
    statuses = ['confirmed'] + (['sent', 'opened'] if include_pending_invites else [])
    ids |= set(Invitation.objects.filter(event=event, status__in=statuses, invited_user__isnull=False)
               .values_list('invited_user_id', flat=True))
    ids.discard(event.organizer_id)
    return list(User.objects.filter(pk__in=ids, is_active=True))


def _cancel_ticket(ticket, reason):
    """Annule un ticket actif ; rembourse s'il était payé. Retourne True si un remboursement a échoué."""
    from tickets.models import Ticket
    refund_failed = False
    if ticket.payment_status == Ticket.PaymentStatus.PAID:
        from tickets.stripe_service import refund_ticket
        refund_failed = not refund_ticket(ticket, reason)
    Ticket.objects.filter(pk=ticket.pk, status__in=Ticket.ACTIVE).update(
        status=Ticket.Status.CANCELLED, updated_at=timezone.now())
    return refund_failed


def cancel_event(event, *, reason='event_cancelled', notify_guests=True):
    """Suppression d'un événement par son organisateur (ou de son compte)."""
    from invitations.models import Invitation
    from notifications.models import Notification
    from notifications.services import notify
    from tickets.models import Ticket

    guests = audience(event) if notify_guests else []
    refunds_failed = 0
    with transaction.atomic():
        for ticket in Ticket.objects.select_related('event').filter(event=event, status__in=Ticket.ACTIVE):
            refunds_failed += _cancel_ticket(ticket, reason)
        Invitation.objects.filter(event=event, status__in=['sent', 'opened', 'confirmed']).update(status='revoked')
        event.deleted_at = timezone.now()
        event.status = 'archived'
        event.save(update_fields=['deleted_at', 'status', 'updated_at'])

    when = timezone.localtime(event.start_date)
    for user in guests:
        notify(user, Notification.Type.EVENT_CANCELLED, 'Événement annulé',
               f"{event.title} du {when:%d/%m} n'aura pas lieu. Les tickets payés sont remboursés automatiquement.",
               event=event, dedupe_key=f'event-cancelled:{event.id}')
    return {'notified': len(guests), 'refunds_failed': refunds_failed}


def revoke_invitation(invitation):
    from notifications.models import Notification
    from notifications.services import notify
    from tickets.models import Ticket

    refunds_failed = 0
    with transaction.atomic():
        invitation.status = 'revoked'
        invitation.save(update_fields=['status', 'updated_at'])
        if invitation.invited_user_id:
            for ticket in Ticket.objects.filter(event=invitation.event, user_id=invitation.invited_user_id,
                                                status__in=Ticket.ACTIVE):
                refunds_failed += _cancel_ticket(ticket, 'invitation_revoked')
    if invitation.invited_user_id:
        notify(invitation.invited_user, Notification.Type.INVITATION_REVOKED, 'Invitation retirée',
               f"L'organisateur a retiré votre invitation à {invitation.event.title}.",
               event=invitation.event, dedupe_key=f'invitation-revoked:{invitation.id}')
    return {'refunds_failed': refunds_failed}


def snapshot(event):
    return {'start': event.start_date, 'end': event.end_date, 'place': (event.location_address or '').strip(),
            'online': event.is_online, 'link': event.online_link or ''}


def notify_changes(event, before):
    """Prévient les participants si la date, l'heure ou le lieu ont changé."""
    from notifications.models import Notification
    from notifications.services import notify

    after = snapshot(event)
    parts = []
    if before['start'] != after['start']:
        when = timezone.localtime(after['start'])
        parts.append(f'nouvelle date : {when:%d/%m} à {when:%H}h{when:%M}')
    if (before['place'], before['online']) != (after['place'], after['online']):
        parts.append('en ligne' if after['online'] else f"nouveau lieu : {after['place'][:80]}")
    elif after['online'] and before['link'] != after['link']:
        parts.append('nouveau lien de connexion')
    if not parts or event.status != 'published':
        return 0
    stamp = after['start'].isoformat() + '|' + after['place'] + '|' + after['link']
    guests = audience(event)
    for user in guests:
        notify(user, Notification.Type.EVENT_UPDATED, f'{event.title} a changé',
               ' · '.join(parts)[:255], event=event,
               dedupe_key=f"event-updated:{event.id}:{hashlib.sha1(stamp.encode()).hexdigest()[:16]}")
    return len(guests)
