"""
events/team.py — cogestion d'un événement

Rôles :
- organisateur (créateur) : tous les droits, seul à gérer l'équipe, la
  répartition des invités, les finances et la suppression ;
- co-organisateur (accepté) : gère l'événement comme l'organisateur
  (modifier, publier, inviter dans sa part, invités, accueil, souvenirs,
  statistiques, message de diffusion) ;
- photographe (accepté) : ajoute des photos à l'espace souvenirs.

Répartition des invités : si l'événement a un nombre de places et des
co-organisateurs, chacun peut recevoir une part (Event.guest_split,
{user_id: nombre}). Une proposition équitable est calculée ; l'organisateur
la modifie quand il veut.
"""
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import Event, EventCollaborator

COHOST = EventCollaborator.Role.COHOST
PHOTOGRAPHER = EventCollaborator.Role.PHOTOGRAPHER
ACCEPTED = EventCollaborator.Status.ACCEPTED
MAX_TEAM = 10


class TeamError(Exception):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


# ─────────────────────────────────────────────────────────────
# Droits
# ─────────────────────────────────────────────────────────────
def role_of(event, user):
    """'organizer', 'cohost', 'photographer' ou None."""
    if user is None or not user.is_authenticated:
        return None
    if event.organizer_id == user.id:
        return 'organizer'
    collab = EventCollaborator.objects.filter(event=event, user=user, status=ACCEPTED).only('role').first()
    return collab.role if collab else None


def is_manager(event, user):
    return role_of(event, user) in ('organizer', COHOST)


def can_add_photos(event, user):
    return role_of(event, user) in ('organizer', COHOST, PHOTOGRAPHER)


def managers_q(user):
    """Filtre Event : événements que l'utilisateur gère (organisateur ou co-organisateur accepté)."""
    return Q(organizer=user) | Q(collaborators__user=user, collaborators__role=COHOST, collaborators__status=ACCEPTED)


def managed_event(user, event_id, *, organizer_only=False):
    """L'événement si l'utilisateur le gère, sinon None (BOLA : même réponse qu'un événement absent)."""
    q = Q(organizer=user) if organizer_only else managers_q(user)
    try:
        return Event.objects.select_related('organizer').filter(q).filter(
            id=event_id, deleted_at__isnull=True).distinct().first()
    except (ValueError, TypeError):
        return None


def manager_ids(event):
    """Organisateur + co-organisateurs acceptés."""
    ids = [event.organizer_id]
    ids += list(event.collaborators.filter(role=COHOST, status=ACCEPTED).values_list('user_id', flat=True))
    return ids


# ─────────────────────────────────────────────────────────────
# Répartition des invités
# ─────────────────────────────────────────────────────────────
def used_by(event):
    """Invitations envoyées par chaque organisateur (sans auteur = l'organisateur) ; un refus libère la place."""
    rows = (event.invitations.exclude(status__in=('revoked', 'declined', 'expired'))
            .values('invited_by').annotate(n=Count('id')))
    used = {}
    for r in rows:
        key = str(r['invited_by'] or event.organizer_id)
        used[key] = used.get(key, 0) + r['n']
    return used


def proposed_split(event, ids=None):
    """Part équitable des places ; le reste revient à l'organisateur."""
    if not event.max_guests:
        return {}
    ids = [str(i) for i in (ids or manager_ids(event))]
    share, rest = divmod(event.max_guests, len(ids))
    return {uid: share + (rest if i == 0 else 0) for i, uid in enumerate(ids)}


def quota_for(event, user):
    """Places réservées à cet organisateur, ou None (pas de répartition)."""
    if not event.max_guests or not event.guest_split:
        return None
    value = event.guest_split.get(str(user.id))
    return int(value) if value is not None else None


