# easevent/__init__.py
# ════════════════════════════════════════════════════════════════════════
# Importe l'app Celery au démarrage de Django pour que les signaux
# @shared_task fonctionnent correctement dans toutes les apps.
# ════════════════════════════════════════════════════════════════════════
from .celery import app as celery_app

__all__ = ('celery_app',)
