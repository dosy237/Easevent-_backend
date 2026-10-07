"""
minisite/catalog.py — la bibliothèque des mini-sites
════════════════════════════════════════════════════════════════
Un mini-site est un « plan » (spec JSON) composé à partir de cette bibliothèque :
  sections (type + variante + options) × thème (couleurs, polices, formes,
  ornements, densité). L'application dessine chaque variante avec ses propres
  composants (components/minisite/) : aucune ligne de code générée par l'IA
  n'est exécutée, le rendu est toujours soigné, accessible et rapide.

⚠ Toute variante ajoutée ici doit exister côté application
  (easevent_frontend/components/minisite/catalog.js) — test de parité.
════════════════════════════════════════════════════════════════
"""

SPEC_VERSION = 1

# ── Polices : paires titre / texte (chargées par l'application) ────────────
FONT_PAIRS = {
    'playfair-inter':      'Playfair Display / Inter',
    'cormorant-lora':      'Cormorant Garamond / Lora',
    'dmserif-poppins':     'DM Serif Display / Poppins',
    'fraunces-nunito':     'Fraunces / Nunito',
    'bebas-inter':         'Bebas Neue / Inter',
    'syne-inter':          'Syne / Inter',
    'spacegrotesk-inter':  'Space Grotesk / Inter',
    'greatvibes-lora':     'Great Vibes / Lora',
    'poppins-nunito':      'Poppins / Nunito',
    'lora-inter':          'Lora / Inter',
    'dmserif-inter':       'DM Serif Display / Inter',
    'syne-nunito':         'Syne / Nunito',
}

RADII = ('sharp', 'soft', 'round', 'pill')
DENSITIES = ('airy', 'balanced', 'compact')
ORNAMENTS = ('none', 'lines', 'dots', 'botanical', 'waves', 'stars', 'confetti', 'grain', 'arches')
HARMONIES = ('monochrome', 'analogous', 'complementary', 'triadic', 'pastel', 'dark', 'gold', 'neutral', 'vivid')
TONES = ('plain', 'tinted', 'inverse')            # traitement du fond de chaque section
ALIGNS = ('left', 'center')
MOTIONS = ('calm', 'lively')

# ── Sections : variantes, options, textes rédigés (longueur max) ─────────────
SECTIONS = {
    'hero':      {'variants': ('fullbleed', 'split', 'framed', 'typographic', 'stacked', 'arch', 'poster'),
                  'copy': {'kicker': 40, 'subtitle': 140}},
    'countdown': {'variants': ('tiles', 'inline', 'ring', 'minimal'), 'copy': {'title': 60}},
    'intro':     {'variants': ('centered', 'quote', 'columns', 'letter'), 'copy': {'title': 60, 'body': 600}},
    'details':   {'variants': ('cards', 'list', 'timeline', 'stub'), 'copy': {'title': 60}},
    'location':  {'variants': ('mapcard', 'splitmap', 'minimal', 'illustrated'), 'copy': {'title': 60, 'note': 160}},
    'online':    {'variants': ('joincard', 'banner'), 'copy': {'title': 60, 'note': 160}},
    'gallery':   {'variants': ('grid', 'mosaic', 'carousel', 'polaroid', 'filmstrip'), 'copy': {'title': 60}},
    'dresscode': {'variants': ('swatches', 'card', 'banner'), 'copy': {'title': 60, 'note': 180}},
    'capacity':  {'variants': ('bar', 'badge'), 'copy': {'title': 60}},
    'cta':       {'variants': ('card', 'banner', 'split', 'minimal'), 'copy': {'title': 70, 'body': 180, 'label': 28}},
    'host':      {'variants': ('card', 'signature', 'inline'), 'copy': {'title': 60, 'note': 220}},
    'faq':       {'variants': ('accordion', 'cards', 'twocol'), 'copy': {'title': 60}},
    'divider':   {'variants': ('ornament', 'quote', 'marquee'), 'copy': {'text': 90}},
    'calendar':  {'variants': ('button', 'card'), 'copy': {'label': 40}},
    'footer':    {'variants': ('simple', 'signature', 'centered'), 'copy': {'text': 140}},
}
# ── Bannière d'accueil (photo plein cadre) : filtre et teinte ────────────────
# veil : dégradé sombre vers le texte ; glass : texte sur un panneau de verre dépoli ;
# tint : la photo prend une couleur (celle du thème, ou une teinte choisie).
HERO_FILTERS = ('veil', 'glass', 'tint')
HERO_TINTS = ('primary', 'blue', 'rose', 'gold', 'sage', 'night')
PHOTO_HEROES = ('fullbleed',)                      # accueils où la photo occupe tout le cadre
# Célébrations : la photo du couple / de la personne fêtée mérite le plein cadre
BANNER_TINTS_BY_TYPE = {'mariage': ('rose', 'gold', 'blue', 'primary'), 'anniversaire': ('rose', 'gold', 'primary'),
                        'gala': ('gold', 'night', 'primary'), 'soiree': ('night', 'blue', 'primary')}
