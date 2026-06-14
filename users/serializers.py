# users/serializers.py
# ════════════════════════════════════════════════════════════════
# Serializers pour l'authentification.
#
# LoginSerializer : valide les données de connexion envoyées
# par le front-end (email + mot de passe) et retourne
# les tokens JWT si les credentials sont corrects.
# ════════════════════════════════════════════════════════════════

from rest_framework import serializers
from django.contrib.auth import authenticate


class LoginSerializer(serializers.Serializer):
    """
    Valide email + mot de passe et authentifie l'utilisateur.

    authenticate() est une fonction Django qui vérifie les
    credentials dans la base de données. Elle retourne l'objet
    User si correct, None si incorrect.
    """

    # EmailField valide le format de l'email automatiquement
    email    = serializers.EmailField()
    password = serializers.CharField(
        # write_only = ce champ n'est jamais retourné dans la réponse
        # On ne renvoie JAMAIS un mot de passe au front-end
        write_only = True,
        style      = {'input_type': 'password'},
    )

    def validate(self, data):
        """
        validate() est appelé automatiquement par DRF.
        C'est ici qu'on vérifie que email + password sont corrects.
        """
        email    = data.get('email')
        password = data.get('password')

        # authenticate() cherche un User avec cet email et vérifie
        # que le hash bcrypt du mot de passe correspond
        user = authenticate(
            request  = self.context.get('request'),
            username = email,    # notre AUTH_USER_MODEL utilise email
            password = password,
        )

        if not user:
            raise serializers.ValidationError(
                # Message volontairement générique — sécurité
                # On ne dit pas si c'est l'email ou le mot de passe
                {'detail': 'Email ou mot de passe incorrect.'}
            )

        if not user.is_verified:
            raise serializers.ValidationError(
                {'detail': 'Veuillez vérifier votre adresse email avant de vous connecter.'}
            )

        if user.deleted_at is not None:
            raise serializers.ValidationError(
                {'detail': 'Ce compte a été supprimé.'}
            )

        # On ajoute l'objet user au dictionnaire validé
        # pour pouvoir y accéder dans la view
        data['user'] = user
        return data