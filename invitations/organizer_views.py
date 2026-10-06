"""
invitations/organizer_views.py
═══════════════════════════════════════════════════════════════
Côté organisateur (parcours D) :

  POST   /api/events/<id>/invite/              M12 / M29 : envoi par lot
  GET    /api/events/<id>/participants/        M13 : invités, statuts, compteurs
  POST   /api/events/<id>/remind-pending/      M13 : « Relancer les N en attente »
  POST   /api/invitations/<id>/remind/         M13 : relancer un invité
  POST   /api/events/<id>/participants/export-link/   M13 : export CSV (plan Standard)
  GET    /api/events/participants/export/<token>/     fichier CSV (lien signé 5 min)
  GET    /api/users/search/?q=&event=          M12 mode Membres
  POST   /api/users/lookup/  { emails: [] }    M29 : « Membre » / « Pas encore inscrit »

Toutes les vues vérifient que l'événement appartient à l'utilisateur
(OWASP API1 : BOLA). Les numéros ne sont renvoyés que masqués.
═══════════════════════════════════════════════════════════════
"""
import csv
import io

from django.core import signing
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from easevent.media import absolute_url
from events.models import Event

from .models import Invitation
from .services import (
    InviteError, can_remind, deliverable, display_name, initials, invite_batch, mask_phone,
    normalize_email, plan_usage, remind,
)
from .throttles import InviteSendThrottle, UserSearchThrottle

EXPORT_SALT = 'easevent.guests.export'
EXPORT_TTL = 300
EXPORT_PLANS = ('standard', 'pro')


def _own_event(request, event_id):
    try:
        return Event.objects.select_related('organizer').get(
            id=event_id, organizer=request.user, deleted_at__isnull=True)
    except Event.DoesNotExist:
        raise Http404


def _error(exc):
    body = {'detail': exc.message, 'code': exc.code}
    body.update(exc.extra)
    return Response(body, status=exc.status)