FAQ_MAX = 5
FAQ_Q_MAX, FAQ_A_MAX = 90, 260

# ── Directions artistiques : une par proposition (6 propositions) ────────────
DIRECTIONS = {
    'editorial': {
        'label': 'Éditorial',
        'fonts': ('playfair-inter', 'dmserif-inter', 'lora-inter', 'fraunces-nunito', 'cormorant-lora'),
        'radius': ('sharp', 'soft'), 'density': ('airy', 'balanced'),
        'ornament': ('lines', 'none', 'grain'), 'harmony': ('monochrome', 'analogous', 'neutral'),
        'hero': ('typographic', 'split', 'stacked', 'framed'), 'motion': ('calm',),
        'tones': ('plain', 'plain', 'tinted', 'inverse'),
    },
    'immersive': {
        'label': 'Photo immersive',
        'fonts': ('bebas-inter', 'syne-inter', 'playfair-inter', 'spacegrotesk-inter', 'dmserif-poppins'),
        'radius': ('soft', 'round'), 'density': ('balanced', 'compact'),
        'ornament': ('none', 'grain', 'lines'), 'harmony': ('dark', 'monochrome', 'complementary'),
        'hero': ('fullbleed', 'poster', 'split'), 'motion': ('lively', 'calm'),
        'tones': ('plain', 'inverse', 'tinted'),
    },
    'minimal': {
        'label': 'Minimal',
        'fonts': ('spacegrotesk-inter', 'lora-inter', 'poppins-nunito', 'dmserif-inter', 'syne-inter'),
        'radius': ('sharp', 'soft'), 'density': ('airy',),
        'ornament': ('none', 'lines'), 'harmony': ('neutral', 'monochrome'),
        'hero': ('typographic', 'stacked', 'framed', 'split'), 'motion': ('calm',),
        'tones': ('plain', 'plain', 'tinted'),
    },
    'festive': {
        'label': 'Festif',
        'fonts': ('syne-nunito', 'poppins-nunito', 'bebas-inter', 'fraunces-nunito', 'syne-inter'),
        'radius': ('round', 'pill'), 'density': ('balanced', 'compact'),
        'ornament': ('confetti', 'dots', 'waves', 'stars'), 'harmony': ('vivid', 'triadic', 'complementary'),
        'hero': ('poster', 'fullbleed', 'arch', 'stacked'), 'motion': ('lively',),
        'tones': ('plain', 'tinted', 'inverse', 'tinted'),
    },
    'luxe': {
        'label': 'Luxe',
        'fonts': ('cormorant-lora', 'greatvibes-lora', 'playfair-inter', 'dmserif-poppins', 'dmserif-inter'),
        'radius': ('sharp', 'soft'), 'density': ('airy', 'balanced'),
        'ornament': ('lines', 'stars', 'botanical', 'arches'), 'harmony': ('gold', 'dark', 'monochrome'),
        'hero': ('arch', 'framed', 'fullbleed', 'typographic'), 'motion': ('calm',),
        'tones': ('plain', 'inverse', 'tinted'),
    },
    'playful': {
        'label': 'Ludique',
        'fonts': ('fraunces-nunito', 'poppins-nunito', 'syne-nunito', 'greatvibes-lora', 'bebas-inter'),
        'radius': ('pill', 'round'), 'density': ('balanced', 'compact'),
        'ornament': ('dots', 'waves', 'confetti', 'botanical'), 'harmony': ('pastel', 'analogous', 'triadic'),
        'hero': ('arch', 'stacked', 'poster', 'split'), 'motion': ('lively',),
        'tones': ('tinted', 'plain', 'tinted', 'inverse'),
    },
}
DIRECTION_ORDER = ('editorial', 'immersive', 'minimal', 'festive', 'luxe', 'playful')

