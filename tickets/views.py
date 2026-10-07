"""
tickets/views.py
═══════════════════════════════════════════════════════════════
GET  /api/tickets/mine/?status=pending|generated|archived
POST /api/events/<id>/tickets/            → ticket « en attente » (Participer / Payer)
GET  /api/tickets/<id>/                   → détail (+ QR si généré)
POST /api/tickets/<id>/validate/          → gratuit : généré
POST /api/tickets/<id>/checkout/          → payant : URL Stripe Checkout
POST /api/tickets/<id>/cancel/
POST /api/tickets/<id>/pdf-link/          → lien de téléchargement signé (5 min)
GET  /api/tickets/pdf/<jeton>/            → PDF du ticket
GET  /api/tickets/counts/                 → compteurs (badge de l'onglet)
GET  /api/payments/connect/status/        → organisateur : état des paiements
POST /api/payments/connect/onboard/       → lien d'activation Stripe
POST /api/payments/connect/dashboard/     → lien tableau de bord Stripe
POST /api/stripe/webhook/                 → Stripe (signature vérifiée)
GET  /api/payments/return/                → page de retour vers l'application
═══════════════════════════════════════════════════════════════
"""
import logging

from django.db.models import Count, Q
from django.core import signing
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.throttling import UserRateThrottle
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from events.models import Event
from .models import Ticket
from .serializers import TicketSerializer
from . import services, stripe_service
from .services import TicketError
from adminpanel.keys import get_key

logger = logging.getLogger(__name__)


def _error(exc):
    return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)


def _own_ticket(request, ticket_id):
    # Un utilisateur ne voit que SES tickets (OWASP API1 — BOLA)
    return get_object_or_404(Ticket.objects.select_related('event', 'event__organizer', 'user', 'purchased_by', 'gift'),
                             pk=ticket_id, user=request.user)


