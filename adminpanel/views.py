"""
adminpanel/views.py — tableau de bord de l'équipe Easevent (comptes « is_staff » uniquement)
════════════════════════════════════════════════════════════════
GET    /api/admin/stats/                      chiffres clés
GET    /api/admin/users/?q=&page=             comptes            POST : créer (lien pour choisir le mot de passe)
PATCH  /api/admin/users/<id>/                 nom, plan, suspension, rôle d'administrateur (super-admin seulement)
DELETE /api/admin/users/<id>/                 suppression RGPD (événements annulés, invités prévenus et remboursés)
GET    /api/admin/events/?q=&status=&page=    événements
PATCH  /api/admin/events/<id>/                titre, publication (modération), visibilité
DELETE /api/admin/events/<id>/                annulation (invités prévenus, paiements remboursés)
GET    /api/admin/announcements/              annonces            POST : créer
PATCH  /api/admin/announcements/<id>/         modifier            DELETE : supprimer
GET    /api/announcements/                    annonces en cours (tous, en tête du fil)
Toute modification est tracée (AdminAction).
════════════════════════════════════════════════════════════════
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator, validate_email
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from events import video as event_video
from events.models import Event
from messaging.realtime import broadcast_public
from tickets.models import Ticket

from .models import AdminAction, Announcement

logger = logging.getLogger(__name__)
User = get_user_model()
PAGE = 30


class AdminThrottle(UserRateThrottle):
    scope = 'admin'


def _log(request, action, target_type, target_id, **detail):
    AdminAction.objects.create(actor=request.user, action=action, target_type=target_type,
                               target_id=str(target_id), detail=detail)


def _page(request, qs):
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except ValueError:
        page = 1
    total = qs.count()
    return qs[(page - 1) * PAGE: page * PAGE], {'page': page, 'total': total, 'has_more': page * PAGE < total}


def _clean(value, limit, label, required=False):
    value = ' '.join(str(value or '').split())
    if required and not value:
        raise ValueError(f'{label} : obligatoire.')
    if len(value) > limit or any(ch in value for ch in '<>{}'):
        raise ValueError(f'{label} : {limit} caractères au plus, sans < > {{ }}.')
    return value


def _bad(msg):
    return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)


# ── Chiffres clés ────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAdminUser])
@throttle_classes([AdminThrottle])
def stats(request):
    now = timezone.now()
    users = User.objects.filter(deleted_at__isnull=True)
    events = Event.objects.filter(deleted_at__isnull=True)
    return Response({
        'users': users.count(),
        'users_7d': users.filter(created_at__gte=now - timedelta(days=7)).count(),
        'paying': users.exclude(subscription_plan='free').count(),
        'suspended': users.filter(is_active=False).count(),
        'events': events.count(),
        'events_published': events.filter(status='published').count(),
        'events_upcoming': events.filter(status='published', start_date__gte=now).count(),
        'tickets': Ticket.objects.filter(status__in=Ticket.ACTIVE).count(),
        'announcements_live': Announcement.objects.live(now).count(),
        'plans': dict(users.values_list('subscription_plan').annotate(n=Count('id')).order_by()),
    })


# ── Comptes ──────────────────────────────────────────────────────────────────
def _user_row(u):
    return {
        'id': str(u.id), 'email': u.email, 'first_name': u.first_name, 'last_name': u.last_name,
        'plan': u.subscription_plan, 'is_active': u.is_active, 'is_staff': u.is_staff, 'is_superuser': u.is_superuser,
        'is_verified': u.is_verified, 'created_at': u.created_at.isoformat(),
        'events': getattr(u, 'events_n', None),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
@throttle_classes([AdminThrottle])
def users(request):
    if request.method == 'POST':
        data = request.data if isinstance(request.data, dict) else {}
        email = str(data.get('email') or '').strip().lower()
        try:
            validate_email(email)
            first = _clean(data.get('first_name'), 50, 'Prénom', required=True)
            last = _clean(data.get('last_name'), 50, 'Nom', required=True)
        except DjangoValidationError:
            return _bad('Adresse email invalide.')
        except ValueError as exc:
            return _bad(str(exc))
        if User.objects.filter(email__iexact=email).exists():
            return _bad('Un compte existe déjà avec cet email.')
        user = User.objects.create_user(email=email, password=None, first_name=first, last_name=last, is_verified=True)
        user.set_unusable_password()
        user.save()
        try:                                           # lien pour choisir son mot de passe
            from users.views import send_password_reset_email
            send_password_reset_email(user, request)
        except Exception:
            logger.exception('Email de bienvenue (administration) non envoyé')
        _log(request, 'user.create', 'user', user.id, email=email)
        return Response(_user_row(user), status=status.HTTP_201_CREATED)

    qs = User.objects.filter(deleted_at__isnull=True).annotate(
        events_n=Count('organized_events', filter=Q(organized_events__deleted_at__isnull=True), distinct=True)
    ).order_by('-created_at')
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    rows, meta = _page(request, qs)
    return Response({**meta, 'results': [_user_row(u) for u in rows]})


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
@throttle_classes([AdminThrottle])
def user_detail(request, user_id):
    user = User.objects.filter(pk=user_id, deleted_at__isnull=True).first()
    if not user:
        return Response({'detail': 'Compte introuvable.'}, status=status.HTTP_404_NOT_FOUND)
    if user.pk == request.user.pk:
        return _bad('Modifiez votre propre compte depuis votre profil.')
    if user.is_superuser and not request.user.is_superuser:
        return Response({'detail': 'Seul un super-administrateur peut modifier ce compte.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        from users.views import erase_account
        email = user.email
        erase_account(user)
        _log(request, 'user.delete', 'user', user.id, email=email)
        return Response(status=status.HTTP_204_NO_CONTENT)

    data = request.data if isinstance(request.data, dict) else {}
    changes = {}
    try:
        for field, label in (('first_name', 'Prénom'), ('last_name', 'Nom')):
            if field in data:
                setattr(user, field, _clean(data[field], 50, label, required=True))
                changes[field] = getattr(user, field)
    except ValueError as exc:
        return _bad(str(exc))
    if 'plan' in data:
        if data['plan'] not in User.SubscriptionPlan.values:
            return _bad('Plan inconnu.')
        user.subscription_plan = changes['plan'] = data['plan']
    if 'is_active' in data:
        user.is_active = changes['is_active'] = bool(data['is_active'])
    if 'is_staff' in data:
        if not request.user.is_superuser:
            return Response({'detail': 'Seul un super-administrateur nomme les administrateurs.'}, status=status.HTTP_403_FORBIDDEN)
        user.is_staff = changes['is_staff'] = bool(data['is_staff'])
    if not changes:
        return _bad('Aucune modification.')
    user.save()
    if changes.get('is_active') is False:            # compte suspendu : déconnecté partout
        from users.views import _blacklist_all_tokens
        _blacklist_all_tokens(user)
    _log(request, 'user.update', 'user', user.id, **changes)
    return Response(_user_row(user))


# ── Événements ───────────────────────────────────────────────────────────────
def _event_row(e):
    return {
        'id': str(e.id), 'title': e.title, 'event_type': e.event_type, 'status': e.status, 'visibility': e.visibility,
        'start_date': e.start_date.isoformat() if e.start_date else None, 'is_paid': e.is_paid,
        'price': str(e.price) if e.price is not None else None, 'currency': e.currency,
        'organizer': {'id': str(e.organizer_id), 'name': f'{e.organizer.first_name} {e.organizer.last_name}'.strip(),
                      'email': e.organizer.email},
        'participants': getattr(e, 'tickets_n', None), 'created_at': e.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
@throttle_classes([AdminThrottle])
def events(request):
    qs = Event.objects.filter(deleted_at__isnull=True).select_related('organizer').annotate(
        tickets_n=Count('tickets', filter=Q(tickets__status__in=Ticket.ACTIVE), distinct=True)).order_by('-created_at')
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(organizer__email__icontains=q))
    st = request.query_params.get('status')
    if st in Event.EventStatus.values:
        qs = qs.filter(status=st)
    rows, meta = _page(request, qs)
    return Response({**meta, 'results': [_event_row(e) for e in rows]})


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
@throttle_classes([AdminThrottle])
def event_detail(request, event_id):
    event = Event.objects.select_related('organizer').filter(pk=event_id, deleted_at__isnull=True).first()
    if not event:
        return Response({'detail': 'Événement introuvable.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        from events.lifecycle import cancel_event
        result = cancel_event(event)
        _log(request, 'event.delete', 'event', event.id, title=event.title, **result)
        return Response(result)

    data = request.data if isinstance(request.data, dict) else {}
    changes = {}
    if 'title' in data:
        try:
            event.title = changes['title'] = _clean(data['title'], 200, 'Titre', required=True)
        except ValueError as exc:
            return _bad(str(exc))
    if 'status' in data:
        if data['status'] not in ('published', 'draft'):
            return _bad('Statut possible : publié ou brouillon.')
        event.status = changes['status'] = data['status']
        if event.status == 'published' and not event.published_at:
            event.published_at = timezone.now()
    if 'visibility' in data:
        if data['visibility'] not in ('public', 'private'):
            return _bad('Visibilité possible : public ou privé.')
        event.visibility = changes['visibility'] = data['visibility']
    if not changes:
        return _bad('Aucune modification.')
    event.save()
    _log(request, 'event.update', 'event', event.id, **changes)
    return Response(_event_row(event))


# ── Annonces ─────────────────────────────────────────────────────────────────
def _announcement(a, admin=False):
    row = {
        'id': str(a.id), 'title': a.title, 'body': a.body, 'link_url': a.link_url or None,
        'link_label': a.link_label or None, 'video': a.video, 'priority': a.priority,
    }
    if admin:
        now = timezone.now()
        row.update({'is_active': a.is_active, 'video_public_id': a.video_public_id or None, 'starts_at': a.starts_at.isoformat(),
                    'ends_at': a.ends_at.isoformat() if a.ends_at else None,
                    'live': a.is_active and a.starts_at <= now and (a.ends_at is None or a.ends_at > now)})
    return row


def _apply_announcement(a, data, request):
    if 'title' in data or a._state.adding:
        a.title = _clean(data.get('title'), 90, 'Titre', required=True)
    if 'body' in data:
        a.body = _clean(data.get('body'), 400, 'Texte')
    if 'link_url' in data:
        url = str(data.get('link_url') or '').strip()
        if url:
            try:
                URLValidator(schemes=['https'])(url)
            except DjangoValidationError:
                raise ValueError('Lien : une adresse https:// complète.')
        a.link_url = url[:300]
    if 'link_label' in data:
        a.link_label = _clean(data.get('link_label'), 30, 'Texte du bouton')
    if a.link_url and not a.link_label:
        a.link_label = 'En savoir plus'
    if 'priority' in data:
        if data['priority'] not in Announcement.Priority.values:
            raise ValueError('Priorité inconnue.')
        a.priority = data['priority']
    if 'is_active' in data:
        a.is_active = bool(data['is_active'])
    for field in ('starts_at', 'ends_at'):
        if field in data:
            raw = data.get(field)
            value = parse_datetime(raw) if isinstance(raw, str) and raw else None
            if raw and not value:
                raise ValueError('Date invalide.')
            if field == 'starts_at':
                a.starts_at = value or timezone.now()
            else:
                a.ends_at = value
    if a.ends_at and a.ends_at <= a.starts_at:
        raise ValueError('La fin de diffusion doit suivre le début.')
    if 'video' in data:
        try:
            fields = event_video.attach(None if a._state.adding else a, request.user, data['video'])
        except event_video.VideoError as exc:
            raise ValueError(exc.message)
        for k, v in fields.items():
            setattr(a, k, v)


def _announce_changed():
    broadcast_public({'type': 'announcements'})       # les fils ouverts se mettent à jour


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
@throttle_classes([AdminThrottle])
def announcements(request):
    if request.method == 'POST':
        data = request.data if isinstance(request.data, dict) else {}
        a = Announcement(created_by=request.user)
        try:
            _apply_announcement(a, data, request)
        except ValueError as exc:
            return _bad(str(exc))
        a.save()
        _log(request, 'announcement.create', 'announcement', a.id, title=a.title)
        _announce_changed()
        return Response(_announcement(a, admin=True), status=status.HTTP_201_CREATED)
    return Response({'results': [_announcement(a, admin=True) for a in Announcement.objects.all()[:100]]})


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
@throttle_classes([AdminThrottle])
def announcement_detail(request, announcement_id):
    a = Announcement.objects.filter(pk=announcement_id).first()
    if not a:
        return Response({'detail': 'Annonce introuvable.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        if a.video_public_id:
            event_video._destroy(a.video_public_id)
        _log(request, 'announcement.delete', 'announcement', a.id, title=a.title)
        a.delete()
        _announce_changed()
        return Response(status=status.HTTP_204_NO_CONTENT)
    data = request.data if isinstance(request.data, dict) else {}
    try:
        _apply_announcement(a, data, request)
    except ValueError as exc:
        return _bad(str(exc))
    a.save()
    _log(request, 'announcement.update', 'announcement', a.id, fields=sorted(data.keys()))
    _announce_changed()
    return Response(_announcement(a, admin=True))


@api_view(['GET'])
@permission_classes([AllowAny])
def live_announcements(request):
    return Response({'results': [_announcement(a) for a in Announcement.objects.live()[:3]]})
