"""
minisite/copy.py — textes du mini-site
  fallback(facts, direction, rng)  textes rédigés sans IA (repli, toujours disponibles)
  faq(facts, cta_label)            questions-réponses construites UNIQUEMENT à partir des
                                   données de l'événement (l'IA ne rédige jamais de faits)
  clean(copy, facts)               nettoie et valide des textes venus de l'IA
"""
import re

from . import catalog
from .facts import scrub

KICKERS = {
    'mariage': ('Nous nous marions', 'Save the date', 'Le grand jour', 'Oui, pour la vie'),
    'anniversaire': ('On fête ça', 'Une année de plus', 'Joyeux anniversaire', 'La fête est lancée'),
    'soiree': ('Une soirée à part', 'Ce soir, on se retrouve', 'Soirée privée', 'La nuit nous appartient'),
    'gala': ('Soirée de gala', 'Une soirée d’exception', 'Tenue de soirée', 'Gala'),
    'concert': ('En concert', 'Live', 'Sur scène', 'Une nuit de musique'),
    'festival': ('Festival', 'Plusieurs jours de fête', 'L’édition à ne pas manquer', 'Rendez-vous au festival'),
    'conference': ('Conférence', 'Idées et échanges', 'Rencontre', 'Partager, apprendre'),
    'seminaire': ('Séminaire', 'Une journée pour avancer', 'Ensemble', 'Réfléchir, décider'),
    'atelier': ('Atelier', 'Apprendre en faisant', 'Mettre la main à la pâte', 'Atelier pratique'),
    'exposition': ('Exposition', 'Vernissage', 'À voir absolument', 'Regards'),
}
DEFAULT_KICKERS = ('Vous êtes invités', 'Rendez-vous', 'Un moment à partager', 'À ne pas manquer')

SUBTITLES = {
    'mariage': ('Nous serions heureux de vous compter parmi nous.', 'Venez célébrer notre union à nos côtés.', 'Un jour, deux cœurs, tous nos proches.'),
    'anniversaire': ('Venez souffler les bougies avec nous.', 'Une fête entre proches, comme on les aime.', 'On compte sur vous pour fêter ça.'),
    'concert': ('Une nuit de musique à vivre en live.', 'Le son, la scène, et vous.', 'Rendez-vous devant la scène.'),
    'festival': ('Plusieurs jours de musique et de rencontres.', 'Vivez le festival de l’intérieur.', 'Le rendez-vous de la saison.'),
    'conference': ('Des idées, des échanges, des rencontres.', 'Une journée pour comprendre et partager.', 'Venez apprendre et échanger.'),
    'seminaire': ('Une journée pour avancer ensemble.', 'Réfléchir, échanger, décider.', 'Le temps d’un séminaire.'),
    'gala': ('Une soirée d’exception.', 'Élégance et convivialité au rendez-vous.', 'Une soirée placée sous le signe du prestige.'),
}
DEFAULT_SUBTITLES = ('Un moment à partager ensemble.', 'Nous avons hâte de vous y retrouver.', 'Vous êtes chaleureusement invités.')

DIVIDERS = {
    'mariage': ('Deux histoires, une seule aventure', 'L’amour se fête à plusieurs', 'Merci d’être là pour nous'),
    'anniversaire': ('Les plus belles années sont devant', 'Venez comme vous êtes', 'Une fête, des souvenirs'),
    'concert': ('Montez le son', 'La musique nous rassemble', 'Une seule nuit, mille émotions'),
    'festival': ('Musique, rencontres et soleil', 'Vivre le moment ensemble', 'Plus fort ensemble'),
}
DEFAULT_DIVIDERS = ('Un moment à vivre ensemble', 'Nous avons hâte de vous voir', 'Gardez la date')