def check_invite_quota(event, inviter, count):
    """Lève TeamError si l'envoi dépasse le nombre de places ou la part de l'organisateur."""
    if not event.max_guests or count <= 0:
        return
    used = used_by(event)
    total = sum(used.values())
    if total + count > event.max_guests:
        left = max(0, event.max_guests - total)
        raise TeamError(f"L'événement est limité à {event.max_guests} invités. Il reste {left} place(s).",
                        'event_full', 403)
    quota = quota_for(event, inviter)
    if quota is not None:
        mine = used.get(str(inviter.id), 0)
        if mine + count > quota:
            left = max(0, quota - mine)
            raise TeamError(
                f"Votre part est de {quota} invité(s) : il vous en reste {left}. "
                "Mettez-vous d'accord avec les autres organisateurs pour changer la répartition.",
                'split_limit', 403)


def set_split(event, split):
    """split : {user_id: nombre} ; somme ≤ nombre de places ; organisateurs connus uniquement."""
    if not event.max_guests:
        raise TeamError("Indiquez d'abord le nombre d'invités de l'événement.", 'no_max_guests')
    if not isinstance(split, dict):
        raise TeamError('Répartition invalide.', 'invalid_split')
    ids = {str(i) for i in manager_ids(event)}
    clean = {}
    for uid, value in split.items():
        if str(uid) not in ids:
            raise TeamError("Cette personne n'est pas co-organisatrice de l'événement.", 'invalid_split')
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise TeamError('Chaque part doit être un nombre.', 'invalid_split')
        if value < 0:
            raise TeamError('Chaque part doit être positive.', 'invalid_split')
        clean[str(uid)] = value
    if sum(clean.values()) > event.max_guests:
        raise TeamError(f"La somme des parts dépasse les {event.max_guests} places.", 'split_too_large')
    event.guest_split = clean
    event.save(update_fields=['guest_split', 'updated_at'])
    return clean


def split_state(event):
    """Répartition actuelle, proposition, utilisation par organisateur."""
    used = used_by(event)
    split = {k: int(v) for k, v in (event.guest_split or {}).items()} if event.max_guests else {}
    return {
        'max_guests': event.max_guests,
        'split': split,
        'proposed': proposed_split(event),
        'used': used,
        'total_used': sum(used.values()),
        'over': bool(split) and sum(split.values()) > (event.max_guests or 0),
    }


# ─────────────────────────────────────────────────────────────
# Équipe
# ─────────────────────────────────────────────────────────────
def _member(user, role, status, collab_id=None, used=0, quota=None):
    from invitations.services import initials
    return {
        'id': str(collab_id) if collab_id else None,
        'user_id': str(user.id), 'name': user.full_name,
        'initials': initials(user.first_name, user.last_name),
        'avatar_url': user.avatar_url, 'role': role, 'status': status,
        'used': used, 'quota': quota,
    }


def team_payload(event, viewer):
    state = split_state(event)
    members = [_member(event.organizer, 'organizer', 'accepted', used=state['used'].get(str(event.organizer_id), 0),
                       quota=state['split'].get(str(event.organizer_id)))]
    for c in event.collaborators.select_related('user').exclude(status='declined').order_by('invited_at'):
        members.append(_member(c.user, c.role, c.status, c.id, used=state['used'].get(str(c.user_id), 0),
                               quota=state['split'].get(str(c.user_id)) if c.role == COHOST else None))
    role = role_of(event, viewer)
    return {'my_role': role, 'can_manage_team': role == 'organizer', 'members': members, **state}


