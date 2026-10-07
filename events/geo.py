"""
events/geo.py
═══════════════════════════════════════════════════════════════
Adresses et cartes des événements.

- Recherche d'adresse avec suggestions (création d'événement) :
  Google Places si GOOGLE_MAPS_API_KEY est configurée, sinon Photon
  (OpenStreetMap, gratuit, sans clé, fait pour l'autocomplétion).
- Image de carte du lieu (détail, ticket, messagerie) :
  Google Static Maps si clé, sinon tuiles OpenStreetMap assemblées
  par le serveur et mises en cache sur disque (une image par lieu).
- L'itinéraire s'ouvre dans Google Maps (lien universel, gratuit) :
  départ = position du téléphone, arrivée = le lieu.
La clé Google reste sur le serveur, jamais dans l'application.
═══════════════════════════════════════════════════════════════
"""
import hashlib
import io
import logging
import math
from pathlib import Path
from urllib.parse import quote

import requests
from django.conf import settings

from adminpanel.keys import get_key
from django.core.cache import cache

logger = logging.getLogger(__name__)

USER_AGENT = 'Easevent/1.0 (eranistechnology@gmail.com)'
TIMEOUT = 5
TILE = 256
MAP_W, MAP_H, ZOOM = 640, 320, 16
GREEN = (27, 107, 74)


def provider():
    return 'google' if get_key('GOOGLE_MAPS_API_KEY') else 'osm'


# ─────────────────────────────────────────────────────────────
# Liens Google Maps (aucune clé nécessaire)
# ─────────────────────────────────────────────────────────────
def maps_links(address, lat=None, lng=None):
    dest = f'{lat},{lng}' if lat is not None and lng is not None else address
    return {
        'view': f'https://www.google.com/maps/search/?api=1&query={quote(str(dest))}',
        'directions': f'https://www.google.com/maps/dir/?api=1&destination={quote(str(dest))}',
    }


