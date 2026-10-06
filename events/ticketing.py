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
