"""
users/apps.py
═══════════════════════════════════════════════════════════════
Fichier de configuration de l'application "users".

La méthode ready() est appelée UNE SEULE FOIS au démarrage
de Django. C'est ici qu'on active les signals.

IMPORTANT : si on importait les signals dans models.py ou
ailleurs, ils pourraient s'activer plusieurs fois et causer
des bugs. La méthode ready() de AppConfig est l'endroit
officiel recommandé par Django pour ça.
═══════════════════════════════════════════════════════════════
"""

from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        """Appelé au démarrage — active les signals."""
        import users.signals  # noqa: F401
