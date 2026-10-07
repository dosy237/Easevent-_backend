"""
subscriptions/views.py — API des abonnements (M20 / M21 / M22)
  GET  /api/subscriptions/            catalogue des plans + mon abonnement
  POST /api/subscriptions/checkout/   { plan, interval }  → { checkout_url } ou { changed }
  POST /api/subscriptions/cancel/     résilier à la fin de la période
  POST /api/subscriptions/resume/     annuler la résiliation
  POST /api/subscriptions/portal/     → { url } factures et carte bancaire (Stripe)
"""
import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from . import services

logger = logging.getLogger(__name__)


class BillingThrottle(UserRateThrottle):
    scope = 'billing'


def _error(exc):
    return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)


def _stripe_down():
    return Response({'detail': 'Le paiement est momentanément indisponible. Réessayez.', 'code': 'stripe_error'},
                    status=status.HTTP_502_BAD_GATEWAY)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def overview(request):
    from events.quota import usage
    return Response({'plans': services.catalog(), 'subscription': services.state(request.user),
                     'event_quota': usage(request.user)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BillingThrottle])
def checkout(request):
    data = request.data if isinstance(request.data, dict) else {}
    try:
        return Response(services.start(request.user, data.get('plan'), data.get('interval', 'monthly')))
    except services.SubscriptionError as exc:
        return _error(exc)
    except Exception:
        logger.exception('Abonnement : session Stripe impossible')
        return _stripe_down()


def _toggle(request, cancel):
    try:
        return Response({'subscription': services.set_cancel(request.user, cancel)})
    except services.SubscriptionError as exc:
        return _error(exc)
    except Exception:
        logger.exception('Abonnement : modification Stripe impossible')
        return _stripe_down()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BillingThrottle])
def cancel(request):
    return _toggle(request, True)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BillingThrottle])
def resume(request):
    return _toggle(request, False)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BillingThrottle])
def portal(request):
    try:
        return Response({'url': services.portal(request.user)})
    except services.SubscriptionError as exc:
        return _error(exc)
    except Exception:
        logger.exception('Abonnement : portail Stripe impossible')
        return _stripe_down()