# ─────────────────────────────────────────────────────────────
# M12 / M29 — Envoyer des invitations
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([InviteSendThrottle])
def invite(request, event_id):
    """
    Body : { emails: [], phone_numbers: [ "+33…" | {phone, name} ], user_ids: [], message }
    Compatibilité : { email } ou { phone_number } (un seul invité).
    """
    event = _own_event(request, event_id)
    data = request.data

    def as_list(key):
        value = data.get(key)
        if value in (None, ''):
            return []
        return value if isinstance(value, list) else [value]

    emails = as_list('emails') + as_list('email')
    phones = as_list('phone_numbers') + as_list('phone_number')
    user_ids = as_list('user_ids')
    try:
        result = invite_batch(event, request.user, emails=emails, phones=phones, user_ids=user_ids,
                              message=data.get('message', ''), request=request)
    except InviteError as exc:
        return _error(exc)

    count = len(result['created'])
    if count == 0:
        detail = 'Aucune nouvelle invitation : ces personnes sont déjà invitées ou les contacts sont invalides.'
    else:
        detail = f"{count} invitation{'s' if count > 1 else ''} envoyée{'s' if count > 1 else ''}."
    return Response({'detail': detail, 'message': detail, **result},
                    status=status.HTTP_201_CREATED if count else status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────
# M13 — Invités & réponses
# ─────────────────────────────────────────────────────────────
def _guest_rows(event):
    from tickets.models import Ticket

    invitations = list(event.invitations.exclude(status='revoked')
                       .select_related('invited_user').order_by('-sent_at'))
    user_ids = [inv.invited_user_id for inv in invitations if inv.invited_user_id]
    tickets = {t.user_id: t for t in Ticket.objects.filter(
        event=event, user_id__in=user_ids, status__in=Ticket.ACTIVE)}
    now = timezone.now()

    rows, counts = [], {'confirmed': 0, 'pending': 0, 'declined': 0, 'total': 0}
    for inv in invitations:
        ticket = tickets.get(inv.invited_user_id)
        expired = inv.status == 'expired' or (inv.status in ('sent', 'opened') and inv.expires_at <= now)
        if ticket and ticket.status == Ticket.Status.GENERATED:
            display = 'confirmed'
        elif inv.status == 'declined':
            display = 'declined'
        elif expired:
            display = 'expired'
        elif inv.status == 'confirmed':
            display = 'to_validate'     # accepté, ticket pas encore validé / payé
        else:
            display = inv.status        # sent | opened
        bucket = {'confirmed': 'confirmed', 'declined': 'declined', 'expired': None}.get(display, 'pending')
        counts['total'] += 1
        if bucket:
            counts[bucket] += 1

        user = inv.invited_user
        kind = 'member' if user else ('email' if inv.email else 'phone')
        phone = inv.phone if inv.phone_number else ''
        rows.append({
            'id':              str(inv.id),
            'status':          inv.status,
            'display_status':  display,
            'bucket':          bucket or 'expired',
            'kind':            kind,
            'channel':         inv.channel,
            'name':            display_name(inv),
            'initials':        initials(user.first_name, user.last_name) if user else initials(*(inv.contact_name or '').split()[:2]),
            'avatar_url':      user.avatar_url if user else None,
            'user_id':         str(user.id) if user else None,
            'email':           inv.email or '',
            'phone':           mask_phone(phone),
            'delivery_status': inv.delivery_status,
            'ticket_status':   ticket.status if ticket else None,
            'payment_status':  ticket.payment_status if ticket else None,
            'sent_at':         inv.sent_at.isoformat() if inv.sent_at else None,
            'opened_at':       inv.opened_at.isoformat() if inv.opened_at else None,
            'responded_at':    inv.responded_at.isoformat() if inv.responded_at else None,
            'reminded_at':     inv.reminded_at.isoformat() if inv.reminded_at else None,
            'can_remind':      can_remind(inv, now),
            # Compatibilité avec l'ancien écran (E08)
            'user': ({'id': str(user.id), 'first_name': user.first_name, 'last_name': user.last_name,
                      'avatar_url': user.avatar_url} if user else {'email': inv.email or '', 'phone_number': mask_phone(phone)}),
        })
    return rows, counts


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def participants(request, event_id):
    event = _own_event(request, event_id)
    # Filet de sécurité si le worker Celery est arrêté : envoi direct
    from datetime import timedelta
    from .services import send_now
    stuck = list(event.invitations.filter(
        delivery_status='pending', status__in=('sent', 'opened'),
        updated_at__lt=timezone.now() - timedelta(minutes=10)).values_list('id', flat=True)[:50])
    if stuck:
        send_now([str(i) for i in stuck])
    rows, counts = _guest_rows(event)
    return Response({
        'count':        len(rows),
        'counts':       counts,
        'usage':        plan_usage(event),
        'remindable':   sum(1 for r in rows if r['can_remind']),
        'participants': rows,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([InviteSendThrottle])
def remind_one(request, invitation_id):
    try:
        inv = Invitation.objects.select_related('event', 'event__organizer', 'invited_user').get(
            id=invitation_id, event__organizer=request.user, event__deleted_at__isnull=True)
    except Invitation.DoesNotExist:
        raise Http404
    if not can_remind(inv):
        if inv.status not in ('sent', 'opened'):
            reason = 'Cet invité a déjà répondu.'
        elif not deliverable(inv):
            reason = "L'envoi de SMS n'est pas encore activé : impossible de relancer ce numéro."
        else:
            reason = 'Cet invité a déjà été relancé il y a moins de 24 heures.'
        return Response({'detail': reason, 'code': 'cannot_remind'}, status=status.HTTP_400_BAD_REQUEST)
    result = remind([inv], request)
    if result['failed']:
        return Response({'detail': "La relance n'a pas pu être envoyée. Réessayez plus tard.", 'code': 'delivery_failed'},
                        status=status.HTTP_502_BAD_GATEWAY)
    return Response({'detail': 'Relance envoyée.', 'reminded': 1, 'delivery_status': inv.delivery_status})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([InviteSendThrottle])
def remind_pending(request, event_id):
    event = _own_event(request, event_id)
    pending = list(event.invitations.filter(status__in=('sent', 'opened'))
                   .select_related('event', 'event__organizer', 'invited_user'))
    result = remind(pending, request)
    count, failed = result['reminded'], result['failed']
    if count:
        detail = f"{count} relance{'s' if count > 1 else ''} envoyée{'s' if count > 1 else ''}."
    else:
        detail = 'Personne à relancer pour le moment (une relance par invité et par jour).'
    if failed:
        detail += f" {failed} n'{'ont' if failed > 1 else 'a'} pas pu partir."
    return Response({'detail': detail, **result})


# ─────────────────────────────────────────────────────────────
# Export CSV (plan Standard ou Pro)
# ─────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def export_link(request, event_id):
    event = _own_event(request, event_id)
    if request.user.subscription_plan not in EXPORT_PLANS:
        return Response({'detail': "L'export de la liste des invités est inclus dans le plan Standard.",
                         'code': 'plan_required'}, status=status.HTTP_403_FORBIDDEN)
    token = signing.dumps({'e': str(event.id), 'u': str(request.user.id)}, salt=EXPORT_SALT, compress=True)
    return Response({'url': absolute_url(f'/api/events/participants/export/{token}/', request),
                     'expires_in': EXPORT_TTL})


STATUS_LABELS = {
    'confirmed': 'Confirmé', 'to_validate': 'Ticket à valider', 'opened': 'Vu',
    'sent': 'En attente', 'declined': 'Décliné', 'expired': 'Expiré',
}


def export_csv(request, token):
    try:
        data = signing.loads(token, salt=EXPORT_SALT, max_age=EXPORT_TTL)
        event = Event.objects.select_related('organizer').get(
            id=data['e'], organizer_id=data['u'], deleted_at__isnull=True)
    except (signing.BadSignature, KeyError, Event.DoesNotExist):
        raise Http404
    rows, _ = _guest_rows(event)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['Nom', 'Email', 'Téléphone', 'Canal', 'Statut', 'Invité le', 'Répondu le'])

    def safe(value):
        # Neutralise les formules à l'ouverture dans un tableur (injection CSV)
        value = str(value or '')
        return "'" + value if value[:1] in ('=', '+', '-', '@', '\t', '\r') else value

    for r in rows:
        writer.writerow([safe(r['name']), safe(r['email']), safe(r['phone']),
                         {'member': 'Membre', 'email': 'Email', 'phone': 'SMS'}[r['kind']],
                         STATUS_LABELS.get(r['display_status'], r['display_status']),
                         (r['sent_at'] or '')[:10], (r['responded_at'] or '')[:10]])
    response = HttpResponse('﻿' + buf.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="invites-{str(event.id)[:8]}.csv"'
    response['Cache-Control'] = 'no-store'
    return response


# ─────────────────────────────────────────────────────────────
# Annuaire des membres (M12 mode Membres, M29)
# ─────────────────────────────────────────────────────────────
def _members():
    from users.models import User
    return User.objects.filter(is_active=True, is_verified=True, deleted_at__isnull=True)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserSearchThrottle])
def search_users(request):
    """Recherche par nom (≥ 2 caractères). Ne renvoie jamais l'email."""
    q = ' '.join(str(request.query_params.get('q', '')).split())[:60]
    if len(q) < 2:
        return Response({'results': []})
    qs = _members().exclude(id=request.user.id)
    for term in q.split()[:3]:
        qs = qs.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))
    users = list(qs.order_by('first_name', 'last_name')[:15])

    invited = set()
    event_id = request.query_params.get('event')
    if event_id:
        try:
            invited = set(Invitation.objects.filter(
                event_id=event_id, event__organizer=request.user, invited_user__in=users,
            ).exclude(status='revoked').values_list('invited_user_id', flat=True))
        except Exception:
            invited = set()
    return Response({'results': [{
        'id':             str(u.id),
        'first_name':     u.first_name,
        'last_name':      u.last_name,
        'initials':       initials(u.first_name, u.last_name),
        'avatar_url':     u.avatar_url,
        'already_invited': u.id in invited,
    } for u in users]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserSearchThrottle])
def lookup_emails(request):
    """{ emails: [] } → [{ email, valid, has_account, name }] (50 adresses max)."""
    emails = request.data.get('emails') or []
    if not isinstance(emails, list) or len(emails) > 50:
        return Response({'detail': '50 adresses au maximum.'}, status=status.HTTP_400_BAD_REQUEST)
    normalized = [(str(e)[:254], normalize_email(e)) for e in emails]
    accounts = {u.email.lower(): u for u in _members().filter(email__in=[n for _, n in normalized if n])}
    results = []
    for original, email in normalized:
        user = accounts.get(email) if email else None
        results.append({
            'email':       email or original,
            'valid':       email is not None,
            'has_account': user is not None,
            'name':        user.full_name if user else '',
            'initials':    initials(user.first_name, user.last_name) if user else '',
            'is_self':     bool(email) and email == request.user.email.lower(),
        })
    return Response({'results': results})
