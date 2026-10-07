"""
minisite/dataset.py — journal d'apprentissage (futur modèle Easevent)
════════════════════════════════════════════════════════════════
Une ligne JSON par fait, dans settings.MINISITE_DATASET_DIR/AAAA-MM.jsonl :
  generation  données de l'événement (nettoyées), réponses des modèles, 6 propositions
  choice      proposition choisie (direction, empreinte, délai de choix)
  edit        retouches de l'organisateur (couleurs, ordre, textes, sections masquées)
  regenerate  l'organisateur n'a rien gardé et relance
Aucune donnée de contact : emails, numéros et liens sont retirés (facts.scrub) ;
l'organisateur n'est identifié que par un pseudonyme (hachage salé).
Ces données servent à évaluer les modèles et, plus tard, à entraîner le nôtre.
════════════════════════════════════════════════════════════════
"""
import fcntl
import hashlib
import json
import logging
import os

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def pseudonym(user_id):
    return hashlib.sha256(f'{settings.SECRET_KEY}:{user_id}'.encode()).hexdigest()[:16] if user_id else None


def write(kind, payload):
    if not settings.MINISITE_DATASET_ENABLED:
        return
    try:
        folder = settings.MINISITE_DATASET_DIR
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{timezone.now():%Y-%m}.jsonl")
        line = json.dumps({'type': kind, 'at': timezone.now().isoformat(), **payload}, ensure_ascii=False, default=str)
        with open(path, 'a', encoding='utf-8') as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(line + '\n')
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        logger.exception('Mini-site : journal d\'apprentissage indisponible')
