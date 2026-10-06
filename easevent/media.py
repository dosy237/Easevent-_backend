"""
easevent/media.py
═══════════════════════════════════════════════════════════════
Construction des URL d'images renvoyées à l'application mobile.

Toutes les images (photos d'événements, illustrations, logo) sont
servies par ce backend. L'application ne reçoit que des URL
absolues : un chemin relatif ("events/photo.png") ne peut pas être
affiché par l'APK.
═══════════════════════════════════════════════════════════════
"""
from django.conf import settings


def public_url(path_or_url, request=None):
    """Retourne une URL absolue pour un fichier média (ou None)."""
    if not path_or_url:
        return None
    if path_or_url.startswith(('http://', 'https://')):
        return path_or_url

    path = path_or_url.lstrip('/')
    media_prefix = settings.MEDIA_URL.strip('/') + '/'
    if not path.startswith(media_prefix):
        path = media_prefix + path
    path = '/' + path

    base = getattr(settings, 'PUBLIC_BASE_URL', '')
    # Adresse publique configurée en HTTPS : prioritaire (derrière un proxy,
    # la requête peut arriver en http et produire des URL refusées par Android)
    if base.startswith('https://') or request is None:
        return f"{base}{path}"
    return request.build_absolute_uri(path)


def static_headers(headers, path, url):
    """
    WHITENOISE_ADD_HEADERS_FUNCTION : les images publiques de l'application
    (static/app/) sont lisibles depuis n'importe quelle origine. Aucune donnée
    personnelle ni cookie n'est concerné.
    """
    if url.startswith(settings.STATIC_URL + 'app/'):
        headers['Access-Control-Allow-Origin'] = '*'