# ─────────────────────────────────────────────────────────────
# Recherche d'adresse
# ─────────────────────────────────────────────────────────────
def search(q, lat=None, lng=None, session=''):
    q = ' '.join(str(q or '').split())[:120]
    if len(q) < 3:
        return []
    key = 'geo:' + hashlib.sha256(f'{provider()}|{q.lower()}|{lat}|{lng}'.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        results = _google_search(q, lat, lng, session) if provider() == 'google' else _photon_search(q, lat, lng)
    except requests.RequestException as exc:
        logger.warning('Recherche d’adresse indisponible : %s', type(exc).__name__)
        return []
    cache.set(key, results, 60 * 60 * 24)
    return results


def _photon_search(q, lat, lng):
    params = {'q': q, 'limit': 6, 'lang': 'fr'}
    if lat is not None and lng is not None:
        params.update(lat=lat, lon=lng)
    resp = requests.get('https://photon.komoot.io/api/', params=params, timeout=TIMEOUT,
                        headers={'User-Agent': USER_AGENT})
    resp.raise_for_status()
    out = []
    for f in resp.json().get('features', []):
        p = f.get('properties', {})
        lon_, lat_ = (f.get('geometry') or {}).get('coordinates', [None, None])[:2]
        street = ' '.join(x for x in (p.get('housenumber'), p.get('street')) if x)
        name = p.get('name') or street or p.get('city') or ''
        city = ' '.join(x for x in (p.get('postcode'), p.get('city') or p.get('county')) if x)
        secondary = ', '.join(x for x in (street if street != name else '', city, p.get('country')) if x)
        address = ', '.join(x for x in (name, secondary) if x)
        if not name or lat_ is None:
            continue
        out.append({'id': f"osm:{p.get('osm_type', '')}{p.get('osm_id', '')}", 'label': name,
                    'secondary': secondary, 'address': address, 'lat': round(lat_, 6), 'lng': round(lon_, 6)})
    return out


def _google_search(q, lat, lng, session):
    body = {'input': q, 'languageCode': 'fr'}
    if session:
        body['sessionToken'] = session[:64]
    if lat is not None and lng is not None:
        body['locationBias'] = {'circle': {'center': {'latitude': lat, 'longitude': lng}, 'radius': 50000.0}}
    resp = requests.post('https://places.googleapis.com/v1/places:autocomplete', json=body, timeout=TIMEOUT,
                         headers={'X-Goog-Api-Key': get_key('GOOGLE_MAPS_API_KEY')})
    resp.raise_for_status()
    out = []
    for s in resp.json().get('suggestions', []):
        pred = s.get('placePrediction')
        if not pred:
            continue
        fmt = pred.get('structuredFormat', {})
        out.append({'id': f"google:{pred.get('placeId')}", 'label': fmt.get('mainText', {}).get('text') or pred.get('text', {}).get('text', ''),
                    'secondary': fmt.get('secondaryText', {}).get('text', ''), 'address': pred.get('text', {}).get('text', ''),
                    'lat': None, 'lng': None})
    return out


def place_details(place_id, session=''):
    """Coordonnées d'une suggestion Google (Photon les donne directement)."""
    if not place_id.startswith('google:') or provider() != 'google':
        return None
    pid = place_id.split(':', 1)[1]
    if not pid.replace('-', '').replace('_', '').isalnum():
        return None
    resp = requests.get(f'https://places.googleapis.com/v1/places/{pid}', timeout=TIMEOUT,
                        params={'sessionToken': session[:64]} if session else None,
                        headers={'X-Goog-Api-Key': get_key('GOOGLE_MAPS_API_KEY'),
                                 'X-Goog-FieldMask': 'location,formattedAddress,displayName'})
    resp.raise_for_status()
    d = resp.json()
    loc = d.get('location') or {}
    return {'address': d.get('formattedAddress', ''), 'label': (d.get('displayName') or {}).get('text', ''),
            'lat': loc.get('latitude'), 'lng': loc.get('longitude')}


# ─────────────────────────────────────────────────────────────
# Image de carte
# ─────────────────────────────────────────────────────────────
def static_map_url(lat, lng, request=None):
    from easevent.media import absolute_url
    if lat is None or lng is None:
        return None
    return absolute_url(f'/api/geo/static-map/?lat={float(lat):.5f}&lng={float(lng):.5f}', request)


def _cache_path(lat, lng):
    name = hashlib.sha256(f'{provider()}|{lat:.5f}|{lng:.5f}|{ZOOM}'.encode()).hexdigest()[:32]
    folder = Path(settings.MEDIA_ROOT) / 'maps'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{name}.png'


def static_map_png(lat, lng):
    """PNG de la carte (avec repère). Retourne des octets, ou None si indisponible."""
    if provider() == 'google':
        try:
            resp = requests.get('https://maps.googleapis.com/maps/api/staticmap', timeout=TIMEOUT, params={
                'center': f'{lat},{lng}', 'zoom': ZOOM, 'size': f'{MAP_W}x{MAP_H}', 'scale': 2,
                'markers': f'color:0x1B6B4A|{lat},{lng}', 'key': get_key('GOOGLE_MAPS_API_KEY'),
            })
            resp.raise_for_status()
            return resp.content          # non conservé sur disque (conditions Google)
        except requests.RequestException:
            logger.warning('Carte Google indisponible')
            return None
    path = _cache_path(lat, lng)
    if path.exists():
        return path.read_bytes()
    png = _osm_png(lat, lng)
    if png:
        path.write_bytes(png)
    return png


def _osm_png(lat, lng):
    from PIL import Image, ImageDraw

    n = 2 ** ZOOM
    px = (lng + 180) / 360 * n * TILE
    lat_r = math.radians(lat)
    py = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n * TILE
    left, top = px - MAP_W / 2, py - MAP_H / 2
    img = Image.new('RGB', (MAP_W, MAP_H), (238, 238, 238))
    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    try:
        for tx in range(int(left // TILE), int((left + MAP_W) // TILE) + 1):
            for ty in range(int(top // TILE), int((top + MAP_H) // TILE) + 1):
                resp = session.get(f'https://tile.openstreetmap.org/{ZOOM}/{tx % n}/{ty}.png', timeout=TIMEOUT)
                resp.raise_for_status()
                tile = Image.open(io.BytesIO(resp.content)).convert('RGB')
                img.paste(tile, (int(tx * TILE - left), int(ty * TILE - top)))
    except (requests.RequestException, OSError):
        logger.warning('Tuiles OpenStreetMap indisponibles')
        return None
    draw = ImageDraw.Draw(img)
    cx, cy = MAP_W // 2, MAP_H // 2
    # Repère : goutte verte avec un point blanc
    draw.polygon([(cx - 11, cy - 22), (cx + 11, cy - 22), (cx, cy)], fill=GREEN)
    draw.ellipse((cx - 14, cy - 40, cx + 14, cy - 12), fill=GREEN, outline=(255, 255, 255), width=3)
    draw.ellipse((cx - 5, cy - 31, cx + 5, cy - 21), fill=(255, 255, 255))
    # Mention obligatoire des contributeurs OpenStreetMap
    text = '© OpenStreetMap'
    draw.rectangle((MAP_W - 112, MAP_H - 18, MAP_W, MAP_H), fill=(255, 255, 255))
    draw.text((MAP_W - 106, MAP_H - 15), text, fill=(60, 60, 60))
    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
