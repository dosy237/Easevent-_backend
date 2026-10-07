"""
invitations/public_views.py
═══════════════════════════════════════════════════════════════
Lien d'invitation pour les personnes sans compte (parcours G, M31).

API (application) :
  GET  /api/invitations/by-token/<token>/          infos de l'invitation (public)
  POST /api/invitations/by-token/<token>/decline/  décliner sans compte (public)
  POST /api/invitations/claim/                     rattacher au compte connecté

Page web (lien reçu par email ou SMS) :
  GET  /i/<token>/   résumé + « Ouvrir dans l'application » (easevent://i/<token>)
  POST /i/<token>/   décliner l'invitation (formulaire protégé CSRF)

Le jeton est la seule preuve : il est comparé par empreinte SHA-256,
les tentatives sont limitées par IP, et une invitation révoquée ou
expirée ne révèle plus rien de l'événement.
═══════════════════════════════════════════════════════════════
"""
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from easevent.media import public_url

from .models import Invitation
from .services import find_by_token, fr_datetime, initials, mask_phone, price_label
from .throttles import InviteTokenThrottle

INVALID = ('Lien invalide', "Ce lien d'invitation est invalide ou a été remplacé par un lien plus récent.", 'invalid')


def _state_error(inv):
    """(titre, message, code) si l'invitation n'est plus utilisable, sinon None."""
    if inv is None:
        return INVALID
    if inv.status == 'revoked':
        return ('Invitation annulée', "L'organisateur a annulé cette invitation.", 'revoked')
    if inv.status == 'expired' or inv.expires_at <= timezone.now():
        return ('Invitation expirée', "Cet événement est terminé : l'invitation n'est plus valable.", 'expired')
    if inv.event.status != 'published':
        return ('Invitation pas encore disponible',
                "L'organisateur prépare encore cet événement. Réessayez un peu plus tard.", 'not_published')
    return None


def _mark_opened(inv):
    if inv.status == 'sent':
        inv.status, inv.opened_at = 'opened', timezone.now()
        inv.save(update_fields=['status', 'opened_at', 'updated_at'])


def _decline(inv):
    from tickets.models import Ticket
    from tickets.services import TicketError, cancel_ticket

    with transaction.atomic():
        for pending in Ticket.objects.filter(invitation=inv, status=Ticket.Status.PENDING):
            try:
                cancel_ticket(pending)
            except TicketError:
                pass
        inv.status, inv.responded_at = 'declined', timezone.now()
        inv.save(update_fields=['status', 'responded_at', 'updated_at'])
    # Organisateur prévenu (réponses groupées), et fil de la conversation pour un membre
    from notifications.services import notify_guest_activity
    notify_guest_activity(inv.event, inv.invited_user, 'declined', name=inv.contact_name or inv.email or None)
    if inv.invited_user_id:
        from messaging.services import record_invitation_event
        record_invitation_event(inv, 'invitation_declined')