def invite_member(event, by, user, role):
    from notifications.models import Notification
    from notifications.services import notify

    if role not in (COHOST, PHOTOGRAPHER):
        raise TeamError('Rôle invalide.', 'invalid_role')
    by_role = role_of(event, by)
    # Organisateur : co-organisateurs et photographes ; co-organisateur : photographes
    if by_role != 'organizer' and not (by_role == COHOST and role == PHOTOGRAPHER):
        raise TeamError("Seul l'organisateur peut ajouter un co-organisateur.", 'forbidden', 403)
    if user.id == event.organizer_id:
        raise TeamError("L'organisateur fait déjà partie de l'équipe.", 'self')
    if not user.is_active or user.deleted_at is not None:
        raise TeamError('Ce compte est introuvable.', 'unknown_user', 404)
    if event.collaborators.exclude(status='declined').count() >= MAX_TEAM:
        raise TeamError(f"{MAX_TEAM} personnes au maximum dans l'équipe.", 'team_full')
    existing = event.collaborators.filter(user=user).first()
    if existing and existing.status != 'declined':
        raise TeamError('Cette personne fait déjà partie de l’équipe.', 'already_member', 409)
    try:
        with transaction.atomic():
            if existing:
                existing.role, existing.status, existing.invited_by = role, 'pending', by
                existing.accepted_at = None
                existing.invited_at = timezone.now()
                existing.save()
                collab = existing
            else:
                collab = EventCollaborator.objects.create(event=event, user=user, role=role, invited_by=by,
                                                          permissions=permissions_for(role))
    except IntegrityError:
        raise TeamError('Cette personne fait déjà partie de l’équipe.', 'already_member', 409)
    label = 'co-organiser' if role == COHOST else 'prendre les photos de'
    notify(user, Notification.Type.TEAM_INVITE, by.full_name, f'vous propose de {label} « {event.title} »',
           actor=by, event=event, data={'collaborator_id': str(collab.id), 'role': role},
           dedupe_key=f'team:{collab.id}:{collab.invited_at.timestamp():.0f}')
    return collab


def permissions_for(role):
    cohost = role == COHOST
    return {'can_read': True, 'can_add_media': True, 'can_edit_components': cohost, 'can_manage_guests': cohost}


def respond(collab, user, accept):
    from notifications.models import Notification
    from notifications.services import notify

    if collab.user_id != user.id:
        raise TeamError('Invitation introuvable.', 'not_found', 404)
    if collab.status != 'pending':
        raise TeamError('Vous avez déjà répondu.', 'already_answered', 409)
    event = collab.event
    if event.deleted_at is not None:
        raise TeamError("Cet événement a été supprimé.", 'event_deleted', 410)
    collab.status = ACCEPTED if accept else 'declined'
    collab.accepted_at = timezone.now() if accept else None
    collab.save(update_fields=['status', 'accepted_at'])
    word = 'a accepté' if accept else 'a refusé'
    role = 'de co-organiser' if collab.role == COHOST else "d'être photographe pour"
    target = collab.invited_by or event.organizer
    notify(target, Notification.Type.TEAM_RESPONSE, user.full_name, f'{word} {role} « {event.title} »',
           actor=user, event=event, data={'collaborator_id': str(collab.id)})
    return collab


def remove_member(collab, by):
    """L'organisateur retire n'importe qui ; chacun peut quitter l'équipe."""
    event = collab.event
    if by.id != event.organizer_id and by.id != collab.user_id:
        raise TeamError("Seul l'organisateur peut retirer quelqu'un de l'équipe.", 'forbidden', 403)
    uid = str(collab.user_id)
    collab.delete()
    if uid in (event.guest_split or {}):
        split = dict(event.guest_split)
        split.pop(uid)
        event.guest_split = split
        event.save(update_fields=['guest_split', 'updated_at'])


def pending_for(user):
    return (EventCollaborator.objects.select_related('event', 'event__organizer', 'invited_by')
            .filter(user=user, status='pending', event__deleted_at__isnull=True).order_by('-invited_at'))


def linked_by_team(a, b):
    """Deux membres d'une même équipe (organisateur compris) peuvent s'écrire."""
    shared = EventCollaborator.objects.filter(status=ACCEPTED, event__deleted_at__isnull=True)
    return (shared.filter(user=a, event__organizer=b).exists()
            or shared.filter(user=b, event__organizer=a).exists()
            or shared.filter(user=a, event__collaborators__user=b,
                             event__collaborators__status=ACCEPTED).exists())
