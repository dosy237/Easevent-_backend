"""
events/insights.py — statistiques et finances d'un événement

  GET  /api/events/<id>/stats/        organisateur et co-organisateurs
  POST /api/events/<id>/attendance/   { count } : présence réelle, après le début
  GET  /api/events/<id>/finance/      organisateur (événement payant) : « combien je gagne »
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Count, Q
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .team import managed_event, role_of

MAX_ATTENDANCE = 1_000_000


def _event(request, event_id, organizer_only=False):
    event = managed_event(request.user, event_id, organizer_only=organizer_only)
    if event is None:
        raise Http404
    return event


def phase(event, now=None):
    now = now or timezone.now()
    if event.start_date and now < event.start_date:
        return 'upcoming'
    if event.end_date and now <= event.end_date:
        return 'live'
    return 'past'


def stats(event):
    from tickets.models import Ticket

    inv = event.invitations.exclude(status='revoked').aggregate(
        total=Count('id'),
        confirmed=Count('id', filter=Q(status='confirmed')),
        declined=Count('id', filter=Q(status='declined')),
        opened=Count('id', filter=Q(status__in=('opened', 'confirmed', 'declined'))),
    )
    tickets = Ticket.objects.filter(event=event).aggregate(
        generated=Count('id', filter=Q(status=Ticket.Status.GENERATED)),
        checked_in=Count('id', filter=Q(status=Ticket.Status.GENERATED, checked_in_at__isnull=False)),
    )
    expected = tickets['generated']
    attendance = event.attendance_count
    return {
        'phase': phase(event),
        'views': event.view_count,
        'likes': event.likes.count(),
        'invited': inv['total'],
        'opened': inv['opened'],
        'accepted': inv['confirmed'],
        'declined': inv['declined'],
        'pending': max(0, inv['total'] - inv['confirmed'] - inv['declined']),
        'participants': expected,                       # invitations ou billets validés
        'checked_in': tickets['checked_in'],            # scannés à l'entrée
        'attendance': attendance,                       # saisi par l'organisateur
        'attendance_reported_at': event.attendance_reported_at.isoformat() if event.attendance_reported_at else None,
        'attendance_rate': round(attendance * 100 / expected) if attendance is not None and expected else None,
        'comments': event.comments.count(),
        'photos': event.media.filter(is_approved=True).count(),
        'max_guests': event.max_guests,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def event_stats(request, event_id):
    event = _event(request, event_id)
    return Response({'role': role_of(event, request.user), 'title': event.title, **stats(event)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def attendance(request, event_id):
    """Le nombre de personnes réellement venues (modifiable ; vide pour effacer)."""
    event = _event(request, event_id)
    if phase(event) == 'upcoming':
        return Response({'detail': "Vous pourrez indiquer la présence réelle une fois l'événement commencé.",
                         'code': 'too_early'}, status=status.HTTP_409_CONFLICT)
    raw = (request.data or {}).get('count') if isinstance(request.data, dict) else None
    if raw in (None, ''):
        event.attendance_count, event.attendance_reported_at = None, None
    else:
        try:
            count = int(str(raw).strip())
        except (TypeError, ValueError):
            return Response({'detail': 'Indiquez un nombre.', 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
        if count < 0 or count > MAX_ATTENDANCE:
            return Response({'detail': 'Nombre invalide.', 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
        event.attendance_count, event.attendance_reported_at = count, timezone.now()
    event.save(update_fields=['attendance_count', 'attendance_reported_at', 'updated_at'])
    return Response(stats(event))


# ─────────────────────────────────────────────────────────────
# Finances
# ─────────────────────────────────────────────────────────────
def _money(value):
    return float(Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def finance(event):
    from tickets.models import Ticket

    pct = Decimal(str(settings.PLATFORM_FEE_PERCENT or 0))
    paid_states = (Ticket.PaymentStatus.PAID,)
    tickets = list(Ticket.objects.filter(event=event, payment_status__in=paid_states + (Ticket.PaymentStatus.REFUNDED,))
                   .only('price', 'currency', 'payment_status', 'status', 'mobile_money_reference',
                         'purchased_by_id', 'user_id', 'created_at', 'generated_at'))
    currency = event.currency or 'EUR'
    gross = refunded = Decimal(0)
    sold = refunds = card = mobile = gifts = 0
    by_price = {}
    by_day = {}
    for t in tickets:
        price = Decimal(t.price or 0)
        if t.payment_status == Ticket.PaymentStatus.REFUNDED:
            refunds += 1
            refunded += price
            continue
        if t.status == Ticket.Status.CANCELLED:
            continue
        sold += 1
        gross += price
        if t.mobile_money_reference:
            mobile += 1
        else:
            card += 1
        if t.purchased_by_id and t.purchased_by_id != t.user_id:
            gifts += 1
        key = str(price.quantize(Decimal('0.01')))
        by_price[key] = by_price.get(key, 0) + 1
        day = (t.generated_at or t.created_at).date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
    fee = (gross * pct / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    capacity = event.max_guests
    organizer = event.organizer
    return {
        'is_paid': bool(event.is_paid),
        'currency': currency,
        'price': _money(event.price or 0),
        'tickets_sold': sold,
        'capacity': capacity,
        'fill_rate': round(sold * 100 / capacity) if capacity else None,
        'gross': _money(gross),
        'fee_percent': float(pct),
        'fee': _money(fee),
        'net': _money(gross - fee),
        'refunds': refunds,
        'refunded': _money(refunded),
        'by_method': {'card': card, 'mobile_money': mobile},
        'gifts': gifts,
        'by_price': [{'price': float(k), 'count': v} for k, v in sorted(by_price.items(), key=lambda kv: Decimal(kv[0]))],
        'by_day': [{'day': k, 'count': v} for k, v in sorted(by_day.items())],
        'payouts': {
            # Carte : Stripe vire automatiquement sur le compte bancaire de l'organisateur
            'stripe_connected': bool(organizer.stripe_account_id),
            'stripe_payouts_enabled': bool(organizer.stripe_payouts_enabled),
            # Mobile Money : encaissé par Easevent puis reversé (commission déduite)
            'mobile_money_due': mobile > 0,
        },
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def event_finance(request, event_id):
    event = _event(request, event_id, organizer_only=True)
    return Response({'title': event.title, **finance(event)})