def _payload(inv, request):
    from users.models import User

    event, organizer = inv.event, inv.event.organizer
    has_account = bool(inv.invited_user_id) or (
        bool(inv.email) and User.objects.filter(email__iexact=inv.email, is_active=True).exists())
    return {
        'status': inv.status,
        'message': inv.message,
        'expires_at': inv.expires_at.isoformat(),
        'organizer': {
            'first_name': organizer.first_name,
            'last_name':  organizer.last_name,
            'initials':   initials(organizer.first_name, organizer.last_name),
            'avatar_url': organizer.avatar_url,
        },
        'event': {
            'id':                 str(event.id),
            'title':              event.title,
            'start_date':         event.start_date.isoformat(),
            'end_date':           event.end_date.isoformat() if event.end_date else None,
            'location_address':   '' if event.is_online else (event.location_address or ''),
            'is_online':          event.is_online,
            'cover_image':        public_url(event.cover_image, request) if event.cover_image else None,
            'is_paid':            event.is_paid,
            'price':              f"{event.price:.2f}",
            'currency':           event.currency,
            'dress_code':         event.dress_code or '',
            'event_type_display': event.event_type_label or event.get_event_type_display(),
        },
        'invited': {
            'email':       inv.email or '',
            'phone':       mask_phone(inv.phone),
            'name':        inv.contact_name,
            'has_account': has_account,
            'claimed':     bool(inv.invited_user_id),
        },
    }


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([InviteTokenThrottle])
def invitation_by_token(request, token):
    inv = find_by_token(token)
    error = _state_error(inv)
    if error:
        code = status.HTTP_404_NOT_FOUND if error[2] == 'invalid' else status.HTTP_410_GONE
        return Response({'detail': error[1], 'code': error[2], 'title': error[0]}, status=code)
    _mark_opened(inv)
    return Response(_payload(inv, request))


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([InviteTokenThrottle])
def decline_by_token(request, token):
    inv = find_by_token(token)
    error = _state_error(inv)
    if error:
        code = status.HTTP_404_NOT_FOUND if error[2] == 'invalid' else status.HTTP_410_GONE
        return Response({'detail': error[1], 'code': error[2]}, status=code)
    if inv.status != 'declined':
        _decline(inv)
    return Response({'detail': 'Invitation déclinée.', 'status': 'declined'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([InviteTokenThrottle])
def claim_invitation(request):
    """
    Rattache l'invitation du lien au compte connecté (après inscription
    ou connexion depuis M31). L'invitation apparaît ensuite dans
    Mes tickets › En attente, où l'invité l'accepte.
    """
    user = request.user
    inv = find_by_token(str(request.data.get('token', '')))
    error = _state_error(inv)
    if error:
        code = status.HTTP_404_NOT_FOUND if error[2] == 'invalid' else status.HTTP_410_GONE
        return Response({'detail': error[1], 'code': error[2]}, status=code)
    if inv.event.organizer_id == user.id:
        return Response({'detail': "Vous êtes l'organisateur de cet événement.", 'code': 'is_organizer'},
                        status=status.HTTP_400_BAD_REQUEST)
    if inv.invited_user_id and inv.invited_user_id != user.id:
        return Response({'detail': 'Cette invitation est déjà rattachée à un autre compte.', 'code': 'already_claimed'},
                        status=status.HTTP_409_CONFLICT)

    with transaction.atomic():
        existing = (Invitation.objects.select_for_update()
                    .filter(event=inv.event, invited_user=user).exclude(pk=inv.pk)
                    .exclude(status='revoked').first())
        if existing:
            # Déjà invité autrement (ex. par email et par SMS) : une seule invitation
            inv.status = 'revoked'
            inv.save(update_fields=['status', 'updated_at'])
            inv = existing
        elif not inv.invited_user_id:
            inv.invited_user = user
            inv.save(update_fields=['invited_user', 'updated_at'])

    return Response({
        'invitation_id': str(inv.id),
        'event_id':      str(inv.event_id),
        'status':        inv.status,
    })


def claim_for_new_user(token, user):
    """
    Inscription avec invitation_token (users.register_view).
    Retourne True si le jeton a été reçu sur l'adresse de ce compte :
    l'email est alors prouvé, comme avec un lien de vérification.
    """
    inv = find_by_token(token)
    if _state_error(inv) or inv.invited_user_id:
        return False
    proves_email = bool(inv.email) and inv.email.lower() == user.email.lower()
    inv.invited_user = user
    inv.save(update_fields=['invited_user', 'updated_at'])
    return proves_email


# ─────────────────────────────────────────────────────────────
# Page web /i/<token>/
# ─────────────────────────────────────────────────────────────
@csrf_protect
@require_http_methods(['GET', 'POST'])
def invitation_page(request, token):
    inv = find_by_token(token)
    error = _state_error(inv)
    if error:
        response = render(request, 'invitations/landing.html', {'error': error}, status=404 if error[2] == 'invalid' else 410)
    else:
        declined = False
        if request.method == 'POST' and request.POST.get('action') == 'decline':
            if inv.status != 'declined':
                _decline(inv)
            declined = True
        else:
            _mark_opened(inv)
        event = inv.event
        response = render(request, 'invitations/landing.html', {
            'inv': inv,
            'event': event,
            'organizer': event.organizer,
            'organizer_initials': initials(event.organizer.first_name, event.organizer.last_name),
            'cover_url': public_url(event.cover_image, request) if event.cover_image else '',
            'date': fr_datetime(event.start_date),
            'location': 'En ligne' if event.is_online else (event.location_address or ''),
            'price': price_label(event),
            'deeplink': f'easevent://i/{token}',
            'declined': declined or inv.status == 'declined',
            **_app_links(request, token),
        })
    # Le jeton est dans l'URL : ne jamais le transmettre à un autre site
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Cache-Control'] = 'no-store'
    return response


def _platform(request):
    ua = request.META.get('HTTP_USER_AGENT', '').lower()
    if 'android' in ua:
        return 'android'
    if any(k in ua for k in ('iphone', 'ipad', 'ipod')):
        return 'ios'
    return 'other'


def _app_links(request, token):
    """
    Ouvrir l'application si elle est installée, sinon proposer le store.
    Android : lien « intent » (Chrome ouvre l'app, sinon le store). Le jeton
    est transmis au Play Store (paramètre referrer) : l'app le relit à son
    premier lancement et ouvre directement l'invitation.
    iOS : tentative easevent:// puis App Store.
    """
    from urllib.parse import quote
    from django.conf import settings

    platform = _platform(request)
    android_store = settings.ANDROID_STORE_URL
    if android_store:
        sep = '&' if '?' in android_store else '?'
        android_store = f"{android_store}{sep}referrer={quote(f'invite={token}', safe='')}"
    store_url = {'android': android_store, 'ios': settings.IOS_STORE_URL}.get(platform, '') or settings.APP_DOWNLOAD_URL
    open_url = f'easevent://i/{token}'
    if platform == 'android':
        fallback = store_url or request.build_absolute_uri(f'/i/{token}/?app=absente')
        open_url = (f'intent://i/{token}#Intent;scheme=easevent;package={settings.ANDROID_PACKAGE};'
                    f"S.browser_fallback_url={quote(fallback, safe='')};end")
    return {
        'platform': platform,
        'open_url': open_url,
        'store_url': store_url,
        'auto_open': platform in ('android', 'ios') and request.method == 'GET' and request.GET.get('app') != 'absente',
        'app_missing': request.GET.get('app') == 'absente',
    }


def assetlinks(request):
    """/.well-known/assetlinks.json : les liens https://…/i/ s'ouvrent directement dans l'app Android."""
    from django.conf import settings
    from django.http import Http404, JsonResponse
    if not settings.ANDROID_CERT_SHA256:
        raise Http404
    return JsonResponse([{
        'relation': ['delegate_permission/common.handle_all_urls'],
        'target': {'namespace': 'android_app', 'package_name': settings.ANDROID_PACKAGE,
                   'sha256_cert_fingerprints': settings.ANDROID_CERT_SHA256},
    }], safe=False)


def apple_app_site_association(request):
    """/.well-known/apple-app-site-association : Universal Links iOS."""
    from django.conf import settings
    from django.http import Http404, JsonResponse
    if not settings.APPLE_TEAM_ID:
        raise Http404
    return JsonResponse({'applinks': {'apps': [], 'details': [{
        'appID': f'{settings.APPLE_TEAM_ID}.{settings.IOS_BUNDLE_ID}', 'paths': ['/i/*', '/e/*'],
    }]}})


def privacy_page(request):
    """Résumé public de la politique de confidentialité (lien du pied des emails)."""
    return render(request, 'invitations/privacy.html')
