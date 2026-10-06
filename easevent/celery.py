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
from celery.schedules import crontab


# Pointe Celery vers les settings Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'easevent.settings')

app = Celery('easevent')

# Charge toute la config Celery depuis settings.py (CELERY_* keys)
app.config_from_object('django.conf:settings', namespace='CELERY')

# Découvre automatiquement les tâches dans chaque app Django
# (cherche un fichier tasks.py dans chaque app INSTALLED_APPS)
app.autodiscover_tasks()

# ── Planificateur (service « celery-beat » de docker-compose) ─────────
# Les mêmes calculs existent « à la consultation » (notifications/services.py) :
# si le planificateur s'arrête, l'application reste juste.
app.conf.beat_schedule = {
    # Rappels J-7 / J-1 / jour J (+ email la veille)
    'rappels-evenements': {
        'task': 'notifications.tasks.send_event_reminders',
        'schedule': crontab(minute=5),                      # toutes les heures
    },
    # « Bilan du jour · 20:00 » pour les organisateurs
    'bilan-du-jour': {
        'task': 'notifications.tasks.send_daily_summaries',
        'schedule': crontab(hour=20, minute=0),
    },
    # Invitations restées « en cours d'envoi » (worker redémarré…)
    'invitations-en-attente-envoi': {
        'task': 'invitations.tasks.retry_stuck_deliveries',
        'schedule': crontab(minute='*/10'),
    },
    # Ménage de nuit : tickets et invitations expirés, notifications > 90 jours
    'menage-de-nuit': {
        'task': 'notifications.tasks.nightly_cleanup',
        'schedule': crontab(hour=3, minute=30),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tâche de diagnostic — vérifie que le worker est opérationnel."""
    print(f'Request: {self.request!r}')
