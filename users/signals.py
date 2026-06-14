"""
users/signals.py
═══════════════════════════════════════════════════════════════
Les signals Django sont des "abonnements automatiques" à des
événements internes de Django.

Ici on s'abonne à l'événement post_save sur le modèle User :
→ Chaque fois qu'un User est créé (et seulement créé, pas
  mis à jour), on crée automatiquement ses UserPreferences.

Sans ce signal, il faudrait appeler manuellement
UserPreferences.objects.create(user=...) à chaque endroit
où on crée un utilisateur. Avec le signal, c'est garanti
de façon centralisée — impossible d'oublier.
═══════════════════════════════════════════════════════════════
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import User, UserPreferences


@receiver(post_save, sender=User)
def create_user_preferences(sender, instance, created, **kwargs):
    """
    Se déclenche automatiquement après chaque sauvegarde d'un User.

    Paramètres :
    - sender   : la classe qui a envoyé le signal (User)
    - instance : l'objet User qui vient d'être sauvegardé
    - created  : True si c'est une création, False si c'est une mise à jour
    - kwargs   : autres paramètres (on n'en a pas besoin ici)
    """
    if created:
        # get_or_create évite les doublons si le signal se déclenche deux fois
        # (ce qui peut arriver dans certaines configurations de tests)
        UserPreferences.objects.get_or_create(user=instance)