INTRO_TITLES = {
    'mariage': ('Notre histoire', 'Bienvenue', 'Avec vous'),
    'conference': ('À propos', 'Le programme en bref', 'Pourquoi venir'),
    'seminaire': ('À propos', 'Les objectifs', 'Le déroulé'),
    'atelier': ('L’atelier', 'Ce que vous allez faire', 'À propos'),
    'exposition': ('L’exposition', 'À propos', 'Le regard de l’artiste'),
}
DEFAULT_INTRO_TITLES = ('Bienvenue', 'À propos', 'Le mot de l’organisateur')

INTRO_FALLBACK = {
    'mariage': "Nous serions tellement heureux de partager ce jour avec vous. Retrouvez ici toutes les informations pour être à nos côtés.",
    'anniversaire': "Une fête, des proches, de bons souvenirs à créer : retrouvez ici tout ce qu’il faut savoir pour venir.",
    'conference': "Retrouvez ici toutes les informations pratiques pour participer à cette conférence.",
    'seminaire': "Toutes les informations pratiques pour participer au séminaire sont réunies ici.",
    'atelier': "Toutes les informations pour participer à l’atelier sont réunies ici.",
}
DEFAULT_INTRO = "Retrouvez ici toutes les informations pour nous rejoindre. Nous avons hâte de vous voir."

TONE_FOOTERS = {
    'editorial': 'Au plaisir de vous y retrouver.', 'immersive': 'On se voit là-bas.',
    'minimal': 'À très bientôt.', 'festive': 'Préparez-vous à faire la fête !',
    'luxe': 'Avec toute notre reconnaissance.', 'playful': 'Ça va être génial, on compte sur vous !',
}


def _pick(rng, options):
    return options[rng.randrange(len(options))]


def cta_label(f):
    if f['is_paid']:
        return 'Prendre mon billet'
    return 'Je participe'


def fallback(f, direction, rng):
    t = f['type']
    # La date et le lieu sont affichés par l'application à partir de l'événement : ici, une phrase d'ambiance
    subtitle = _pick(rng, SUBTITLES.get(t, DEFAULT_SUBTITLES))
    intro_body = f['description'] or INTRO_FALLBACK.get(t, DEFAULT_INTRO)
    paid = f['is_paid']
    return {
        'hero': {'kicker': _pick(rng, KICKERS.get(t, DEFAULT_KICKERS)), 'subtitle': subtitle},
        'countdown': {'title': _pick(rng, ('Plus que', 'Le compte à rebours', 'Bientôt'))},
        'intro': {'title': _pick(rng, INTRO_TITLES.get(t, DEFAULT_INTRO_TITLES)), 'body': intro_body},
        'details': {'title': _pick(rng, ('Les informations', 'Infos pratiques', 'Quand et où'))},
        'location': {'title': _pick(rng, ('Le lieu', 'Où nous retrouver', 'Comment venir')),
                     'note': 'Touchez la carte pour obtenir l’itinéraire.'},
        'online': {'title': 'En ligne', 'note': 'Le lien de connexion est disponible pour les participants.'},
        'gallery': {'title': _pick(rng, ('En images', 'Quelques images', 'Galerie'))},
        'dresscode': {'title': _pick(rng, ('Tenue', 'Dress code', 'Côté tenue')),
                      'note': f"Tenue demandée : {f['dress_code']}." if f['dress_code'] else ''},
        'capacity': {'title': 'Places limitées'},
        'cta': {'title': 'Réservez votre place' if paid else _pick(rng, ('Serez-vous des nôtres ?', 'Confirmez votre présence', 'On vous attend')),
                'body': (f"Billet à {f['price_text']}. " if paid and f['price_text'] else '') + 'Votre ticket est généré aussitôt, à présenter à l’entrée.',
                'label': cta_label(f)},
        'host': {'title': _pick(rng, ('Votre hôte', 'L’organisation', 'Qui vous reçoit')),
                 'note': f"Organisé par {f['host_first_name']}. Une question ? Écrivez-lui depuis l’application." if f['host_first_name'] else 'Une question ? Écrivez à l’organisateur depuis l’application.'},
        'faq': {'title': _pick(rng, ('Questions fréquentes', 'Bon à savoir', 'Vos questions'))},
        'divider': {'text': _pick(rng, DIVIDERS.get(t, DEFAULT_DIVIDERS))},
        'calendar': {'label': 'Ajouter à mon agenda'},
        'footer': {'text': TONE_FOOTERS.get(direction, 'À très bientôt.')},
    }


