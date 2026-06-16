# users/views.py
import secrets
from datetime import timedelta

from django.core.mail import send_mail
from django.conf      import settings
from django.utils     import timezone
from django.template.loader import render_to_string
from django.shortcuts import render

from rest_framework.decorators  import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response    import Response
from rest_framework             import status
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers        import LoginSerializer
from .models             import User, EmailVerification



def get_tokens_for_user(user):
    """Génère access + refresh token JWT pour un utilisateur."""
    refresh = RefreshToken.for_user(user)
    return {
        'access':  str(refresh.access_token),
        'refresh': str(refresh),
    }


def send_verification_email(user, token, request=None):
    if request:
        verification_url = request.build_absolute_uri(f"/api/auth/verify/{token}/")
    else:
        base = getattr(settings, 'BASE_URL', 'http://127.0.0.1:8003')
        # strip trailing slash if present
        base = base.rstrip('/')
        verification_url = f"{base}/api/auth/verify/{token}/"

    context = {
        'user': user,
        'verification_url': verification_url,
    }

    # Render HTML template
    html_content = render_to_string('users/emails/verify_email.html', context)

    # plain text fallback (for email clients that don't support HTML)
    text_content = f"""
Bonjour {user.first_name},

Merci de vous être inscrit sur Easevent !

Cliquez sur ce lien pour activer votre compte :
{verification_url}

Ce lien expire dans 24 heures.

L'équipe Easevent
    """

    send_mail(
        subject       = '✅ Confirmez votre adresse email — Easevent',
        message       = text_content,
        html_message  = html_content,
        from_email    = settings.DEFAULT_FROM_EMAIL,
        recipient_list= [user.email],
        fail_silently = False,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """POST /api/auth/login/"""
    serializer = LoginSerializer(
        data    = request.data,
        context = {'request': request},
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user   = serializer.validated_data['user']
    tokens = get_tokens_for_user(user)

    return Response({
        'access':  tokens['access'],
        'refresh': tokens['refresh'],
        'user': {
            'id':                str(user.id),
            'email':             user.email,
            'first_name':        user.first_name,
            'last_name':         user.last_name,
            'avatar_url':        user.avatar_url,
            'bio':         user.bio,          # ← AJOUTER
            'subscription_plan': user.subscription_plan,
            'is_verified':       user.is_verified,
        }
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def register_view(request):
    """POST /api/auth/register/"""
    data = request.data

    if User.objects.filter(email=data.get('email', '')).exists():
        return Response(
            {'email': 'Un compte existe déjà avec cette adresse email.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        user = User.objects.create_user(
            email      = data['email'],
            password   = data['password'],
            first_name = data.get('first_name', ''),
            last_name  = data.get('last_name', ''),
            is_verified= False,
        )

        # Générer le token de vérification
        # secrets.token_urlsafe(32) = 43 caractères aléatoires sécurisés
        token = secrets.token_urlsafe(32)

        EmailVerification.objects.create(
            user       = user,
            token      = token,
            expires_at = timezone.now() + timedelta(hours=24),
        )

        # Envoyer l'email de vérification
        send_verification_email(user, token, request)

        tokens = get_tokens_for_user(user)

        return Response({
            'access':  tokens['access'],
            'refresh': tokens['refresh'],
            'message': 'Compte créé. Vérifiez votre email pour activer votre compte.',
            'user': {
                'id':          str(user.id),
                'email':       user.email,
                'first_name':  user.first_name,
                'last_name':   user.last_name,
                'bio':         user.bio,          # ← AJOUTER
                'is_verified': False,
            }
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


def verify_email_view(request, token):
    """
    GET /api/auth/verify/<token>/
    Activé quand l'utilisateur clique sur le lien dans son email.
    """
    try:
        verification = EmailVerification.objects.get(token=token)
    except EmailVerification.DoesNotExist:
        return render(request, 'users/verify_result.html', {
            'status': 'error',
            'title': 'Lien invalide',
            'message': "Ce lien de vérification est invalide ou n'existe plus."
        })

    if verification.is_expired():
        return render(request, 'users/verify_result.html', {
            'status': 'error',
            'title': 'Lien expiré',
            'message': 'Ce lien a expiré. Veuillez demander un nouveau lien de vérification.'
        })

    # Activer le compte
    user = verification.user
    user.is_verified = True
    user.save()

    # Supprimer le token — il ne peut servir qu'une fois
    verification.delete()

    return render(request, 'users/verify_result.html', {
        'status': 'success',
        'title': 'Email vérifié !',
        'message': 'Votre adresse email a été vérifiée avec succès. Vous pouvez maintenant fermer cette page et retourner sur l\'application pour vous connecter.'
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def resend_verification_view(request):
    """
    POST /api/auth/resend-verification/
    Renvoie l'email de vérification si le lien a expiré.
    """
    email = request.data.get('email')
    try:
        user = User.objects.get(email=email, is_verified=False)
    except User.DoesNotExist:
        # Réponse générique — ne pas révéler si l'email existe
        return Response({'detail': 'Si cet email existe, un nouveau lien a été envoyé.'})

    # Supprimer l'ancien token s'il existe
    EmailVerification.objects.filter(user=user).delete()

    # Créer un nouveau token
    token = secrets.token_urlsafe(32)
    EmailVerification.objects.create(
        user       = user,
        token      = token,
        expires_at = timezone.now() + timedelta(hours=24),
    )

    send_verification_email(user, token, request)

    return Response({'detail': 'Si cet email existe, un nouveau lien a été envoyé.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    """GET /api/auth/me/ — infos de l'utilisateur connecté."""
    user = request.user
    return Response({
        'id':                str(user.id),
        'email':             user.email,
        'first_name':        user.first_name,
        'last_name':         user.last_name,
        'avatar_url':        user.avatar_url,
        'bio':               user.bio,
        'subscription_plan': user.subscription_plan,
        'is_verified':       user.is_verified,
    })
@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_profile_view(request):
    """
    PUT /api/auth/me/update/
    Modifie le profil de l'utilisateur connecté.
    PUT = remplace tout, PATCH = modifie partiellement.
    On accepte les deux pour la flexibilité.
    """
    user = request.user
    data = request.data

    # On met à jour uniquement les champs envoyés
    if 'first_name' in data:
        user.first_name = data['first_name'].strip()
    if 'last_name' in data:
        user.last_name = data['last_name'].strip()
    if 'bio' in data:
        # bio limitée à 250 caractères comme dans le cahier des charges
        user.bio = data['bio'][:250]
    if 'avatar_url' in data:
        user.avatar_url = data['avatar_url']

    user.save()

    return Response({
        'id':                str(user.id),
        'email':             user.email,
        'first_name':        user.first_name,
        'last_name':         user.last_name,
        'avatar_url':        user.avatar_url,
        'bio':               user.bio,
        'subscription_plan': user.subscription_plan,
        'is_verified':       user.is_verified,
    })
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    """
    POST /api/auth/change-password/
    Body : { "old_password": "...", "new_password": "..." }
    """
    user         = request.user
    old_password = request.data.get('old_password', '')
    new_password = request.data.get('new_password', '')

    # Vérifier l'ancien mot de passe
    if not user.check_password(old_password):
        return Response(
            {'detail': 'Mot de passe actuel incorrect.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Valider le nouveau mot de passe
    if len(new_password) < 8:
        return Response(
            {'detail': 'Le nouveau mot de passe doit contenir au moins 8 caractères.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # set_password hache automatiquement le nouveau mot de passe
    user.set_password(new_password)
    user.save()

    return Response({'detail': 'Mot de passe modifié avec succès.'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def delete_account_view(request):
    """
    POST /api/auth/delete-account/
    Body : { "password": "..." }
    Suppression RGPD — confirmation par mot de passe obligatoire.
    Délai de grâce de 7 jours selon le cahier des charges US-05.
    """
    user     = request.user
    password = request.data.get('password', '')

    # Confirmation par mot de passe — sécurité obligatoire
    if not user.check_password(password):
        return Response(
            {'detail': 'Mot de passe incorrect.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Suppression immédiate des données personnelles (RGPD)
    user.email      = f'deleted_{user.id}@deleted.easevent'
    user.first_name = 'Utilisateur'
    user.last_name  = 'Supprimé'
    user.avatar_url = None
    user.bio        = ''
    user.is_active  = False

    # Soft delete — on horodate sans supprimer la ligne
    from django.utils import timezone
    user.deleted_at = timezone.now()
    user.save()

    # Invalider tous les tokens
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
    try:
        tokens = OutstandingToken.objects.filter(user=user)
        for token in tokens:
            BlacklistedToken.objects.get_or_create(token=token)
    except Exception:
        pass

    return Response({'detail': 'Compte supprimé avec succès.'})