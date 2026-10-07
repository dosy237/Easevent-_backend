"""
baskets/views.py — API du panier

  GET  /api/events/<id>/basket/                      panier ouvert (ou le dernier) + bilan
  POST /api/events/<id>/basket/                      lancer { title, description?, goal_amount?, allow_items?, allow_money? }
  POST /api/baskets/<id>/close/                      fermer (organisateurs)
  POST /api/baskets/<id>/items/                      { label, quantity?, message? }
  POST /api/baskets/<id>/money/                      { amount, message?, anonymous? } → participation à payer
  POST /api/baskets/contributions/<id>/checkout/     carte → { checkout_url }
  POST /api/baskets/contributions/<id>/mobile-money/ { phone? } → { url }
  GET  /api/baskets/contributions/<id>/              état d'une participation
  DELETE /api/baskets/contributions/<id>/            retirer (objet) / annuler (argent non payé)
"""
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from events.models import Event

from . import payments, services
from .models import Basket, Contribution
from .services import BasketError


class BasketThrottle(UserRateThrottle):
    scope = 'baskets'


def _error(exc):
    return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)


def _data(request):
    return request.data if isinstance(request.data, dict) else {}


def _basket(request, basket_id):
    b = Basket.objects.select_related('event', 'event__organizer', 'created_by').filter(
        pk=basket_id, event__deleted_at__isnull=True).first()
    if b is None or not services.can_take_part(b.event, request.user):
        raise Http404
    return b


def _contribution(request, cid):
    c = Contribution.objects.select_related('basket', 'basket__event', 'basket__event__organizer', 'user').filter(
        pk=cid).first()
    if c is None:
        raise Http404
    return c


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BasketThrottle])
def event_basket(request, event_id):
    event = Event.objects.select_related('organizer').filter(pk=event_id, deleted_at__isnull=True).first()
    if event is None or not services.can_take_part(event, request.user):
        raise Http404
    if request.method == 'GET':
        basket = services.open_basket(event) or event.baskets.first()
        from events.team import is_manager
        return Response({'basket': services.summary(basket, request.user) if basket else None,
                         'can_launch': is_manager(event, request.user) and services.open_basket(event) is None})
    try:
        basket, result = services.launch(event, request.user, _data(request))
    except BasketError as exc:
        return _error(exc)
    return Response({'basket': services.summary(basket, request.user), 'announced_to': result['recipients'],
                     'without_account': result['without_account']}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def close_basket(request, basket_id):
    basket = _basket(request, basket_id)
    try:
        services.close(basket, request.user)
    except BasketError as exc:
        return _error(exc)
    return Response(services.summary(basket, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BasketThrottle])
def add_item(request, basket_id):
    basket = _basket(request, basket_id)
    try:
        services.add_item(basket, request.user, _data(request))
    except BasketError as exc:
        return _error(exc)
    return Response(services.summary(basket, request.user), status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BasketThrottle])
def add_money(request, basket_id):
    basket = _basket(request, basket_id)
    try:
        c = services.add_money(basket, request.user, _data(request))
    except BasketError as exc:
        return _error(exc)
    return Response({'id': str(c.id), 'amount_text': services.format_amount(c.amount, c.currency),
                     'status': c.status}, status=status.HTTP_201_CREATED)


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def contribution(request, cid):
    c = _contribution(request, cid)
    if request.method == 'GET':
        if c.user_id != request.user.id:
            raise Http404
        return Response({'id': str(c.id), 'status': c.status, 'amount_text': services.format_amount(c.amount, c.currency),
                         'basket_id': str(c.basket_id)})
    try:
        services.remove(c, request.user)
    except BasketError as exc:
        return _error(exc)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BasketThrottle])
def checkout(request, cid):
    c = _contribution(request, cid)
    if c.user_id != request.user.id:
        raise Http404
    try:
        return Response({'checkout_url': payments.card_checkout(c, request)})
    except BasketError as exc:
        return _error(exc)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Session Stripe (panier) impossible')
        return Response({'detail': 'Le paiement est momentanément indisponible. Réessayez.', 'code': 'stripe_error'},
                        status=status.HTTP_502_BAD_GATEWAY)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BasketThrottle])
def mobile_money(request, cid):
    c = _contribution(request, cid)
    if c.user_id != request.user.id:
        raise Http404
    try:
        return Response(payments.mobile_money(c, request, _data(request).get('phone', '')))
    except BasketError as exc:
        return _error(exc)
