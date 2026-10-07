"""
tickets/gifts.py — « Payer pour un proche » : offrir un billet d'un événement public
════════════════════════════════════════════════════════════════
1. L'acheteur choisit le proche : un ami, un membre trouvé par la recherche, ou une
   personne pas encore inscrite (nom + email ou téléphone).
2. Il paie (carte via Stripe, ou Orange Money / MTN MoMo via Notch Pay). Gratuit : rien à payer.
3. Remise :
   - proche membre : billet généré À SON NOM, dans ses Invitations, avec un message de
     l'acheteur dans leur conversation et une notification ;
   - proche non inscrit : email ou SMS « X vous offre une place » ; le billet l'attend et
     lui est remis dès qu'il vérifie cet email ou ce numéro (claim_gifts, à l'inscription).
4. Annulation de l'événement : le billet remis suit le cycle normal (remboursé) ; un cadeau
   payé mais pas encore remis est remboursé à l'acheteur.
════════════════════════════════════════════════════════════════
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from events.wording import pass_word

from .models import Ticket, TicketGift
from .services import TicketError, active_ticket, generate_ticket, spots_left

logger = logging.getLogger(__name__)
MESSAGE_MAX = 300


def _clean(value, limit):
    value = ' '.join(str(value or '').split())
    if any(ch in value for ch in '<>{}'):
        raise TicketError('Caractères non autorisés.', 'invalid')
    return value[:limit]


def _check_event(event, buyer):
    if event.deleted_at is not None or event.status != 'published' or event.visibility != 'public':
        raise TicketError('Seuls les événements publics peuvent être offerts.', 'not_giftable', 403)
    if event.end_date and event.end_date < timezone.now():
        raise TicketError('Cet événement est terminé.', 'event_ended')
    if event.organizer_id == buyer.id:
        raise TicketError('Vous organisez cet événement : invitez vos proches depuis la liste des invités.', 'is_organizer')
    left = spots_left(event)
    if left is not None and left <= 0:
        raise TicketError('Cet événement est complet.', 'sold_out', 409)


def _find_member(email=None, phone_hash=None):
    from users.models import User
    qs = User.objects.filter(is_active=True, deleted_at__isnull=True)
    if email:
        return qs.filter(email__iexact=email, is_verified=True).first()
    if phone_hash:
        return qs.filter(phone_hash=phone_hash, phone_verified_at__isnull=False).first()
    return None


def create_gift(buyer, event, data):
    """data : {"recipient": {"user_id"} | {"name", "email"} | {"name", "phone"}, "message"}"""
    from invitations.crypto import blind_index, encrypt
    from invitations.services import normalize_email, normalize_phone
    from users.models import User

    _check_event(event, buyer)
    spec = data.get('recipient') if isinstance(data.get('recipient'), dict) else {}
    message = _clean(data.get('message'), MESSAGE_MAX)
    name = _clean(spec.get('name'), 80)
    recipient, email, phone, phone_hash = None, '', '', ''

    if spec.get('user_id'):
        recipient = User.objects.filter(pk=spec['user_id'], is_active=True, deleted_at__isnull=True).first()
        if recipient is None:
            raise TicketError('Personne introuvable.', 'recipient_not_found', 404)
    elif spec.get('email'):
        email = normalize_email(spec['email'])
        if not email:
            raise TicketError('Adresse email invalide.', 'invalid_email')
        recipient = _find_member(email=email)
    elif spec.get('phone'):
        phone = normalize_phone(spec['phone'])
        if not phone:
            raise TicketError('Numéro invalide : indiquez-le avec l’indicatif (ex. +237 6 90 00 00 00).', 'invalid_phone')
        phone_hash = blind_index(phone)
        recipient = _find_member(phone_hash=phone_hash)
    else:
        raise TicketError('Choisissez la personne à qui offrir ce billet.', 'recipient_required')

    if recipient is not None:
        if recipient.pk == buyer.pk:
            raise TicketError('Pour vous-même, choisissez « Pour moi ».', 'self_gift')
        if recipient.pk == event.organizer_id:
            raise TicketError("Cette personne organise l'événement.", 'recipient_is_organizer')
        existing = active_ticket(event, recipient)
        if existing and existing.status == Ticket.Status.GENERATED:
            raise TicketError(f'{recipient.first_name} a déjà sa place pour cet événement.', 'already_has_ticket', 409)
        name = name or f'{recipient.first_name} {recipient.last_name}'.strip()
    elif not name:
        raise TicketError('Indiquez le prénom de la personne.', 'name_required')

    # Même cadeau déjà commencé (double clic, retour arrière) : on le reprend
    same = TicketGift.objects.filter(event=event, buyer=buyer, status=TicketGift.Status.AWAITING_PAYMENT)
    same = same.filter(recipient=recipient) if recipient else (
        same.filter(recipient_email=email, recipient__isnull=True) if email else same.filter(recipient_phone_hash=phone_hash))
    gift = same.first()
    price = event.price if event.is_paid else 0
    if gift is None:
        gift = TicketGift.objects.create(
            event=event, buyer=buyer, recipient=recipient, recipient_name=name, recipient_email=email,
            recipient_phone=encrypt(phone) if phone else '', recipient_phone_hash=phone_hash, message=message,
            price=price, currency=event.currency or 'EUR',
            payment_status=Ticket.PaymentStatus.PENDING if price > 0 else Ticket.PaymentStatus.NOT_REQUIRED)
    elif message != gift.message:
        gift.message = message
        gift.save(update_fields=['message', 'updated_at'])
    if gift.is_free:
        mark_paid(gift, Ticket.PaymentStatus.NOT_REQUIRED)
        gift.refresh_from_db()
    return gift


def mark_paid(gift, payment_status=Ticket.PaymentStatus.PAID, payment_intent=''):
    """Paiement confirmé (webhook) : on remet le billet si possible. Idempotent."""
    with transaction.atomic():
        gift = TicketGift.objects.select_for_update(of=('self',)).select_related('event', 'buyer', 'recipient').get(pk=gift.pk)
        if gift.status in (TicketGift.Status.DELIVERED, TicketGift.Status.REFUND_NEEDED):
            return gift
        if payment_intent:
            gift.stripe_payment_intent_id = payment_intent
        gift.payment_status = payment_status
        gift.paid_at = gift.paid_at or timezone.now()
        gift.status = TicketGift.Status.PAID
        gift.save()
    event = gift.event
    if event.deleted_at is not None:
        refund(gift, 'event_cancelled')
        return gift
    if gift.recipient_id:
        return deliver(gift)
    _invite_outsider(gift)
    _notify_buyer(gift, 'pending_signup')
    return gift


def deliver(gift):
    """Billet au nom du proche (membre), message et notifications."""
    from notifications.models import Notification
    from notifications.services import notify

    recipient, event = gift.recipient, gift.event
    ticket = active_ticket(event, recipient)
    if ticket and ticket.status == Ticket.Status.GENERATED:
        # Le proche a pris sa place entre-temps : l'acheteur est remboursé
        refund(gift, 'already_has_ticket')
        return gift
    try:
        with transaction.atomic():
            if ticket is None:
                ticket = Ticket.objects.create(event=event, user=recipient, price=gift.price, currency=gift.currency,
                                               payment_status=Ticket.PaymentStatus.PENDING, dress_code=event.dress_code or '')
            ticket.purchased_by = gift.buyer
            # Le paiement suit le billet : une annulation de l'événement le rembourse automatiquement
            ticket.stripe_payment_intent_id = gift.stripe_payment_intent_id
            ticket.mobile_money_reference = gift.mobile_money_reference
            ticket.mobile_money_amount = gift.mobile_money_amount
            ticket.price, ticket.currency = gift.price, gift.currency
            ticket.save()
            ticket = generate_ticket(ticket, gift.payment_status)
            gift.ticket = ticket
            gift.status = TicketGift.Status.DELIVERED
            gift.delivered_at = timezone.now()
            gift.save(update_fields=['ticket', 'status', 'delivered_at', 'updated_at'])
    except (TicketError, IntegrityError) as exc:
        logger.warning('Cadeau %s non remis (%s) : remboursement', gift.id, exc)
        refund(gift, 'not_deliverable')
        return gift

    w = pass_word(event)
    buyer = gift.buyer
    notify(recipient, Notification.Type.TICKET_GIFT, f'{buyer.first_name} vous offre {w["a"]}',
           f'pour {event.title}. {w["One"]} prêt{w["e"]} dans vos invitations.',
           actor=buyer, event=event, ticket=ticket, dedupe_key=f'gift:{gift.id}')
    _message(gift, ticket)
    _notify_buyer(gift, 'delivered')
    return gift


def _message(gift, ticket):
    """Le cadeau apparaît dans la conversation entre l'acheteur et le proche."""
    from messaging import services as ms
    from messaging.models import Message
    try:
        conv = ms.get_or_create_pair(gift.buyer, gift.recipient)
        w = pass_word(gift.event)
        body = gift.message or f'Je vous offre {w["a"]} pour « {gift.event.title} ».'
        now = timezone.now()
        msg = Message.objects.create(conversation=conv, sender=gift.buyer, kind=Message.Kind.EVENT, body=body,
                                     meta={**ms.event_card(gift.event), 'gift': True, 'pass': w['one']}, created_at=now)
        ms._after_send(conv, gift.buyer, f'vous offre {w["a"]} pour « {gift.event.title} »', now)
        ms._broadcast(conv, msg)
    except Exception:
        logger.exception('Cadeau %s : message non envoyé', gift.id)


