"""
tickets/services.py
═══════════════════════════════════════════════════════════════
Règles métier des tickets (MVP §5). Toutes les vues passent par ici.
═══════════════════════════════════════════════════════════════
"""
from events.wording import pass_word
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Ticket

EXPIRY_AFTER_END = timedelta(days=7)


class TicketError(Exception):
    """Erreur métier présentable à l'utilisateur."""

    def __init__(self, message, code='ticket_error', status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def generated_count(event):
    return event.tickets.filter(status=Ticket.Status.GENERATED).count()


def spots_left(event):
    """Places restantes (None = illimité). Seuls les tickets générés réservent une place."""
    if not event.max_guests:
        return None
    return max(0, event.max_guests - generated_count(event))


def can_access_event(event, user):
    """Public publié, ou privé avec une invitation valide, ou organisateur."""
    if event.deleted_at is not None:
        return False
    if event.organizer_id == user.id:
        return True
    if event.status != 'published':
        return False
    if event.visibility == 'public':
        return True
    return event.invitations.filter(invited_user=user, expires_at__gt=timezone.now()) \
        .exclude(status__in=['revoked', 'expired']).exists()


def active_ticket(event, user):
    return Ticket.objects.filter(event=event, user=user, status__in=Ticket.ACTIVE).first()


def create_pending_ticket(event, user, invitation=None):
    """
    Crée le ticket « en attente » de l'utilisateur (ou retourne celui
    qui existe déjà : un seul ticket actif par événement et par personne).
    """
    if event.organizer_id == user.id:
        raise TicketError("Vous êtes l'organisateur de cet événement.", 'is_organizer')
    if not can_access_event(event, user):
        raise TicketError("Cet événement n'est pas accessible.", 'forbidden', 403)
    if event.end_date and event.end_date < timezone.now():
        raise TicketError('Cet événement est terminé.', 'event_ended')

    existing = active_ticket(event, user)
    if existing:
        if invitation and not existing.invitation_id:
            existing.invitation = invitation
            existing.save(update_fields=['invitation', 'updated_at'])
        return existing, False

    left = spots_left(event)
    if left is not None and left <= 0:
        raise TicketError('Cet événement est complet.', 'sold_out', 409)

    price = event.price if event.is_paid else 0
    try:
        with transaction.atomic():
            ticket = Ticket.objects.create(
                event=event, user=user, invitation=invitation,
                price=price, currency=event.currency or 'EUR',
                payment_status=(Ticket.PaymentStatus.PENDING if price > 0
                                else Ticket.PaymentStatus.NOT_REQUIRED),
                dress_code=event.dress_code or '',
            )
    except IntegrityError:
        # Double clic / requêtes simultanées : la contrainte d'unicité a joué
        return active_ticket(event, user), False
    return ticket, True


def ensure_still_accessible(ticket):
    """
    Avant de valider ou de payer un ticket en attente : l'événement doit
    toujours exister, être publié, accessible (invitation non retirée pour
    un événement privé) et ne pas être terminé.
    """
    event = ticket.event
    if event.deleted_at is not None:
        raise TicketError("Cet événement a été annulé par l'organisateur.", 'event_cancelled', 410)
    if event.end_date and event.end_date < timezone.now():
        raise TicketError('Cet événement est terminé.', 'event_ended')
    if not can_access_event(event, ticket.user):
        raise TicketError("Cet événement n'est plus accessible.", 'forbidden', 403)


def generate_ticket(ticket, payment_status):
    """
    pending → generated. La place est vérifiée à ce moment-là, sous verrou
    sur l'événement pour éviter la survente quand deux personnes valident
    en même temps.
    """
    from events.models import Event

    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=ticket.event_id)
        ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
        if ticket.status == Ticket.Status.GENERATED:
            return ticket
        if ticket.status != Ticket.Status.PENDING:
            raise TicketError("Ce ticket n'est plus valable.", 'not_pending')
        if event.max_guests and generated_count(event) >= event.max_guests:
            # Un paiement déjà encaissé ne doit jamais être perdu : le ticket
            # est quand même généré, l'organisateur voit le dépassement.
            if payment_status != Ticket.PaymentStatus.PAID:
                raise TicketError('Cet événement est complet.', 'sold_out', 409)
        ticket.status = Ticket.Status.GENERATED
        ticket.payment_status = payment_status
        ticket.generated_at = timezone.now()
        ticket.dress_code = event.dress_code or ''
        ticket.save()

        if ticket.invitation_id:
            from invitations.models import Invitation
            Invitation.objects.filter(pk=ticket.invitation_id).exclude(status='confirmed').update(
                status='confirmed', responded_at=timezone.now())

        from notifications.models import Notification
        from notifications.services import notify_event_full, notify_guest_activity, notify_on_commit
        paid = payment_status == Ticket.PaymentStatus.PAID
        # Organisateur : nouveau participant (réponses groupées) et « Complet ! »
        guest, kind = ticket.user, ('paid' if paid else ('accepted' if ticket.invitation_id else 'joined'))
        if not ticket.invitation_id or paid:
            transaction.on_commit(lambda: notify_guest_activity(event, guest, kind))
        if event.max_guests and generated_count(event) >= event.max_guests:
            transaction.on_commit(lambda: notify_event_full(event))
        w = pass_word(event)
        notify_on_commit(
            ticket.user, Notification.Type.TICKET_GENERATED, f"{w['One']} générée" if w['e'] else f"{w['One']} généré",
            f"pour {event.title}{' · paiement reçu' if paid else ''}. Présentez le QR code à l'entrée.",
            event=event, ticket=ticket, dedupe_key=f'ticket-generated:{ticket.id}',
        )
        if ticket.invitation_id:
            from django.db import transaction as _tx
            from messaging.services import record_invitation_event
            invitation = ticket.invitation
            _tx.on_commit(lambda: record_invitation_event(invitation, 'ticket_generated'))
    return ticket


def validate_free_ticket(ticket):
    if ticket.status == Ticket.Status.GENERATED:
        return ticket
    if ticket.status != Ticket.Status.PENDING:
        raise TicketError("Ce ticket n'est plus valable.", 'not_pending')
    if not ticket.is_free:
        raise TicketError('Ce ticket est payant : réglez-le pour le générer.', 'payment_required', 402)
    ensure_still_accessible(ticket)
    return generate_ticket(ticket, Ticket.PaymentStatus.NOT_REQUIRED)


def cancel_ticket(ticket):
    """Annuler un ticket en attente. Accepter puis annuler remet l'invitation en « déclinée »."""
    if ticket.status != Ticket.Status.PENDING:
        raise TicketError('Seul un ticket en attente peut être annulé.', 'not_pending')
    if ticket.payment_status == Ticket.PaymentStatus.PROCESSING:
        raise TicketError('Un paiement est en cours de confirmation pour ce ticket.', 'payment_processing', 409)
    ticket.status = Ticket.Status.CANCELLED
    ticket.save(update_fields=['status', 'updated_at'])
    if ticket.invitation_id:
        from invitations.models import Invitation
        Invitation.objects.filter(pk=ticket.invitation_id).update(status='declined', responded_at=timezone.now())
    return ticket


def expire_old_tickets(queryset):
    """Expiration paresseuse : fin de l'événement + 7 jours."""
    limit = timezone.now() - EXPIRY_AFTER_END
    queryset.filter(status__in=Ticket.ACTIVE, event__end_date__lt=limit).update(
        status=Ticket.Status.EXPIRED, updated_at=timezone.now())
