"""
events/validation.py — contrôle des champs d'un événement (création et modification)

Toute entrée invalide donne un message clair (400) au lieu d'une erreur
serveur : types inattendus, dates impossibles, textes trop longs, liens
non http(s), coordonnées hors limites.
"""
import json

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils import timezone
from django.utils.dateparse import parse_datetime

TEXT_LIMITS = {'title': 100, 'description': 5000, 'location_address': 500}
TEMPLATE_CONFIG_MAX = 20_000        # octets (JSON)


class EventInputError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def _text(data, key, required=False):
    value = data.get(key)
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise EventInputError('Texte attendu.', key)
    value = value.strip()
    if required and not value:
        raise EventInputError('Le titre est obligatoire.' if key == 'title' else f'Le champ « {key} » est obligatoire.', key)
    if len(value) > TEXT_LIMITS[key]:
        raise EventInputError(f'{TEXT_LIMITS[key]} caractères maximum.', key)
    return value


def _date(value, key):
    if not isinstance(value, str):
        raise EventInputError('Date invalide.', key)
    try:
        parsed = parse_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        raise EventInputError('Date invalide. Format attendu : 2026-09-15T18:00:00', key)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _bool(value, key):
    if isinstance(value, bool):
        return value
    if value in ('true', 'True', '1', 1):
        return True
    if value in ('false', 'False', '0', 0, None, ''):
        return False
    raise EventInputError('Valeur oui / non attendue.', key)


def _coord(value, key, limit):
    if value in (None, ''):
        return None
    try:
        number = round(float(value), 6)
    except (TypeError, ValueError):
        raise EventInputError('Coordonnées invalides.', key)
    if not -limit <= number <= limit:
        raise EventInputError('Coordonnées invalides.', key)
    return number


def clean_core(data, current=None):
    """
    Champs principaux. current : événement modifié (PATCH partiel) ou None (création).
    Retourne un dict des valeurs à enregistrer.
    """
    if not isinstance(data, dict):
        raise EventInputError('Requête invalide.')
    creating = current is None
    out = {}
    if creating or 'title' in data:
        out['title'] = _text(data, 'title', required=True)
    for key in ('description', 'location_address'):
        if creating or key in data:
            out[key] = _text(data, key)
    if creating or 'start_date' in data:
        if creating and not data.get('start_date'):
            raise EventInputError('Le champ "start_date" est obligatoire.', 'start_date')
        out['start_date'] = _date(data.get('start_date'), 'start_date')
    if creating or 'end_date' in data:
        if creating and not data.get('end_date'):
            raise EventInputError('Le champ "end_date" est obligatoire.', 'end_date')
        out['end_date'] = _date(data.get('end_date'), 'end_date')
    start = out.get('start_date', getattr(current, 'start_date', None))
    end = out.get('end_date', getattr(current, 'end_date', None))
    if start and end and end <= start:
        raise EventInputError('La date de fin doit être après la date de début.', 'end_date')
    if creating or 'is_online' in data:
        out['is_online'] = _bool(data.get('is_online', False), 'is_online')
    if 'latitude' in data or creating:
        out['latitude'] = _coord(data.get('latitude'), 'latitude', 90)
    if 'longitude' in data or creating:
        out['longitude'] = _coord(data.get('longitude'), 'longitude', 180)
    if creating or 'online_link' in data:
        link = data.get('online_link') or None
        if link is not None:
            if not isinstance(link, str) or len(link) > 512:
                raise EventInputError('Lien invalide (512 caractères maximum).', 'online_link')
            try:
                URLValidator(schemes=['http', 'https'])(link.strip())
            except ValidationError:
                raise EventInputError('Lien invalide : il doit commencer par https://', 'online_link')
            link = link.strip()
        out['online_link'] = link
    if 'timezone' in data:
        from .tz import is_valid
        tz = data.get('timezone')
        if tz:                                       # fuseau du téléphone de l'organisateur
            if not is_valid(tz):
                raise EventInputError('Fuseau horaire invalide.', 'timezone')
            out['timezone'] = tz
    if creating or 'online_link_public' in data:
        out['online_link_public'] = _bool(data.get('online_link_public', False), 'online_link_public')
    if creating or 'cover_image' in data:
        cover = data.get('cover_image') or None
        if cover is not None and (not isinstance(cover, str) or len(cover) > 512):
            raise EventInputError('Image de couverture invalide.', 'cover_image')
        out['cover_image'] = cover
    if creating or 'template_config' in data:
        cfg = data.get('template_config')
        if cfg is not None and (not isinstance(cfg, dict) or len(json.dumps(cfg)) > TEMPLATE_CONFIG_MAX):
            raise EventInputError('Configuration du modèle invalide.', 'template_config')
        out['template_config'] = cfg
    # Une nouvelle adresse saisie sans suggestion : les anciennes coordonnées ne valent plus
    if current is not None and 'location_address' in out and out['location_address'] != current.location_address \
            and 'latitude' not in data:
        out['latitude'] = out['longitude'] = None
    return out