def _notify_buyer(gift, what):
    from notifications.models import Notification
    from notifications.services import notify
    w = pass_word(gift.event)
    if what == 'delivered':
        title, body = f'{w["One"]} offert{w["e"]} à {gift.recipient_name}', f'pour {gift.event.title}. Merci pour ce cadeau.'
    else:
        title = f'{w["One"]} réservé{w["e"]} pour {gift.recipient_name}'
        body = f'pour {gift.event.title}. Il lui sera remis dès son inscription sur Easevent.'
    notify(gift.buyer, Notification.Type.TICKET_GIFT, title, body, event=gift.event, dedupe_key=f'gift-buyer:{what}:{gift.id}')


def _invite_outsider(gift):
    """Proche pas encore inscrit : email ou SMS avec le lien de l'application."""
    from invitations.crypto import decrypt
    from invitations import sms
    from .stripe_service import _url
    w = pass_word(gift.event)
    buyer = f'{gift.buyer.first_name} {gift.buyer.last_name}'.strip()
    link = _url(None, f'/e/{gift.event_id}/')
    text = (f"Bonjour {gift.recipient_name},\n\n{buyer} vous offre {w['a']} pour « {gift.event.title} ».\n"
            + (f'\n« {gift.message} »\n' if gift.message else '')
            + f"\nPour recevoir {w['the']}, créez votre compte Easevent avec "
            + ('cette adresse email' if gift.recipient_email else 'ce numéro de téléphone')
            + f" : {link}\n\nL'équipe Easevent")
    try:
        if gift.recipient_email:
            send_mail(f"{buyer} vous offre {w['a']} — {gift.event.title}"[:150], text, settings.DEFAULT_FROM_EMAIL,
                      [gift.recipient_email], fail_silently=False)
        elif gift.recipient_phone:
            sms.send_sms(decrypt(gift.recipient_phone),
                         f"{buyer} vous offre {w['a']} pour {gift.event.title[:60]}. Créez votre compte Easevent avec ce numéro : {link}")
    except Exception:
        logger.exception('Cadeau %s : email/SMS non envoyé', gift.id)


