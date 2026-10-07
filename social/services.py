"""social/services.py — demandes d'amitié et liste d'amis."""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Friendship


class FriendError(Exception):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def between(a, b):
    return Friendship.objects.filter(Q(requester=a, addressee=b) | Q(requester=b, addressee=a)).first()


def friend_ids(user):
    rows = Friendship.objects.filter(Q(requester=user) | Q(addressee=user), status='accepted') \
        .values_list('requester_id', 'addressee_id')
    return {b if a == user.id else a for a, b in rows}


def status_for(user, other_ids):
    """{user_id: 'friend' | 'sent' | 'received'} pour l'affichage des boutons."""
    out = {}
    for f in Friendship.objects.filter(Q(requester=user, addressee_id__in=other_ids) |
                                       Q(addressee=user, requester_id__in=other_ids)):
        other = f.addressee_id if f.requester_id == user.id else f.requester_id
        out[other] = 'friend' if f.status == 'accepted' else ('sent' if f.requester_id == user.id else 'received')
    return out


def _notify(user, type_, actor, friendship):
    from notifications.services import notify
    title = actor.full_name
    body = "vous a envoyé une demande d'ami" if type_ == 'friend_request' else "a accepté votre demande d'ami"
    notify(user, type_, title, body, actor=actor, data={'friendship_id': str(friendship.id)},
           dedupe_key=f'{type_}:{friendship.id}')


def request(user, target):
    if target.id == user.id:
        raise FriendError('Vous ne pouvez pas vous ajouter vous-même.', 'self')
    existing = between(user, target)
    if existing:
        if existing.status == 'accepted':
            raise FriendError('Vous êtes déjà amis.', 'already_friends', 409)
        if existing.requester_id == user.id:
            return existing                       # demande déjà envoyée
        return accept(user, existing)             # l'autre avait déjà demandé : on accepte
    try:
        with transaction.atomic():
            f = Friendship.objects.create(requester=user, addressee=target)
    except IntegrityError:
        return between(user, target)
    _notify(target, 'friend_request', user, f)
    return f


def accept(user, friendship):
    if friendship.addressee_id != user.id or friendship.status != 'pending':
        raise FriendError('Cette demande ne peut pas être acceptée.', 'invalid')
    friendship.status, friendship.responded_at = 'accepted', timezone.now()
    friendship.save(update_fields=['status', 'responded_at'])
    _notify(friendship.requester, 'friend_accepted', user, friendship)
    _close_request_notification(user, friendship)
    return friendship


def decline(user, friendship):
    """Refuser (destinataire), annuler (demandeur) ou retirer un ami (les deux)."""
    if user.id not in (friendship.requester_id, friendship.addressee_id):
        raise FriendError('Introuvable.', 'not_found', 404)
    _close_request_notification(user, friendship)
    friendship.delete()


def _close_request_notification(user, friendship):
    from notifications.models import Notification
    Notification.objects.filter(user=user, dedupe_key=f'friend_request:{friendship.id}', read_at__isnull=True) \
        .update(read_at=timezone.now())
