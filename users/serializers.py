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

        if user.deleted_at is not None:
            raise serializers.ValidationError(
                # Même message qu'un mauvais mot de passe : on ne
                # révèle pas l'existence d'un compte supprimé
                {'detail': 'Email ou mot de passe incorrect.'}
            )

        if not user.is_verified:
            # code → le front-end ouvre l'écran M03 « Vérifiez votre email »
            raise serializers.ValidationError({
                'detail': 'Veuillez vérifier votre adresse email avant de vous connecter.',
                'code':   'email_not_verified',
                'email':  user.email,
            })

        # On ajoute l'objet user au dictionnaire validé
        # pour pouvoir y accéder dans la view
        data['user'] = user
        return data

# ════════════════════════════════════════════════════════════════
# Représentation unique d'un utilisateur renvoyée au front-end.
# Un seul endroit → impossible d'oublier un champ ou d'en exposer
# un sensible (stripe_customer_id, oauth_uid…) par erreur.
# ════════════════════════════════════════════════════════════════
def user_payload(user):
    return {
        'id':                  str(user.id),
        'email':               user.email,
        'first_name':          user.first_name,
        'last_name':           user.last_name,
        'avatar_url':          user.avatar_url,
        'bio':                 user.bio,
        'subscription_plan':   user.subscription_plan,
        'is_verified':         user.is_verified,
        'marketing_opt_in':    user.marketing_opt_in,
        'accepted_privacy_at': user.accepted_privacy_at.isoformat() if user.accepted_privacy_at else None,
        # Téléphone masqué ; « phone_verified » faux = numéro saisi mais pas encore confirmé
        'phone':               _masked_phone(user),
        'phone_verified':      user.phone_verified_at is not None,
        # Accès au tableau de bord de l'équipe (le serveur revérifie chaque appel)
        'is_staff':            user.is_staff,
        'is_superuser':        user.is_superuser,
    }


def _masked_phone(user):
    from .phone import masked
    return masked(user)


def _clean_name(value, label):
    value = (value or '').strip()
    if not value:
        raise serializers.ValidationError(f'Le {label} est requis.')
    if any(ch in value for ch in '<>{}'):
        raise serializers.ValidationError(f'Le {label} contient des caractères non autorisés.')
    return value


class RegisterSerializer(serializers.Serializer):
    """
    Inscription — POST /api/auth/register/

    accepted_privacy est OBLIGATOIRE (case de consentement M01).
    La date de consentement est fixée par le serveur : on ne fait
    pas confiance à une date envoyée par le client.
    """
    email            = serializers.EmailField(max_length=254)
    password         = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)
    first_name       = serializers.CharField(max_length=50)
    last_name        = serializers.CharField(max_length=50)
    accepted_privacy = serializers.BooleanField(required=False, default=False)
    # Compatibilité : le MD prévoit l'envoi de accepted_privacy_at
    accepted_privacy_at = serializers.DateTimeField(required=False, allow_null=True)
    marketing_opt_in = serializers.BooleanField(required=False, default=False)
    invitation_token = serializers.CharField(required=False, allow_blank=True, max_length=64)
    # Obligatoire : relie le compte aux invitations reçues par SMS (vérifié par code)
    phone_number     = serializers.CharField(max_length=30, error_messages={
        'required': 'Le numéro de téléphone est requis.', 'blank': 'Le numéro de téléphone est requis.'})
    # Canal du code de vérification choisi par l'utilisateur
    verification_channel = serializers.ChoiceField(choices=['email', 'sms'], required=False, default='email')

    def validate_phone_number(self, value):
        from invitations.services import normalize_phone
        e164 = normalize_phone(value)
        if not e164:
            raise serializers.ValidationError('Numéro invalide : indiquez-le avec son indicatif (ex. +33 6 12 34 56 78).')
        return e164

    def validate_email(self, value):
        from .models import User
        value = User.objects.normalize_email(value).strip()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Un compte existe déjà avec cette adresse email.')
        return value

    def validate_first_name(self, value):
        return _clean_name(value, 'prénom')

    def validate_last_name(self, value):
        return _clean_name(value, 'nom')

    def validate(self, data):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .models import User

        if not (data.get('accepted_privacy') or data.get('accepted_privacy_at')):
            raise serializers.ValidationError({
                'accepted_privacy': "Vous devez accepter la politique de confidentialité pour créer un compte."
            })

        candidate = User(
            email      = data['email'],
            first_name = data['first_name'],
            last_name  = data['last_name'],
        )
        try:
            validate_password(data['password'], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})
        return data


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid          = serializers.CharField(max_length=64)
    token        = serializers.CharField(max_length=128)
    new_password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)
