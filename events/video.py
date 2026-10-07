"""
events/video.py — vidéo de présentation d'un événement public (45 s au plus)
════════════════════════════════════════════════════════════════
1. L'application demande une signature (POST /api/events/video/signature/) puis envoie
   la vidéo DIRECTEMENT à Cloudinary : le fichier ne transite pas par notre serveur
   (pas de limite nginx, pas de charge), et la signature n'autorise qu'un envoi dans
   le dossier de l'organisateur, pendant une heure.
2. L'application transmet l'identifiant obtenu avec l'événement ({"video": {"public_id",
   "caption"}}) : le serveur interroge Cloudinary et vérifie la vidéo réelle (durée,
   format, poids). Refusée → supprimée de Cloudinary.
3. Diffusion : version optimisée (format et qualité adaptés à l'appareil, 1080 px au
   plus) et une image d'aperçu. Lecture au toucher, jamais automatique (sobriété).
════════════════════════════════════════════════════════════════
"""
import time
import uuid

import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

MAX_SECONDS = 45
TOLERANCE = 0.9                       # arrondis de durée des encodeurs
MAX_BYTES = 200 * 1024 * 1024
FORMATS = {'mp4', 'mov', 'webm', 'm4v', '3gp'}
CAPTION_MAX = 220


class VideoError(Exception):
    def __init__(self, message, code='invalid_video'):
        super().__init__(message)
        self.message, self.code = message, code


class VideoThrottle(UserRateThrottle):
    scope = 'video'


def folder_for(user):
    return f'events/videos/{user.id.hex}'


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([VideoThrottle])
def signature(request):
    """Paramètres signés pour un envoi direct à Cloudinary (valables une heure)."""
    cfg = cloudinary.config()
    if not cfg.api_secret or cfg.api_secret == 'dummy':
        return Response({'detail': 'L’envoi de vidéos n’est pas encore configuré.', 'code': 'video_unavailable'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    params = {'timestamp': int(time.time()), 'folder': folder_for(request.user), 'public_id': uuid.uuid4().hex}
    params['signature'] = cloudinary.utils.api_sign_request(params, cfg.api_secret)
    return Response({**params, 'api_key': cfg.api_key, 'cloud_name': cfg.cloud_name,
                     'upload_url': f'https://api.cloudinary.com/v1_1/{cfg.cloud_name}/video/upload',
                     'max_seconds': MAX_SECONDS})


def _urls(public_id):
    url, _ = cloudinary.utils.cloudinary_url(public_id, resource_type='video', secure=True,
                                            transformation=[{'quality': 'auto', 'fetch_format': 'auto',
                                                             'width': 1080, 'crop': 'limit'}])
    poster, _ = cloudinary.utils.cloudinary_url(f'{public_id}.jpg', resource_type='video', secure=True,
                                               transformation=[{'start_offset': 0, 'width': 1080, 'crop': 'limit',
                                                                'quality': 'auto'}])
    return url, poster


def attach(event, user, data):
    """
    data : {"public_id", "caption"} pour ajouter / remplacer, None pour retirer.
    Renvoie les champs à enregistrer sur l'événement.
    """
    if data is None:
        if event is not None and event.video_public_id:
            _destroy(event.video_public_id)
        return {'video_public_id': '', 'video': None}
    if not isinstance(data, dict):
        raise VideoError('Vidéo invalide.')
    caption = ' '.join(str(data.get('caption') or '').split())
    if len(caption) > CAPTION_MAX or any(ch in caption for ch in '<>{}'):
        raise VideoError(f'Le texte de la vidéo fait {CAPTION_MAX} caractères au plus.', 'invalid_caption')
    public_id = str(data.get('public_id') or '').strip()
    # Même vidéo, seule la légende change
    if event is not None and public_id == event.video_public_id and event.video:
        return {'video': {**event.video, 'caption': caption}}
    # Seules les vidéos envoyées par l'organisateur dans SON dossier sont acceptées
    if not public_id.startswith(folder_for(user) + '/') or len(public_id) > 200:
        raise VideoError('Vidéo introuvable : renvoyez-la depuis l’application.', 'not_found')
    try:
        info = cloudinary.api.resource(public_id, resource_type='video')
    except Exception:
        raise VideoError('Vidéo introuvable : renvoyez-la depuis l’application.', 'not_found')
    duration = float(info.get('duration') or 0)
    if duration <= 0 or duration > MAX_SECONDS + TOLERANCE:
        _destroy(public_id)
        raise VideoError(f'La vidéo dure {round(duration)} s : {MAX_SECONDS} secondes au plus.', 'too_long')
    if int(info.get('bytes') or 0) > MAX_BYTES or str(info.get('format', '')).lower() not in FORMATS:
        _destroy(public_id)
        raise VideoError('Format ou poids de vidéo non pris en charge (MP4, MOV ou WebM).', 'invalid_format')
    if event is not None and event.video_public_id and event.video_public_id != public_id:
        _destroy(event.video_public_id)               # l'ancienne vidéo est remplacée
    url, poster = _urls(public_id)
    return {'video_public_id': public_id, 'video': {
        'url': url, 'poster': poster, 'caption': caption, 'duration': round(duration, 1),
        'width': info.get('width'), 'height': info.get('height'),
    }}


def _destroy(public_id):
    try:
        cloudinary.uploader.destroy(public_id, resource_type='video', invalidate=True)
    except Exception:
        pass
