"""
events/demo_covers.py
═══════════════════════════════════════════════════════════════
Couvertures cohérentes pour les événements de démonstration.

- Une vraie photo quand elle correspond au sujet (cuisine, cocktail,
  technologie) ;
- sinon une couverture illustrée propre au type d'événement
  (static/app/covers/<type>-<1..3>.jpg, ~30 Ko chacune).

Les fichiers sont des fichiers statiques du backend : ils sont
déployés avec le code et mis en cache 7 jours par l'application.
═══════════════════════════════════════════════════════════════
"""
import hashlib
import re

COVERS_URL = '/static/app/covers/'
TYPES = {'mariage', 'conference', 'anniversaire', 'soiree', 'concert', 'seminaire',
         'gala', 'exposition', 'festival', 'atelier', 'autre'}

# Mots du titre → photo réelle cohérente (prioritaire : sujet très précis)
TITLE_PHOTOS = [
    (r'cuisine|chef|culinaire',               ['cuisine-chef', 'cuisine-ramen']),
    (r'food|gastronom|brunch',                ['cuisine-brunch', 'cuisine-saumon', 'cuisine-veloute']),
    (r'cocktail',                             ['cocktail']),
    (r'afterwork',                            ['afterwork']),
    (r'cin[eé]ma|film|rooftop',               ['soiree-cinema-rooftop']),
]

# Type d'événement → vraies photos fournies par l'équipe
TYPE_PHOTOS = {
    'mariage':    ['mariage-reception', 'mariage-alliance', 'mariage-mains', 'mariage-noir-blanc'],
    'concert':    ['concert-stade'],
    'festival':   ['concert-stade', 'soiree-cinema-rooftop'],
    'conference': ['conference-panel', 'conference-salle', 'seminaire-salle'],
    'seminaire':  ['seminaire-salle', 'conference-salle'],
    'gala':       ['mariage-reception'],
}

# Sujet technologique (types sans photo dédiée : exposition, atelier…)
TECH = r'\bia\b|\bai\b|intelligence|digital|innovation|tech|num[eé]rique|django|web|cloud|startup'


def _pick(options, key):
    digest = int(hashlib.sha256(key.encode('utf-8')).hexdigest(), 16)
    return options[digest % len(options)]


def cover_for(event_type, title=''):
    """Chemin statique d'une couverture cohérente avec le type et le titre."""
    lowered = (title or '').lower()
    for pattern, photos in TITLE_PHOTOS:
        if re.search(pattern, lowered):
            return f"{COVERS_URL}photos/{_pick(photos, lowered)}.jpg"
    if event_type in TYPE_PHOTOS:
        return f"{COVERS_URL}photos/{_pick(TYPE_PHOTOS[event_type], lowered or event_type)}.jpg"
    if re.search(TECH, lowered):
        return f"{COVERS_URL}photos/{_pick(['tech-ia', 'tech-vr'], lowered)}.jpg"
    kind = event_type if event_type in TYPES else 'autre'
    return f"{COVERS_URL}{kind}-{_pick(['1', '2', '3'], lowered or kind)}.jpg"


def is_demo_cover(value):
    """Couverture de démo (ancienne copie seed_* ou couverture statique)."""
    return bool(value) and (value.startswith('events/seed_') or value.startswith(COVERS_URL))
