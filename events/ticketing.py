"""
events/ticketing.py
═══════════════════════════════════════════════════════════════
Validation des champs de billetterie d'un événement (écran M23).

Règles :
- un événement gratuit a toujours un prix de 0,00 (chaque participant
  reçoit quand même un ticket) ;
- un événement payant a un prix strictement positif ;
- max_guests vide = places illimitées ;
- dress code : 80 caractères maximum, texte brut.
═══════════════════════════════════════════════════════════════
"""
from decimal import Decimal, InvalidOperation

MAX_PRICE = Decimal('99999.99')
MAX_GUESTS = 100000
CURRENCIES = {'EUR', 'XAF', 'XOF', 'USD', 'GBP', 'CAD', 'CHF'}


def _to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'oui', 'yes', 'on')


def clean_ticketing(data, current=None):
    """
    data    : dictionnaire reçu (création ou PATCH).
    current : événement existant (PATCH) pour compléter les champs absents.
    Retourne (valeurs_nettoyées, erreurs).
    """
    errors, out = {}, {}
    legacy = data.get('template_config') if isinstance(data.get('template_config'), dict) else {}

    def pick(name):
        if name in data:
            return data.get(name)
        return legacy.get(name)

    raw_paid = pick('is_paid')
    is_paid = _to_bool(raw_paid) if raw_paid is not None else (current.is_paid if current else False)

    raw_price = pick('price')
    if raw_price in (None, ''):
        price = current.price if (current and raw_paid is None) else Decimal('0')
    else:
        try:
            price = Decimal(str(raw_price).replace(',', '.').strip()).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            errors['price'] = 'Le prix doit être un nombre (ex. 25,00).'
            price = Decimal('0')

    if 'price' not in errors:
        if price < 0 or price > MAX_PRICE:
            errors['price'] = 'Le prix doit être compris entre 0 et 99 999,99.'
        elif is_paid and price <= 0:
            errors['price'] = 'Indiquez le prix du ticket pour un événement payant.'
    if not is_paid:
        price = Decimal('0')
    out['is_paid'], out['price'] = is_paid, price

    if 'currency' in data:
        currency = str(data.get('currency') or 'EUR').upper()
        if currency not in CURRENCIES:
            errors['currency'] = 'Devise non prise en charge.'
        out['currency'] = currency

    raw_guests = pick('max_guests')
    if raw_guests is not None or 'max_guests' in data:
        if raw_guests in (None, ''):
            out['max_guests'] = None
        else:
            try:
                guests = int(str(raw_guests).strip())
                if guests < 1 or guests > MAX_GUESTS:
                    raise ValueError
                out['max_guests'] = guests
            except (TypeError, ValueError):
                errors['max_guests'] = 'Le nombre de places doit être un entier entre 1 et 100 000.'

    if 'dress_code' in data:
        dress = str(data.get('dress_code') or '').strip()
        if len(dress) > 80:
            errors['dress_code'] = 'Le dress code fait 80 caractères maximum.'
        elif any(ch in dress for ch in '<>{}'):
            errors['dress_code'] = 'Le dress code contient des caractères non autorisés.'
        out['dress_code'] = dress or None

    return out, errors


# ─────────────────────────────────────────────────────────────
# Type personnalisé et palette libre (étapes 1 et 4)
# ─────────────────────────────────────────────────────────────
import re

HEX_COLOR = re.compile(r'^#[0-9A-Fa-f]{6}$')


def clean_style(data, event_type):
    """
    Valide le type personnalisé (« Autre ») et la palette de couleurs.
    La palette est libre : toute couleur hexadécimale #RRGGBB est acceptée.
    Retourne (valeurs_nettoyées, erreurs).
    """
    errors, out = {}, {}

    if 'event_type_label' in data or event_type == 'autre':
        label = str(data.get('event_type_label') or '').strip()
        if event_type == 'autre' and not label:
            errors['event_type_label'] = "Indiquez le type de votre événement."
        elif len(label) > 40:
            errors['event_type_label'] = 'Le type fait 40 caractères maximum.'
        elif any(ch in label for ch in '<>{}'):
            errors['event_type_label'] = 'Le type contient des caractères non autorisés.'
        # Un type prédéfini n'a pas de libellé personnalisé
        out['event_type_label'] = label if event_type == 'autre' else ''

    if 'ambiance' in data or 'ambiance_label' in data:
        ambiance = str(data.get('ambiance') or '')
        allowed = {'', 'elegant', 'festif', 'minimaliste', 'colore', 'professionnel', 'autre'}
        if ambiance not in allowed:
            errors['ambiance'] = 'Ambiance invalide.'
        label = str(data.get('ambiance_label') or '').strip()
        if ambiance == 'autre' and not label:
            errors['ambiance_label'] = "Décrivez l'ambiance de votre événement."
        elif len(label) > 40 or any(ch in label for ch in '<>{}'):
            errors['ambiance_label'] = "L'ambiance fait 40 caractères maximum, sans caractères spéciaux."
        out['ambiance'] = ambiance
        out['ambiance_label'] = label if ambiance == 'autre' else ''

    if 'theme' in data:
        theme = ' '.join(str(data.get('theme') or '').split())
        if len(theme) > 160:
            errors['theme'] = 'Le thème fait 160 caractères maximum.'
        elif any(ch in theme for ch in '<>{}'):
            errors['theme'] = 'Le thème contient des caractères non autorisés.'
        out['theme'] = theme

    if 'assistant_enabled' in data:
        if not isinstance(data.get('assistant_enabled'), bool):
            errors['assistant_enabled'] = 'Valeur invalide.'
        else:
            out['assistant_enabled'] = data['assistant_enabled']

    if 'palette' in data and data.get('palette') is not None:
        palette = data.get('palette')
        if not isinstance(palette, dict):
            errors['palette'] = 'Palette invalide.'
        else:
            clean = {}
            for key in ('primary', 'secondary'):
                value = palette.get(key)
                if value in (None, ''):
                    continue
                if not isinstance(value, str) or not HEX_COLOR.match(value):
                    errors['palette'] = 'Les couleurs doivent être au format #RRGGBB.'
                    break
                clean[key] = value.upper()
            out['palette'] = clean or None
    return out, errors