def claim_gifts(user, phone_hash=None, email=None):
    """À l'inscription (email ou téléphone vérifié) : les billets offerts qui attendaient cette personne."""
    from django.db.models import Q
    match = Q()
    if email:
        match |= Q(recipient_email__iexact=email)
    if phone_hash:
        match |= Q(recipient_phone_hash=phone_hash)
    if not match:
        return 0
    count = 0
    for gift in TicketGift.objects.filter(match, recipient__isnull=True, status=TicketGift.Status.PAID).select_related('event', 'buyer'):
        if gift.buyer_id == user.id:
            continue
        gift.recipient = user
        gift.save(update_fields=['recipient', 'updated_at'])
        deliver(gift)
        count += 1
    return count


def refund(gift, reason):
    """Rembourse l'acheteur (Stripe) ; Mobile Money : signalé pour un remboursement depuis Notch Pay."""
    from notifications.models import Notification
    from notifications.services import notify
    done = False
    if gift.payment_status == Ticket.PaymentStatus.NOT_REQUIRED:
        done = True
    elif gift.stripe_payment_intent_id:
        try:
            from .stripe_service import _configure
            import stripe
            _configure()
            stripe.Refund.create(payment_intent=gift.stripe_payment_intent_id, reverse_transfer=True,
                                 refund_application_fee=True, metadata={'gift_id': str(gift.id), 'reason': reason},
                                 idempotency_key=f'gift-refund-{gift.id}')
            done = True
        except Exception:
            logger.exception('Remboursement Stripe du cadeau %s impossible', gift.id)
    else:
        logger.error('Remboursement Mobile Money à faire : cadeau %s, référence %s, %s FCFA',
                     gift.id, gift.mobile_money_reference, gift.mobile_money_amount)
    TicketGift.objects.filter(pk=gift.pk).update(
        status=TicketGift.Status.CANCELLED if done else TicketGift.Status.REFUND_NEEDED,
        payment_status=Ticket.PaymentStatus.REFUNDED if done and gift.payment_status == Ticket.PaymentStatus.PAID
        else gift.payment_status)
    if gift.payment_status == Ticket.PaymentStatus.PAID:
        notify(gift.buyer, Notification.Type.PAYMENT_REFUNDED, 'Cadeau remboursé' if done else 'Remboursement en cours',
               f'{gift.price} {gift.currency} pour {gift.event.title} ({gift.recipient_name}).',
               event=gift.event, dedupe_key=f'gift-refund:{gift.id}')
    return done


