"""
tickets/checkin.py — contrôle des tickets à l'entrée (scanner de l'organisateur)

  GET  /api/events/<id>/check-in/            compteurs + 20 dernières entrées
  POST /api/events/<id>/check-in/  { code }  code = contenu du QR (signé) ou n° de ticket (EV-XXXXXXXX)

Réponse : result = ok | already | invalid | wrong_event | not_valid, avec le
nom du participant pour que l'organisateur vérifie d'un coup d'œil.
Un ticket ne peut entrer qu'une fois ; l'entrée est verrouillée en base
(deux scanners en même temps ne valident pas deux fois le même ticket).
"""
from events.wording import pass_word
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from events.models import Event

from .models import Ticket


class CheckInThrottle(UserRateThrottle):
    scope = 'checkin'


def _counts(event):
    qs = Ticket.objects.filter(event=event, status=Ticket.Status.GENERATED)
    return {'checked_in': qs.filter(checked_in_at__isnull=False).count(), 'total': qs.count()}


def _person(ticket):
    return {'name': ticket.user.full_name, 'number': ticket.number,
            'checked_in_at': ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
            'dress_code': ticket.dress_code}


def _find(event, code):
    code = str(code or '').strip()
    if not code or len(code) > 500:
        return None
    ticket_id = Ticket.read_qr_payload(code)
    if ticket_id:
        return Ticket.objects.select_related('user').filter(pk=ticket_id).first()
    number = code.upper().replace(' ', '')
    if not number.startswith('EV-') and len(number) == 8:
        number = f'EV-{number}'
    return Ticket.objects.select_related('user').filter(number=number, event=event).first()


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([CheckInThrottle])
def check_in(request, event_id):
    from events.team import managed_event
    event = managed_event(request.user, event_id)
    if event is None:
        raise Http404
    if request.method == 'GET':
        recent = (Ticket.objects.select_related('user').filter(event=event, checked_in_at__isnull=False)
                  .order_by('-checked_in_at')[:20])
        return Response({'counts': _counts(event), 'recent': [_person(t) for t in recent]})

    data = request.data if isinstance(request.data, dict) else {}
    ticket = _find(event, data.get('code'))
    if ticket is None:
        return Response({'result': 'invalid', 'detail': 'Code inconnu : ce QR code n’est pas une invitation ni un billet Easevent.',
                         'counts': _counts(event)})
    if ticket.event_id != event.id:
        return Response({'result': 'wrong_event', 'detail': 'Ce code est pour un autre événement.',
                         'participant': _person(ticket), 'counts': _counts(event)})
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().select_related('user').get(pk=ticket.pk)
        if ticket.status != Ticket.Status.GENERATED:
            w = pass_word(event)
            label = {'pending': f"pas encore validé{w['e']} (paiement non finalisé)", 'cancelled': f"annulé{w['e']}",
                     'expired': f"expiré{w['e']}"}.get(ticket.status, ticket.status)
            return Response({'result': 'not_valid', 'detail': f"{w['One']} {label}.",
                             'participant': _person(ticket), 'counts': _counts(event)})
        if ticket.checked_in_at:
            return Response({'result': 'already', 'detail': 'Déjà entré.',
                             'participant': _person(ticket), 'counts': _counts(event)})
        ticket.checked_in_at = timezone.now()
        ticket.save(update_fields=['checked_in_at', 'updated_at'])
    return Response({'result': 'ok', 'detail': 'Entrée validée.', 'participant': _person(ticket), 'counts': _counts(event)})