# ── Structure selon le type d'événement ──────────────────────────────────────
# core : sections principales dans un ordre de base (le compositeur les permute
# légèrement) ; extras : sections ajoutées ou non selon la proposition.
STRUCTURES = {
    'mariage':      {'core': ('countdown', 'intro', 'details', 'dresscode', 'location', 'gallery', 'faq', 'cta'),
                     'extras': ('divider', 'host', 'calendar')},
    'anniversaire': {'core': ('countdown', 'intro', 'details', 'gallery', 'location', 'dresscode', 'cta', 'faq'),
                     'extras': ('divider', 'host', 'calendar')},
    'soiree':       {'core': ('intro', 'details', 'countdown', 'location', 'gallery', 'dresscode', 'cta', 'faq'),
                     'extras': ('divider', 'capacity', 'calendar')},
    'gala':         {'core': ('intro', 'countdown', 'details', 'dresscode', 'location', 'gallery', 'cta', 'faq'),
                     'extras': ('divider', 'host', 'capacity')},
    'concert':      {'core': ('countdown', 'cta', 'details', 'gallery', 'location', 'capacity', 'faq'),
                     'extras': ('divider', 'intro', 'calendar')},
    'festival':     {'core': ('countdown', 'intro', 'cta', 'details', 'gallery', 'location', 'capacity', 'faq'),
                     'extras': ('divider', 'calendar')},
    'conference':   {'core': ('intro', 'details', 'online', 'location', 'capacity', 'cta', 'faq'),
                     'extras': ('host', 'calendar', 'countdown')},
    'seminaire':    {'core': ('intro', 'details', 'online', 'location', 'cta', 'faq'),
                     'extras': ('host', 'calendar', 'capacity')},
    'atelier':      {'core': ('intro', 'details', 'online', 'location', 'capacity', 'cta', 'faq'),
                     'extras': ('host', 'gallery', 'calendar')},
    'exposition':   {'core': ('intro', 'gallery', 'details', 'location', 'cta', 'faq'),
                     'extras': ('divider', 'calendar', 'host')},
    'autre':        {'core': ('intro', 'countdown', 'details', 'location', 'gallery', 'cta', 'faq'),
                     'extras': ('divider', 'host', 'calendar')},
}
# Sections qui ne bougent pas : l'accueil en premier, le pied de page en dernier
FIXED_FIRST, FIXED_LAST = 'hero', 'footer'


def combinations_count():
    """Ordre de grandeur des mini-sites possibles (affiché dans la documentation)."""
    variants = 1
    for s in SECTIONS.values():
        variants *= len(s['variants']) * len(TONES) * len(ALIGNS)
    theme = len(FONT_PAIRS) * len(RADII) * len(DENSITIES) * len(ORNAMENTS) * len(HARMONIES)
    return variants * theme
