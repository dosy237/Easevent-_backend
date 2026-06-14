"""
easevent/celery.py
═══════════════════════════════════════════════════════════════════════
Configuration de l'application Celery pour Easevent.

Ce fichier était manquant du dépôt — il est requis pour que le
worker Celery démarre.  Il doit être importé dans __init__.py.
═══════════════════════════════════════════════════════════════════════
"""

import os
from celery import Celery

# Pointe Celery vers les settings Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'easevent.settings')

app = Celery('easevent')

# Charge toute la config Celery depuis settings.py (CELERY_* keys)
app.config_from_object('django.conf:settings', namespace='CELERY')

# Découvre automatiquement les tâches dans chaque app Django
# (cherche un fichier tasks.py dans chaque app INSTALLED_APPS)
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tâche de diagnostic — vérifie que le worker est opérationnel."""
    print(f'Request: {self.request!r}')
