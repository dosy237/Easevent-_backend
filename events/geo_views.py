"""
events/geo_views.py — adresses et cartes
  GET /api/geo/search/?q=&lat=&lng=&session=   suggestions d'adresse (connecté)
  GET /api/geo/place/<id>/?session=            coordonnées d'une suggestion Google
  GET /api/geo/static-map/?lat=&lng=           image PNG de la carte (publique, mise en cache)
"""
from django.http import Http404, HttpResponse
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from invitations.throttles import UserSearchThrottle

from . import geo


class MapThrottle(SimpleRateThrottle):
    scope = 'static_map'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


def _coord(value, limit):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if -limit <= v <= limit else None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserSearchThrottle])
def search(request):
    p = request.query_params
    return Response({
        'provider': geo.provider(),
        'results': geo.search(p.get('q'), _coord(p.get('lat'), 90), _coord(p.get('lng'), 180), p.get('session', '')),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserSearchThrottle])
def place(request, place_id):
    try:
        details = geo.place_details(place_id, request.query_params.get('session', ''))
    except Exception:
        details = None
    if not details:
        raise Http404
    return Response(details)


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([MapThrottle])
def static_map(request):
    lat, lng = _coord(request.query_params.get('lat'), 85), _coord(request.query_params.get('lng'), 180)
    if lat is None or lng is None:
        raise Http404
    png = geo.static_map_png(round(lat, 5), round(lng, 5))
    if not png:
        return HttpResponse(status=503)
    response = HttpResponse(png, content_type='image/png')
    response['Cache-Control'] = 'public, max-age=2592000, immutable'
    return response
