# users/views.py
# ════════════════════════════════════════════════════════════════
# Authentification et compte utilisateur.
#
# Endpoints :
#   POST /api/auth/login/                      → connexion
#   POST /api/auth/register/                   → inscription (+ consentement)
#   GET  /api/auth/verify/<token>/             → lien email (page HTML)
#   POST /api/auth/verify-email/               → vérification depuis l'app (connecte)
#   POST /api/auth/resend-verification/        → renvoyer le lien
#   POST /api/auth/password-reset/             → demander un lien (1 h)
#   POST /api/auth/password-reset/confirm/     → choisir un nouveau mot de passe
#   POST /api/auth/logout/                     → invalider le refresh token
#   GET  /api/auth/me/   PATCH /api/auth/me/update/
#   GET  /api/auth/me/stats/                   → compteurs du profil
#   GET  /api/auth/me/export/                  → export RGPD (Art. 20)
#   POST /api/auth/change-password/
#   POST /api/auth/delete-account/
#
# Sécurité (OWASP) :
#   - réponses génériques sur les flux email (pas d'énumération) ;
#   - limitation de débit par portée (settings.DEFAULT_THROTTLE_RATES) ;
#   - jetons email hachés en base, à usage unique ;
#   - aucune exception interne renvoyée au client.
# ════════════════════════════════════════════════════════════════

import logging

from django.conf                    import settings
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens     import default_token_generator
from django.core.exceptions         import ValidationError as DjangoValidationError
from django.core.mail               import send_mail
from django.db                      import transaction
from django.shortcuts               import render
from django.template.loader         import render_to_string
from django.utils                   import timezone
from django.utils.encoding          import force_bytes, force_str
from django.utils.http              import urlsafe_base64_encode, urlsafe_base64_decode

from rest_framework                 import status
from rest_framework.decorators      import api_view, permission_classes, throttle_classes
from rest_framework.permissions     import AllowAny, IsAuthenticated
from rest_framework.response        import Response
from rest_framework.throttling      import SimpleRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .models      import User
from .serializers import (
    LoginSerializer, RegisterSerializer,
    PasswordResetRequestSerializer, PasswordResetConfirmSerializer,
    user_payload,
)
from .tokens      import create_email_verification, find_email_verification

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Limitation de débit par portée (voir settings.DEFAULT_THROTTLE_RATES)
# ─────────────────────────────────────────────────────────────────
class LoginThrottle(SimpleRateThrottle):
    """Limite par adresse IP, avec un quota propre à chaque flux sensible."""
    scope = 'auth_login'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class RegisterThrottle(LoginThrottle):
    scope = 'auth_register'


class EmailThrottle(LoginThrottle):
    scope = 'auth_email'


class PasswordResetThrottle(LoginThrottle):
    scope = 'password_reset'


# ─────────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────────
def get_tokens_for_user(user):
    """Génère access + refresh token JWT pour un utilisateur."""
    refresh = RefreshToken.for_user(user)
    return {
        'access':  str(refresh.access_token),
        'refresh': str(refresh),
    }


def _first_errors(errors):
    """{'champ': ['msg']} → {'champ': 'msg'} — plus simple à afficher côté app."""
    flat = {}
    for key, value in errors.items():
        if isinstance(value, (list, tuple)) and value:
            flat[key] = str(value[0])
        elif isinstance(value, dict):
            flat[key] = _first_errors(value)
        else:
            flat[key] = str(value)
    return flat


def _frontend_link(path, fallback):
    """Lien vers l'application si FRONTEND_URL est configuré, sinon page backend."""
    base = getattr(settings, 'FRONTEND_URL', '')
    return f"{base}{path}" if base else fallback


def _backend_url(request, path):
    if request is not None:
        return request.build_absolute_uri(path)
    return f"{settings.BASE_URL.rstrip('/')}{path}"