def cancel(gift):
    if gift.status != TicketGift.Status.AWAITING_PAYMENT:
        raise TicketError('Ce cadeau ne peut plus être annulé.', 'not_cancellable')
    if gift.payment_status == Ticket.PaymentStatus.PROCESSING:
        raise TicketError('Un paiement est en cours de confirmation.', 'payment_processing', 409)
    gift.status = TicketGift.Status.CANCELLED
    gift.save(update_fields=['status', 'updated_at'])
    return gift


def payload(gift):
    from invitations.crypto import decrypt
    from invitations.services import mask_phone
    e = gift.event
    contact = (f'{gift.recipient.first_name} {gift.recipient.last_name}'.strip() if gift.recipient
               else gift.recipient_email or mask_phone(decrypt(gift.recipient_phone)))
    return {
        'id': str(gift.id), 'status': gift.status, 'payment_status': gift.payment_status,
        'price': str(gift.price), 'currency': gift.currency, 'message': gift.message,
        'recipient': {'name': gift.recipient_name, 'is_member': gift.recipient_id is not None, 'contact': contact},
        'event': {'id': str(e.id), 'title': e.title, 'start_date': e.start_date.isoformat() if e.start_date else None,
                  'timezone': e.timezone, 'event_type': e.event_type, 'visibility': e.visibility,
                  'cover_image': None},
        'pass_word': pass_word(e),
        'created_at': gift.created_at.isoformat(),
    }
