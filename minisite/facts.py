"""
minisite/facts.py — les données de l'événement transmises au générateur
  facts(event)      : tout ce qu'il faut pour composer et rédiger
  art_brief(facts)  : sous-ensemble NON sensible pour la direction artistique
                      (aucun titre, texte, nom, adresse ni date précise)
"""
import re

from django.utils import timezone

MONTHS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
          'septembre', 'octobre', 'novembre', 'décembre')
DAYS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
SEASONS = {12: 'hiver', 1: 'hiver', 2: 'hiver', 3: 'printemps', 4: 'printemps', 5: 'printemps',
           6: 'été', 7: 'été', 8: 'été', 9: 'automne', 10: 'automne', 11: 'automne'}
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
PHONE = re.compile(r'(?:\+|00)?\d[\d .-]{7,}\d')
URL = re.compile(r'https?://\S+|www\.\S+', re.I)


def scrub(text):
    """Retire emails, numéros de téléphone et liens (données envoyées aux modèles et au journal)."""
    text = EMAIL.sub('', str(text or ''))
    text = PHONE.sub('', text)
    return URL.sub('', text).strip()


def time_fr(dt):
    return f"{dt.hour}h{dt.minute:02d}" if dt.minute else f"{dt.hour}h"


def date_fr(dt):
    return f"{DAYS[dt.weekday()]} {dt.day}{'er' if dt.day == 1 else ''} {MONTHS[dt.month - 1]} {dt.year}"


def _city(address):
    parts = [p.strip() for p in str(address or '').split(',') if p.strip()]
    if not parts:
        return ''
    for p in reversed(parts):
        cleaned = re.sub(r'\b\d{4,5}\b', '', p).strip()
        if cleaned and not cleaned.lower() in ('france', 'cameroun', 'belgique', 'suisse', 'canada', 'sénégal'):
            return cleaned
    return re.sub(r'\b\d{4,5}\b', '', parts[-1]).strip()


def facts(event):
    start = timezone.localtime(event.start_date) if event.start_date else None
    end = timezone.localtime(event.end_date) if event.end_date else None
    tc = event.template_config or {}
    gallery = [g for g in (tc.get('gallery') or []) if g]
    has_cover = bool(event.cover_image or tc.get('cover_image'))
    palette = event.palette if isinstance(event.palette, dict) else {}
    type_label = event.event_type_label if event.event_type == 'autre' and event.event_type_label else event.get_event_type_display()
    organizer = event.organizer
    price = f"{event.price:.2f}".replace('.', ',').replace(',00', '') + ' €' if event.is_paid and event.price else ''
    return {
        'type': event.event_type,
        'type_label': type_label,
        'title': scrub(event.title)[:120],
        'description': scrub(event.description)[:900],
        'ambiance': event.ambiance or '',
        'ambiance_label': event.ambiance_label or '',
        'theme': scrub(getattr(event, 'theme', '') or '')[:80],
        'primary': palette.get('primary') or '',
        'secondary': palette.get('secondary') or '',
        'date_text': date_fr(start) if start else '',
        'time_text': time_fr(start) if start else '',
        'end_time_text': time_fr(end) if end else '',
        'same_day': bool(start and end and start.date() == end.date()),
        'end_date_text': date_fr(end) if end else '',
        'duration_h': round((end - start).total_seconds() / 3600, 1) if start and end else None,
        'month': start.month if start else None,
        'season': SEASONS.get(start.month) if start else '',
        'upcoming': bool(start and start > timezone.now()),
        'address': scrub(event.location_address)[:200],
        'city': _city(scrub(event.location_address)),
        'is_online': bool(event.is_online),
        'is_paid': bool(event.is_paid),
        'price_text': price,
        'max_guests': event.max_guests,
        'dress_code': scrub(event.dress_code)[:80] if event.dress_code else '',
        'images': int(has_cover) + len(gallery),
        'visibility': event.visibility,
        'host_first_name': (organizer.first_name or '').strip()[:40] if organizer else '',
    }


def size_bucket(n):
    if not n:
        return 'ouvert'
    return 'intime' if n <= 30 else 'moyen' if n <= 150 else 'grand'


def art_brief(f):
    """Direction artistique : uniquement des données non sensibles."""
    return {
        'type': f['type'], 'theme': f['theme'], 'ambiance': f['ambiance'], 'primary_color': f['primary'], 'secondary_color': f['secondary'],
        'season': f['season'], 'duration_hours': f['duration_h'], 'photos': f['images'],
        'paid': f['is_paid'], 'online': f['is_online'], 'size': size_bucket(f['max_guests']),
        'dress_code': bool(f['dress_code']), 'visibility': f['visibility'],
    }