def send_verification_email(user, token, request=None):
    verification_url = _frontend_link(
        f"/verify/{token}",
        _backend_url(request, f"/api/auth/verify/{token}/"),
    )
    context = {'user': user, 'verification_url': verification_url}
    html_content = render_to_string('users/emails/verify_email.html', context)
    text_content = (
        f"Bonjour {user.first_name},\n\n"
        "Merci de vous être inscrit sur Easevent.\n\n"
        f"Confirmez votre adresse email en ouvrant ce lien :\n{verification_url}\n\n"
        "Ce lien expire dans 24 heures.\n\n"
        "L'équipe Easevent"
    )
    send_mail(
        subject        = 'Confirmez votre adresse email — Easevent',
        message        = text_content,
        html_message   = html_content,
        from_email     = settings.DEFAULT_FROM_EMAIL,
        recipient_list = [user.email],
        fail_silently  = False,
    )


def send_password_reset_email(user, request=None):
    uid   = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_url = _frontend_link(
        f"/reset-password/{uid}/{token}",
        _backend_url(request, f"/api/auth/password-reset/{uid}/{token}/"),
    )
    context = {'user': user, 'reset_url': reset_url}
    html_content = render_to_string('users/emails/password_reset.html', context)
    text_content = (
        f"Bonjour {user.first_name},\n\n"
        "Vous avez demandé à réinitialiser votre mot de passe Easevent.\n\n"
        f"Choisissez un nouveau mot de passe en ouvrant ce lien :\n{reset_url}\n\n"
        "Ce lien expire dans 1 heure. Si vous n'êtes pas à l'origine de cette "
        "demande, ignorez cet email : votre mot de passe reste inchangé.\n\n"
        "L'équipe Easevent"
    )
    send_mail(
        subject        = 'Réinitialisez votre mot de passe — Easevent',
        message        = text_content,
        html_message   = html_content,
        from_email     = settings.DEFAULT_FROM_EMAIL,
        recipient_list = [user.email],
        fail_silently  = False,
    )


def _apply_new_password(user, raw_password):
    """Enregistre le mot de passe et ferme toutes les sessions."""
    user.set_password(raw_password)
    # Recevoir le lien par email prouve la possession de l'adresse
    user.is_verified = True
    user.save()
    _blacklist_all_tokens(user)


def _blacklist_all_tokens(user):
    """Invalide toutes les sessions (refresh tokens) d'un utilisateur."""
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def _verify_user(verification):
    user = verification.user
    user.is_verified = True
    user.save(update_fields=['is_verified', 'updated_at'])
    verification.delete()   # usage unique
    return user