def _out(request, ticket, code=status.HTTP_200_OK):
    return Response(TicketSerializer(ticket, context={'request': request}).data, status=code)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_tickets(request):
    qs = Ticket.objects.filter(user=request.user)
    services.expire_old_tickets(qs)
    wanted = request.query_params.get('status')
    if wanted == 'pending':
        qs = qs.filter(status=Ticket.Status.PENDING)
    elif wanted == 'generated':
        qs = qs.filter(status=Ticket.Status.GENERATED)
    elif wanted == 'archived':
        qs = qs.filter(status__in=[Ticket.Status.CANCELLED, Ticket.Status.EXPIRED])
    qs = qs.select_related('event', 'event__organizer', 'user', 'purchased_by', 'gift').order_by('-created_at')[:100]
    return Response({'tickets': TicketSerializer(qs, many=True, context={'request': request}).data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ticket_counts(request):
    from invitations.models import Invitation
    from django.utils import timezone
    counts = Ticket.objects.filter(user=request.user).aggregate(
        pending=Count('id', filter=Q(status='pending')),
        generated=Count('id', filter=Q(status='generated')),
        archived=Count('id', filter=Q(status__in=['cancelled', 'expired'])),
    )
    counts['invitations_to_answer'] = Invitation.objects.filter(
        invited_user=request.user, status__in=['sent', 'opened'], expires_at__gt=timezone.now(),
        event__deleted_at__isnull=True, event__status='published',
    ).count()
    counts['badge'] = counts['pending'] + counts['invitations_to_answer']
    return Response(counts)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def take_ticket(request, event_id):
    event = get_object_or_404(Event, pk=event_id, deleted_at__isnull=True)
    # Questions RSVP (M19) : seulement pour un nouveau ticket, et si l'événement lui est accessible
    from rsvp.services import RsvpError, can_answer, check_required
    if can_answer(event, request.user) and not services.active_ticket(event, request.user):
        try:
            check_required(event, request.user, request.data.get('rsvp_answers'))
        except RsvpError as exc:
            return Response(exc.payload(), status=exc.status)
    try:
        ticket, created = services.create_pending_ticket(event, request.user)
        # Gratuit : validation directe (M24 « Participer — ticket gratuit »)
        if ticket.is_free and ticket.status == Ticket.Status.PENDING:
            ticket = services.validate_free_ticket(ticket)
    except TicketError as exc:
        return _error(exc)
    return _out(request, ticket, status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ticket_detail(request, ticket_id):
    return _out(request, _own_ticket(request, ticket_id))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_ticket(request, ticket_id):
    try:
        ticket = services.validate_free_ticket(_own_ticket(request, ticket_id))
    except TicketError as exc:
        return _error(exc)
    return _out(request, ticket)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def checkout_ticket(request, ticket_id):
    try:
        url = stripe_service.create_checkout(_own_ticket(request, ticket_id), request)
    except TicketError as exc:
        return _error(exc)
    except Exception:
        logger.exception('Création de session Stripe impossible')
        return Response({'detail': 'Le paiement est momentanément indisponible. Réessayez.',
                         'code': 'stripe_error'}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({'checkout_url': url})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_ticket(request, ticket_id):
    try:
        ticket = services.cancel_ticket(_own_ticket(request, ticket_id))
    except TicketError as exc:
        return _error(exc)
    return _out(request, ticket)


# ─────────────────────────────────────────────────────────────
# Organisateur : Stripe Connect
# ─────────────────────────────────────────────────────────────
def _connect_payload(user):
    return {
        'connected':        bool(user.stripe_account_id),
        'charges_enabled':  user.stripe_charges_enabled,
        'payouts_enabled':  user.stripe_payouts_enabled,
        'payments_available': bool(get_key('STRIPE_SECRET_KEY')),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def connect_status(request):
    user = request.user
    if user.stripe_account_id and not user.stripe_charges_enabled:
        try:
            stripe_service.sync_account(user)
        except Exception:
            logger.exception('Lecture du compte Stripe impossible')
    return Response(_connect_payload(user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def connect_onboard(request):
    try:
        url = stripe_service.onboarding_link(request.user, request)
    except TicketError as exc:
        return _error(exc)
    except Exception:
        logger.exception('Lien Stripe Connect impossible')
        return Response({'detail': "L'activation des paiements est momentanément indisponible.",
                         'code': 'stripe_error'}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({'url': url})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def connect_dashboard(request):
    try:
        url = stripe_service.dashboard_link(request.user)
    except TicketError as exc:
        return _error(exc)
    except Exception:
        logger.exception('Lien tableau de bord Stripe impossible')
        return Response({'detail': 'Tableau de bord momentanément indisponible.', 'code': 'stripe_error'},
                        status=status.HTTP_502_BAD_GATEWAY)
    return Response({'url': url})


# ─────────────────────────────────────────────────────────────
# Stripe → Easevent
# ─────────────────────────────────────────────────────────────
@csrf_exempt
@require_POST
def stripe_webhook(request):
    try:
        event = stripe_service.parse_webhook(request.body, request.META.get('HTTP_STRIPE_SIGNATURE', ''))
    except stripe_service.PaymentsUnavailable:
        return HttpResponse(status=503)
    except Exception as exc:
        # Signature invalide ou charge illisible : requête rejetée
        logger.warning('Webhook Stripe rejeté : %s', type(exc).__name__)
        return HttpResponse(status=400)
    try:
        stripe_service.handle_event(event)
    except TicketError as exc:
        logger.warning('Webhook Stripe : %s', exc.message)
    except Exception:
        # Stripe renverra l'événement plus tard (nouvelle tentative automatique)
        logger.exception('Webhook Stripe non traité : %s', event.get('type'))
        return HttpResponse(status=500)
    return HttpResponse(status=200)


# ─────────────────────────────────────────────────────────────
# Mobile Money (Orange Money, MTN MoMo) via Notch Pay
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_methods(request):
    from . import mobile_money
    return Response({'card': bool(get_key('STRIPE_SECRET_KEY')), 'mobile_money': mobile_money.available()})


class MobileMoneyThrottle(UserRateThrottle):
    scope = 'billing'


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([MobileMoneyThrottle])
def mobile_money_checkout(request, ticket_id):
    from . import mobile_money
    data = request.data if isinstance(request.data, dict) else {}
    try:
        return Response(mobile_money.create_payment(_own_ticket(request, ticket_id), request, data.get('phone', '')))
    except TicketError as exc:
        return _error(exc)


@csrf_exempt
@require_POST
def mobile_money_webhook(request):
    from . import mobile_money
    if not mobile_money.verify_signature(request.body, request.META.get('HTTP_X_NOTCH_SIGNATURE', '')):
        logger.warning('Webhook Notch Pay rejeté : signature invalide')
        return HttpResponse(status=400)
    try:
        mobile_money.sync(mobile_money.reference_from_webhook(mobile_money.parse(request.body)))
    except ValueError:
        return HttpResponse(status=400)
    except Exception:
        return HttpResponse(status=500)              # Notch Pay renverra l'événement
    return HttpResponse(status=200)


def mobile_money_return(request):
    """Retour de la page Notch Pay : on vérifie l'état (sans attendre le webhook), puis on renvoie vers l'application."""
    from . import mobile_money
    ticket = None
    try:
        ticket = mobile_money.sync(str(request.GET.get('reference', ''))[:64])
    except Exception:
        pass
    from .models import TicketGift
    from baskets.models import Contribution
    if isinstance(ticket, Contribution):
        done = ticket.status == Contribution.Status.PAID
        title = 'Merci !' if done else ('Paiement non abouti' if ticket.status == 'failed' else 'Paiement en cours')
        message = ('Votre participation est dans le panier. Retournez dans Easevent.' if done else
                   'Validez le paiement sur votre téléphone, puis retournez dans Easevent.')
        return render(request, 'tickets/return.html', {'title': title, 'message': message, 'deeplink': 'easevent://invitations'})
    if isinstance(ticket, TicketGift):
        done = ticket.status in (TicketGift.Status.DELIVERED, TicketGift.Status.PAID)
        title = 'Cadeau réglé' if done else ('Paiement non abouti' if ticket.payment_status == 'failed' else 'Paiement en cours')
        message = ('Merci ! Retournez dans Easevent : votre proche est prévenu.' if done else
                   'Validez le paiement sur votre téléphone, puis retournez dans Easevent.')
        return render(request, 'tickets/return.html', {'title': title, 'message': message, 'deeplink': 'easevent://invitations'})
    if ticket is not None and ticket.status == Ticket.Status.GENERATED:
        title, message = 'Paiement reçu', 'Votre billet est prêt dans « Mes invitations ».'
    elif ticket is not None and ticket.payment_status == Ticket.PaymentStatus.FAILED:
        title, message = 'Paiement non abouti', 'Votre billet reste en attente : vous pouvez réessayer depuis l’application.'
    else:
        title, message = 'Paiement en cours', 'Validez le paiement sur votre téléphone : votre billet apparaît dès la confirmation.'
    return render(request, 'tickets/return.html', {'title': title, 'message': message, 'deeplink': 'easevent://invitations'})


# ─────────────────────────────────────────────────────────────
# Billets offerts (« Payer pour un proche »)
# ─────────────────────────────────────────────────────────────
class GiftThrottle(UserRateThrottle):
    scope = 'gifts'


def _own_gift(request, gift_id):
    from .models import TicketGift
    return get_object_or_404(TicketGift.objects.select_related('event', 'event__organizer', 'buyer', 'recipient'),
                             pk=gift_id, buyer=request.user)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([GiftThrottle])
def event_gifts(request, event_id):
    from . import gifts
    if request.method == 'GET':                      # mes cadeaux pour cet événement
        from .models import TicketGift
        rows = TicketGift.objects.select_related('event', 'recipient').filter(event_id=event_id, buyer=request.user)
        return Response({'results': [gifts.payload(g) for g in rows[:50]]})
    event = get_object_or_404(Event.objects.select_related('organizer'), pk=event_id, deleted_at__isnull=True)
    try:
        gift = gifts.create_gift(request.user, event, request.data if isinstance(request.data, dict) else {})
    except TicketError as exc:
        return _error(exc)
    return Response(gifts.payload(gift), status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gift_detail(request, gift_id):
    from . import gifts
    return Response(gifts.payload(_own_gift(request, gift_id)))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([MobileMoneyThrottle])
def gift_checkout(request, gift_id):
    gift = _own_gift(request, gift_id)
    try:
        return Response({'checkout_url': stripe_service.create_gift_checkout(gift, request)})
    except TicketError as exc:
        return _error(exc)
    except Exception:
        logger.exception('Session Stripe (cadeau) impossible')
        return Response({'detail': 'Le paiement est momentanément indisponible. Réessayez.', 'code': 'stripe_error'},
                        status=status.HTTP_502_BAD_GATEWAY)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([MobileMoneyThrottle])
def gift_mobile_money(request, gift_id):
    from . import mobile_money
    data = request.data if isinstance(request.data, dict) else {}
    try:
        return Response(mobile_money.create_gift_payment(_own_gift(request, gift_id), request, data.get('phone', '')))
    except TicketError as exc:
        return _error(exc)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def gift_cancel(request, gift_id):
    from . import gifts
    try:
        return Response(gifts.payload(gifts.cancel(_own_gift(request, gift_id))))
    except TicketError as exc:
        return _error(exc)


def payment_return(request):
    """Page affichée après Stripe (paiement ou activation) : renvoie vers l'application."""
    flow = request.GET.get('flow', 'ticket')
    state = request.GET.get('status', '')
    pages = {
        ('ticket', 'success'):  ('Paiement envoyé', 'Votre billet apparaît dans « Mes invitations » dès que le paiement est confirmé.', 'easevent://invitations'),
        ('gift', 'success'):    ('Cadeau réglé', 'Merci ! Votre proche est prévenu dès la confirmation du paiement.', 'easevent://invitations'),
        ('basket', 'success'):  ('Merci !', 'Votre participation apparaît dans le panier dès la confirmation du paiement.', 'easevent://invitations'),
        ('basket', 'cancel'):   ('Paiement interrompu', 'Aucun montant n’a été prélevé. Vous pouvez réessayer depuis le panier.', 'easevent://invitations'),
        ('gift', 'cancel'):     ('Paiement interrompu', 'Aucun montant n’a été prélevé. Vous pouvez reprendre le cadeau depuis l’événement.', 'easevent://decouvrir'),
        ('ticket', 'cancel'):   ('Paiement interrompu', 'Votre billet reste dans « Mes invitations › En attente ». Vous pourrez payer plus tard.', 'easevent://invitations'),
        ('connect', 'done'):    ('Informations enregistrées', 'Retournez dans Easevent pour voir l’état de vos paiements.', 'easevent://profil/paiements'),
        ('connect', 'refresh'): ('Lien expiré', 'Relancez l’activation des paiements depuis votre profil Easevent.', 'easevent://profil/paiements'),
        ('subscription', 'success'): ('Abonnement activé', 'Merci ! Retournez dans Easevent : vos nouvelles fonctionnalités sont prêtes.', 'easevent://profil/abonnement/succes'),
        ('subscription', 'cancel'):  ('Paiement interrompu', 'Aucun montant n’a été prélevé. Vous pouvez choisir un plan à tout moment.', 'easevent://profil/plans'),
        ('subscription', 'portal'):  ('Modifications enregistrées', 'Retournez dans Easevent pour voir votre abonnement.', 'easevent://profil/plans'),
    }
    title, message, deeplink = pages.get((flow, state), ('Easevent', 'Retournez dans l’application.', 'easevent://'))
    return render(request, 'tickets/return.html', {'title': title, 'message': message, 'deeplink': deeplink})


# ─────────────────────────────────────────────────────────────
# PDF du ticket (bouton « Télécharger » de M27)
# Lien signé et valable 5 minutes : l'application l'ouvre dans le
# navigateur du téléphone, qui télécharge ou affiche le PDF.
# ─────────────────────────────────────────────────────────────
PDF_SALT = 'easevent.ticket.pdf'
PDF_LINK_TTL = 300


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ticket_pdf_link(request, ticket_id):
    ticket = _own_ticket(request, ticket_id)
    if ticket.status != Ticket.Status.GENERATED:
        return Response({'detail': 'Le PDF est disponible une fois le ticket généré.', 'code': 'not_generated'},
                        status=status.HTTP_400_BAD_REQUEST)
    token = signing.dumps({'t': str(ticket.id), 'u': str(request.user.id)}, salt=PDF_SALT, compress=True)
    url = stripe_service._url(request, f'/api/tickets/pdf/{token}/')
    return Response({'url': url, 'expires_in': PDF_LINK_TTL})


def ticket_pdf(request, token):
    from .pdf import render_ticket_pdf
    try:
        data = signing.loads(token, salt=PDF_SALT, max_age=PDF_LINK_TTL)
    except signing.BadSignature:
        raise Http404('Lien expiré')
    ticket = Ticket.objects.select_related('event', 'user').filter(
        pk=data.get('t'), user_id=data.get('u'), status=Ticket.Status.GENERATED).first()
    if ticket is None:
        raise Http404('Ticket introuvable')
    response = HttpResponse(render_ticket_pdf(ticket), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="ticket-{ticket.number}.pdf"'
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
