"""
easevent/dispatch.py
═══════════════════════════════════════════════════════════════
Lancer une tâche Celery sans jamais perdre le travail.

dispatch(task, *args) :
  - met la tâche dans la file Redis (après la transaction en cours) ;
  - si Redis ne répond pas, exécute la tâche tout de suite, dans la
    requête : l'envoi est plus lent mais jamais perdu.
Les tâches planifiées de rattrapage (beat) couvrent le cas d'un worker
arrêté alors que Redis répond.
═══════════════════════════════════════════════════════════════
"""
import logging

from django.conf import settings
from django.db import connection, transaction

logger = logging.getLogger(__name__)


def _send(task, args, kwargs, countdown=None):
    try:
        task.apply_async(args=args, kwargs=kwargs, retry=False, countdown=countdown)
    except Exception as exc:     # Redis absent ou injoignable
        logger.warning('File Celery indisponible (%s) : exécution directe de %s', type(exc).__name__, task.name)
        task.apply(args=args, kwargs=kwargs)


def dispatch(task, *args, countdown=None, **kwargs):
    """countdown : délai (secondes) avant exécution — sert à étaler les vagues d'envoi."""
    if connection.in_atomic_block and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        transaction.on_commit(lambda: _send(task, args, kwargs, countdown))
    else:
        _send(task, args, kwargs, countdown)
