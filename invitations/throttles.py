"""Limitation de débit des invitations (OWASP API4 : consommation de ressources)."""
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class InviteTokenThrottle(SimpleRateThrottle):
    """Lien d'invitation public : par adresse IP (empêche l'énumération de jetons)."""
    scope = 'invite_token'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class UserSearchThrottle(UserRateThrottle):
    """Recherche de membres et vérification d'emails : par utilisateur."""
    scope = 'user_search'


class InviteSendThrottle(UserRateThrottle):
    """Envoi d'invitations et relances (emails / SMS facturés) : par utilisateur."""
    scope = 'invite_send'
