"""
events/tz.py — fuseau horaire de l'événement
Chaque événement garde le fuseau où il a lieu (« Europe/Paris », « Africa/Douala ») :
les dates écrites par le serveur (notifications, emails, SMS, aperçus de lien,
mini-site) sont dans CE fuseau, avec son nom. L'application affiche en plus l'heure
locale du visiteur quand elle diffère.
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.conf import settings
from django.utils import timezone

_VALID = None


def is_valid(name):
    global _VALID
    if _VALID is None:
        _VALID = available_timezones()
    return isinstance(name, str) and name in _VALID


def zone(event):
    try:
        return ZoneInfo(getattr(event, 'timezone', '') or settings.TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(settings.TIME_ZONE)


def local(dt, event):
    return timezone.localtime(dt, zone(event)) if dt else None


def label(event):
    """« heure de Paris », « heure de Douala »."""
    name = getattr(event, 'timezone', '') or settings.TIME_ZONE
    return f"heure de {name.split('/')[-1].replace('_', ' ')}"
