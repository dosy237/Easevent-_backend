"""
events/fx.py — taux de change pour afficher les prix dans la devise du visiteur
════════════════════════════════════════════════════════════════
GET /api/fx/rates/  →  { base: "EUR", date, source, rates: {USD: 1.08, XAF: 655.957, …}, currencies: […] }
- Taux quotidiens de la Banque centrale européenne (api.frankfurter.app), mis en
  cache 6 h ; derniers taux connus gardés 7 jours si la source est injoignable.
- Franc CFA : parité FIXE avec l'euro (1 € = 655,957 XAF / XOF, BEAC / BCEAO).
- Conversion INDICATIVE : le paiement reste débité dans la devise de l'événement.
════════════════════════════════════════════════════════════════
"""
import logging

import requests
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)
SOURCE_URL = 'https://api.frankfurter.app/latest'
CFA = 655.957
# Repli (ordre de grandeur) si aucune donnée n'a encore pu être obtenue
FALLBACK = {'USD': 1.08, 'GBP': 0.85, 'CHF': 0.95, 'CAD': 1.48, 'JPY': 162.0, 'CNY': 7.8, 'INR': 90.0, 'BRL': 6.0,
            'MXN': 19.5, 'ZAR': 20.0, 'TRY': 37.0, 'AUD': 1.65, 'SEK': 11.4, 'NOK': 11.6, 'DKK': 7.46, 'PLN': 4.3}
CURRENCIES = [
    ('EUR', 'Euro', '€'), ('XAF', 'Franc CFA (Afrique centrale)', 'FCFA'), ('XOF', 'Franc CFA (Afrique de l’Ouest)', 'FCFA'),
    ('USD', 'Dollar américain', '$'), ('GBP', 'Livre sterling', '£'), ('CAD', 'Dollar canadien', '$ CA'),
    ('CHF', 'Franc suisse', 'CHF'), ('ZAR', 'Rand sud-africain', 'R'), ('INR', 'Roupie indienne', '₹'),
    ('CNY', 'Yuan', '¥'), ('JPY', 'Yen', '¥'), ('BRL', 'Réal brésilien', 'R$'), ('MXN', 'Peso mexicain', '$ MX'),
    ('TRY', 'Livre turque', '₺'), ('AUD', 'Dollar australien', '$ AU'), ('SEK', 'Couronne suédoise', 'kr'),
    ('NOK', 'Couronne norvégienne', 'kr'), ('DKK', 'Couronne danoise', 'kr'), ('PLN', 'Złoty', 'zł'),
]


def get_rates():
    cached = cache.get('fx:EUR')
    if cached:
        return cached
    try:
        r = requests.get(SOURCE_URL, params={'from': 'EUR'}, timeout=6)
        r.raise_for_status()
        data = r.json()
        rates = {k: float(v) for k, v in (data.get('rates') or {}).items()}
        if not rates:
            raise ValueError('aucun taux')
        result = {'base': 'EUR', 'date': data.get('date'), 'source': 'Banque centrale européenne', 'rates': rates}
        cache.set('fx:EUR:last', result, 7 * 86400)
    except Exception as exc:                                  # source injoignable : dernier taux connu, sinon repli
        logger.info('Taux de change indisponibles (%s)', type(exc).__name__)
        result = cache.get('fx:EUR:last') or {'base': 'EUR', 'date': None, 'source': 'Taux indicatifs', 'rates': dict(FALLBACK)}
    result = {**result, 'rates': {**result['rates'], 'EUR': 1.0, 'XAF': CFA, 'XOF': CFA}}
    cache.set('fx:EUR', result, 6 * 3600)
    return result


@api_view(['GET'])
@permission_classes([AllowAny])
def rates(request):
    data = get_rates()
    known = data['rates']
    return Response({**data, 'currencies': [{'code': c, 'name': n, 'symbol': s} for c, n, s in CURRENCIES if c in known]})