def faq(f, label):
    items = []
    if f['date_text']:
        when = f"Le {f['date_text']}" + (f" à {f['time_text']}" if f['time_text'] else '')
        if f['end_time_text'] and f['same_day']:
            when += f", jusqu’à {f['end_time_text']}"
        elif f['end_date_text'] and not f['same_day']:
            when += f", jusqu’au {f['end_date_text']}"
        # 'key' : l'application recalcule la réponse dans le fuseau horaire du téléphone
        items.append({'q': 'Quand a lieu l’événement ?', 'a': when + '.', 'key': 'when'})
    if f['is_online'] and not f['address']:
        items.append({'q': 'Où se déroule l’événement ?', 'a': 'En ligne : le lien de connexion est donné aux participants dans l’application.'})
    elif f['address']:
        items.append({'q': 'Où se déroule l’événement ?', 'a': f"{f['address']}. La carte de cette page ouvre l’itinéraire."})
    if f['dress_code']:
        items.append({'q': 'Y a-t-il une tenue demandée ?', 'a': f"Oui : {f['dress_code']}."})
    if f['is_paid'] and f['price_text']:
        items.append({'q': 'Combien coûte la participation ?', 'a': f"Le billet coûte {f['price_text']}, payable en ligne en toute sécurité."})
    elif not f['is_paid']:
        items.append({'q': 'La participation est-elle payante ?', 'a': 'Non, la participation est gratuite.'})
    if f['max_guests']:
        items.append({'q': 'Le nombre de places est-il limité ?', 'a': f"Oui, l’événement accueille {f['max_guests']} personnes au plus."})
    items.append({'q': 'Comment confirmer ma présence ?', 'a': f"Touchez « {label} » : votre ticket est généré aussitôt, à présenter à l’entrée."})
    return items[:catalog.FAQ_MAX]


# ── Nettoyage des textes venus de l'IA ──────────────────────────────────────
CTRL = re.compile(r'[\x00-\x1f\x7f<>{}\[\]`*_#|\\]')
TIME_OR_PRICE = re.compile(r'\b\d{1,2}\s?h(?:\s?\d{2})?\b|\b\d{1,2}:\d{2}\b|\d+(?:[.,]\d+)?\s?(?:€|euros?|eur\b|fcfa|\$)', re.I)


def _cut(text, n):
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(' ', 1)[0].rstrip(',;:–- ')
    return cut + '…'


def clean_text(value, limit, f):
    if not isinstance(value, str):
        return None
    text = CTRL.sub('', scrub(value))
    text = re.sub(r'\s+', ' ', text).strip(' "\'«»')
    if not text:
        return None
    blob = ' '.join(str(f.get(k) or '') for k in ('time_text', 'end_time_text', 'price_text', 'description', 'title')).lower()
    for m in TIME_OR_PRICE.finditer(text):
        if m.group(0).lower().replace(' ', '') not in blob.replace(' ', ''):
            return None                                   # heure ou prix inventé : on rejette ce texte
    return _cut(text, limit)


def clean(copy, f):
    """Ne garde que les champs connus, valides et sans fait inventé."""
    out = {}
    if not isinstance(copy, dict):
        return out
    for kind, spec in catalog.SECTIONS.items():
        block = copy.get(kind)
        if not isinstance(block, dict):
            continue
        fields = {}
        for field, limit in spec['copy'].items():
            text = clean_text(block.get(field), limit, f)
            if text:
                fields[field] = text
        if fields:
            out[kind] = fields
    return out