# ════════════════════════════════════════════════════════════════
# CONNEXION / INSCRIPTION
# ════════════════════════════════════════════════════════════════
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def login_view(request):
    """POST /api/auth/login/"""
    serializer = LoginSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return Response(_first_errors(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

    user   = serializer.validated_data['user']
    tokens = get_tokens_for_user(user)
    return Response({
        'access':  tokens['access'],
        'refresh': tokens['refresh'],
        'user':    user_payload(user),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegisterThrottle])
def register_view(request):
    """
    POST /api/auth/register/
    Body : email, password, first_name, last_name,
           accepted_privacy (obligatoire), marketing_opt_in, invitation_token?

    Le compte est créé non vérifié : aucun jeton de session n'est
    renvoyé. L'application affiche M03 « Vérifiez votre email ».
    """
    serializer = RegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(_first_errors(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    with transaction.atomic():
        user = User.objects.create_user(
            email               = data['email'],
            password            = data['password'],
            first_name          = data['first_name'],
            last_name           = data['last_name'],
            is_verified         = False,
            accepted_privacy_at = timezone.now(),
            marketing_opt_in    = data.get('marketing_opt_in', False),
        )
        # Lien d'invitation (M31) : rattachement automatique. Si le lien a été
        # reçu sur cette adresse, l'email est prouvé : connexion immédiate.
        invitation_token = (data.get('invitation_token') or '').strip()
        if invitation_token:
            from invitations.public_views import claim_for_new_user
            if claim_for_new_user(invitation_token, user):
                user.is_verified = True
                user.save(update_fields=['is_verified', 'updated_at'])
        token = None if user.is_verified else create_email_verification(user)

    if user.is_verified:
        tokens = get_tokens_for_user(user)
        return Response({
            'message':               'Compte créé.',
            'requires_verification': False,
            'access':                tokens['access'],
            'refresh':               tokens['refresh'],
            'email':                 user.email,
            'user':                  user_payload(user),
            'invitation_claimed':    True,
        }, status=status.HTTP_201_CREATED)

    try:
        send_verification_email(user, token, request)
    except Exception:
        # Le compte existe : l'utilisateur pourra redemander le lien depuis M03
        logger.exception("Échec d'envoi de l'email de vérification")

    return Response({
        'message':               'Compte créé. Vérifiez votre email pour activer votre compte.',
        'requires_verification': True,
        'email':                 user.email,
        'user':                  user_payload(user),
    }, status=status.HTTP_201_CREATED)


# ════════════════════════════════════════════════════════════════
# VÉRIFICATION DE L'EMAIL
# ════════════════════════════════════════════════════════════════
def verify_email_view(request, token):
    """
    GET /api/auth/verify/<token>/
    Page HTML affichée quand le lien est ouvert hors de l'application.
    """
    verification = find_email_verification(token)
    if verification is None:
        return render(request, 'users/verify_result.html', {
            'status':  'error',
            'title':   'Lien invalide',
            'message': "Ce lien de vérification est invalide ou a déjà été utilisé.",
        })
    if verification.is_expired():
        return render(request, 'users/verify_result.html', {
            'status':  'error',
            'title':   'Lien expiré',
            'message': "Ce lien a expiré. Demandez un nouveau lien depuis l'application.",
        })

    _verify_user(verification)
    return render(request, 'users/verify_result.html', {
        'status':  'success',
        'title':   'Email vérifié',
        'message': "Votre adresse email est vérifiée. Vous pouvez retourner sur l'application pour vous connecter.",
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def verify_email_api_view(request):
    """
    POST /api/auth/verify-email/   Body : { "token": "..." }
    Appelé par l'application quand le lien /verify/:token l'ouvre.
    Valide l'adresse puis connecte l'utilisateur.
    """
    verification = find_email_verification(str(request.data.get('token', '')))
    if verification is None:
        return Response(
            {'detail': 'Ce lien est invalide ou a déjà été utilisé.', 'code': 'invalid_token'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if verification.is_expired():
        return Response(
            {'detail': 'Ce lien a expiré. Demandez un nouveau lien.', 'code': 'expired_token',
             'email': verification.user.email},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = _verify_user(verification)
    if not user.is_active or user.is_deleted:
        return Response({'detail': 'Ce lien est invalide.', 'code': 'invalid_token'},
                        status=status.HTTP_400_BAD_REQUEST)

    tokens = get_tokens_for_user(user)
    return Response({
        'access':  tokens['access'],
        'refresh': tokens['refresh'],
        'user':    user_payload(user),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([EmailThrottle])
def resend_verification_view(request):
    """
    POST /api/auth/resend-verification/   Body : { "email": "..." }
    Réponse identique que le compte existe ou non (pas d'énumération).
    """
    generic = {'detail': 'Si un compte non vérifié existe pour cet email, un nouveau lien a été envoyé.'}
    email = str(request.data.get('email', '')).strip()
    user = User.objects.filter(
        email__iexact=email, is_verified=False, is_active=True, deleted_at__isnull=True,
    ).first() if email else None

    if user is not None:
        token = create_email_verification(user)
        try:
            send_verification_email(user, token, request)
        except Exception:
            logger.exception("Échec du renvoi de l'email de vérification")
    return Response(generic)


# ════════════════════════════════════════════════════════════════
# MOT DE PASSE OUBLIÉ
# ════════════════════════════════════════════════════════════════
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetThrottle])
def password_reset_request_view(request):
    """
    POST /api/auth/password-reset/   Body : { "email": "..." }
    Envoie un lien valable 1 heure. Réponse toujours identique.
    """
    serializer = PasswordResetRequestSerializer(data=request.data)
    generic = {'detail': 'Si un compte existe pour cet email, un lien de réinitialisation a été envoyé.'}
    if not serializer.is_valid():
        return Response(_first_errors(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.filter(
        email__iexact=serializer.validated_data['email'],
        is_active=True, deleted_at__isnull=True,
    ).first()
    if user is not None:
        try:
            send_password_reset_email(user, request)
        except Exception:
            logger.exception("Échec d'envoi de l'email de réinitialisation")
    return Response(generic)


def _user_from_uid(uid):
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        return User.objects.get(pk=pk, is_active=True, deleted_at__isnull=True)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist, DjangoValidationError):
        return None


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetThrottle])
def password_reset_confirm_view(request):
    """
    POST /api/auth/password-reset/confirm/
    Body : { "uid": "...", "token": "...", "new_password": "..." }

    Le jeton Django devient invalide dès que le mot de passe change
    (usage unique) ou après PASSWORD_RESET_TIMEOUT (1 h).
    Toutes les sessions ouvertes sont fermées.
    """
    serializer = PasswordResetConfirmSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(_first_errors(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    user = _user_from_uid(data['uid'])
    if user is None or not default_token_generator.check_token(user, data['token']):
        return Response(
            {'detail': 'Ce lien est invalide ou a expiré. Demandez un nouveau lien.', 'code': 'invalid_token'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        validate_password(data['new_password'], user=user)
    except DjangoValidationError as exc:
        return Response({'new_password': exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

    _apply_new_password(user, data['new_password'])
    return Response({'detail': 'Mot de passe modifié. Vous pouvez vous connecter.'})


def password_reset_page_view(request, uid, token):
    """
    GET/POST /api/auth/password-reset/<uid>/<token>/
    Page web ouverte depuis le lien de l'email (l'application est un APK :
    il n'y a pas de site web pour accueillir le lien). Formulaire protégé
    par CSRF ; mêmes règles que l'endpoint /confirm/.
    """
    from django.templatetags.static import static
    from django.views.decorators.csrf import csrf_protect

    @csrf_protect
    def handle(request):
        context = {'logo_url': static('app/logo.svg'), 'status': 'form'}
        user = _user_from_uid(uid)
        if user is None or not default_token_generator.check_token(user, token):
            context['status'] = 'invalid'
            return render(request, 'users/password_reset_page.html', context)

        if request.method == 'POST':
            password = request.POST.get('new_password', '')
            if password != request.POST.get('confirm_password', ''):
                context['error'] = 'Les deux mots de passe ne correspondent pas.'
            else:
                try:
                    validate_password(password, user=user)
                except DjangoValidationError as exc:
                    context['error'] = exc.messages[0]
                else:
                    _apply_new_password(user, password)
                    context['status'] = 'done'
        response = render(request, 'users/password_reset_page.html', context)
        # Le jeton est dans l'URL : il n'est jamais transmis à un autre site.
        # (no-referrer ferait envoyer « Origin: null » et bloquerait le CSRF)
        response['Referrer-Policy'] = 'same-origin'
        response['Cache-Control'] = 'no-store'
        return response

    return handle(request)


# ════════════════════════════════════════════════════════════════
# SESSION
# ════════════════════════════════════════════════════════════════
@api_view(['POST'])
@permission_classes([AllowAny])
def logout_view(request):
    """
    POST /api/auth/logout/   Body : { "refresh": "..." }
    Ajoute le refresh token à la liste noire : il ne pourra plus
    servir à obtenir de nouveaux access tokens.
    """
    raw = request.data.get('refresh')
    if raw:
        try:
            RefreshToken(raw).blacklist()
        except TokenError:
            pass   # déjà expiré ou invalide : rien à faire
    return Response(status=status.HTTP_204_NO_CONTENT)


# ════════════════════════════════════════════════════════════════
# PROFIL
# ════════════════════════════════════════════════════════════════
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    """GET /api/auth/me/ — infos de l'utilisateur connecté."""
    return Response(user_payload(request.user))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_stats_view(request):
    """GET /api/auth/me/stats/ — compteurs affichés sur le profil (E12)."""
    from events.models      import Event
    from invitations.models import Invitation

    events_count = Event.objects.filter(organizer=request.user, deleted_at__isnull=True).count()
    participations = Invitation.objects.filter(invited_user=request.user, status='confirmed').count()
    return Response({
        'events_count':         events_count,
        'participations_count': participations,
        'subscription_plan':    request.user.subscription_plan,
    })


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_profile_view(request):
    """
    PATCH /api/auth/me/update/
    Champs modifiables : first_name, last_name, bio, avatar_url, marketing_opt_in.
    """
    user = request.user
    data = request.data
    errors = {}

    for field, label in (('first_name', 'prénom'), ('last_name', 'nom')):
        if field in data:
            value = str(data[field] or '').strip()
            if not value:
                errors[field] = f'Le {label} est requis.'
            elif len(value) > 50 or any(ch in value for ch in '<>{}'):
                errors[field] = f'Le {label} est invalide.'
            else:
                setattr(user, field, value)

    if 'bio' in data:
        user.bio = str(data['bio'] or '')[:250]

    if 'avatar_url' in data:
        url = data['avatar_url']
        if url in (None, ''):
            user.avatar_url = None
        elif isinstance(url, str) and url.startswith('https://') and len(url) <= 512:
            user.avatar_url = url
        else:
            errors['avatar_url'] = "L'URL de la photo doit commencer par https://."

    if 'marketing_opt_in' in data:
        user.marketing_opt_in = bool(data['marketing_opt_in'])

    if errors:
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    user.save()
    return Response(user_payload(user))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_data_view(request):
    """
    GET /api/auth/me/export/
    Portabilité des données (RGPD Art. 20) : tout ce qui concerne
    l'utilisateur, dans un format lisible par machine (JSON).
    """
    from events.models      import Event
    from invitations.models import Invitation

    user = request.user
    prefs = getattr(user, 'preferences', None)
    events = Event.objects.filter(organizer=user, deleted_at__isnull=True).order_by('created_at')
    invitations = (
        Invitation.objects.filter(invited_user=user)
        .select_related('event').order_by('sent_at')
    )

    export = {
        'exported_at': timezone.now().isoformat(),
        'account': {
            **user_payload(user),
            'created_at': user.created_at.isoformat(),
        },
        'preferences': {
            field.name: getattr(prefs, field.name)
            for field in prefs._meta.fields
            if field.name not in ('id', 'user', 'created_at', 'updated_at')
        } if prefs else {},
        'events_organized': [
            {
                'id':               str(e.id),
                'title':            e.title,
                'event_type':       e.event_type,
                'description':      e.description,
                'start_date':       e.start_date.isoformat(),
                'end_date':         e.end_date.isoformat(),
                'location_address': e.location_address,
                'visibility':       e.visibility,
                'status':           e.status,
                'created_at':       e.created_at.isoformat(),
            } for e in events
        ],
        'invitations_received': [
            {
                'event':        inv.event.title,
                'status':       inv.status,
                'sent_at':      inv.sent_at.isoformat(),
                'responded_at': inv.responded_at.isoformat() if inv.responded_at else None,
            } for inv in invitations
        ],
    }
    response = Response(export)
    response['Content-Disposition'] = 'attachment; filename="easevent-mes-donnees.json"'
    return response


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

    if not user.check_password(old_password):
        return Response({'detail': 'Mot de passe actuel incorrect.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as exc:
        return Response({'detail': exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

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
    """
    user     = request.user
    password = request.data.get('password', '')

    if not user.check_password(password):
        return Response({'detail': 'Mot de passe incorrect.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Anonymisation immédiate des données personnelles (RGPD)
    user.email            = f'deleted_{user.id}@deleted.easevent'
    user.first_name       = 'Utilisateur'
    user.last_name        = 'Supprimé'
    user.avatar_url       = None
    user.bio              = ''
    user.marketing_opt_in = False
    user.is_active        = False
    user.deleted_at       = timezone.now()   # soft delete
    user.set_unusable_password()
    user.save()

    _blacklist_all_tokens(user)
    return Response({'detail': 'Compte supprimé avec succès.'})
