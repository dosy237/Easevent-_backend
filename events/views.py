# events/views.py
# ════════════════════════════════════════════════════════════════
# Views pour les événements Easevent.
#
# Endpoints couverts :
# ────────────────────
# GET    /api/events/publics/                   → fil de découverte public
# GET    /api/events/publics/<id>/              → détail événement public
# GET    /api/events/mes-evenements/            → mes événements (organisateur)
# POST   /api/events/create/                   → créer un événement
# POST   /api/events/upload-image/             → uploader une image sur Cloudinary
# GET    /api/events/<id>/detail/              → détail complet (organisateur)
# PATCH  /api/events/<id>/update/              → modifier un événement
# POST   /api/events/<id>/publish/             → publier / dépublier
# DELETE /api/events/<id>/delete/              → supprimer (soft delete)
# GET    /api/events/<id>/participants/        → liste des invités
# POST   /api/events/<id>/invite/             → inviter un participant
# DELETE /api/invitations/<id>/revoke/        → révoquer une invitation
# ════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────
# IMPORTS PYTHON STANDARD
# math     : calculs mathématiques (formule de Haversine pour la distance GPS)
# datetime : manipulation des dates pour les filtres date_from et date_to
# ─────────────────────────────────────────────────────────────────
import math
import secrets
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# IMPORTS DJANGO
# Q           : combine des conditions OR / AND dans les requêtes
# timezone    : gestion des dates avec fuseau horaire (UTC)
# send_mail   : envoi d'emails via SendGrid (configuré dans settings.py)
# slugify     : transforme un texte en slug URL (ex: "Mon Mariage" → "mon-mariage")
# parse_datetime : convertit une string ISO en objet datetime Python
# ─────────────────────────────────────────────────────────────────
from django.db.models        import Q
from django.utils            import timezone
from django.utils.text       import slugify
from django.utils.dateparse  import parse_datetime
from django.core.mail        import send_mail
from django.conf             import settings

# ─────────────────────────────────────────────────────────────────
# IMPORTS DJANGO REST FRAMEWORK
# ─────────────────────────────────────────────────────────────────
from rest_framework.decorators  import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response    import Response
from rest_framework             import status

# ─────────────────────────────────────────────────────────────────
# IMPORT CLOUDINARY
# Bibliothèque officielle pour uploader des images vers Cloudinary.
# La configuration (cloud_name, api_key, api_secret) est dans settings.py.
# ─────────────────────────────────────────────────────────────────
import cloudinary
import cloudinary.uploader

# ─────────────────────────────────────────────────────────────────
# IMPORTS LOCAUX
# ─────────────────────────────────────────────────────────────────
from .models      import Event
from .serializers import EventPublicSerializer
from .ticketing   import clean_ticketing, clean_style

import logging
logger = logging.getLogger(__name__)

VISIBILITIES = ('public', 'private')


# ════════════════════════════════════════════════════════════════
# VIEW : liste_evenements_publics
# GET /api/events/publics/
# ════════════════════════════════════════════════════════════════
from datetime import datetime

from django.db import connection

@api_view(['GET'])
@permission_classes([AllowAny])
def liste_evenements_publics(request):
    """
    GET /api/events/publics/
    Retourne la liste des événements publiés et non supprimés.
    Filtres possibles : type, date, recherche par titre.
    """
    evenements = Event.objects.select_related('organizer').filter(
        status             = 'published',
        visibility         = 'public',
        deleted_at__isnull = True
    ).order_by('-start_date')

    # Filtre par type
    event_type = request.query_params.get('type')
    if event_type:
        evenements = evenements.filter(event_type=event_type)

    # Filtre par recherche
    search = request.query_params.get('search')
    if search:
        evenements = evenements.filter(title__icontains=search)

    serializer = EventPublicSerializer(evenements, many=True, context={'request': request})
    return Response({
        'count':  evenements.count(),
        'events': serializer.data
    })
# ════════════════════════════════════════════════════════════════
# VIEW : detail_evenement_public
# GET /api/events/publics/<event_id>/
# ════════════════════════════════════════════════════════════════
@api_view(['GET'])
@permission_classes([AllowAny])
def detail_evenement_public(request, event_id):
    """
    Retourne le détail d'un événement.

    Règles d'accès :
    - Événement public  → accessible par tous (connecté ou non)
    - Événement privé   → accessible uniquement par l'organisateur
                          ou un utilisateur ayant une invitation valide
    """
    try:
        event = Event.objects.get(
            id                 = event_id,
            status             = 'published',
            deleted_at__isnull = True,
        )
    except Event.DoesNotExist:
        return Response(
            {'error': 'Événement introuvable ou non publié.'},
            status=status.HTTP_404_NOT_FOUND
        )

    # ── Contrôle d'accès pour les événements privés ───────────────
    # Organisateur, ou invitation valide (non retirée, non expirée) — même règle que les tickets
    if event.visibility == 'private':
        if not request.user.is_authenticated:
            return Response(
                {'error': 'Cet événement est privé. Vous devez être invité pour y accéder.', 'code': 'private'},
                status=status.HTTP_403_FORBIDDEN
            )
        from tickets.models import Ticket
        from tickets.services import can_access_event
        has_ticket = Ticket.objects.filter(event=event, user=request.user, status__in=Ticket.ACTIVE).exists()
        if not (can_access_event(event, request.user) or has_ticket):
            return Response(
                {'error': 'Cet événement est privé. Vous n\'avez pas été invité.', 'code': 'private'},
                status=status.HTTP_403_FORBIDDEN
            )

    serializer = EventPublicSerializer(event, context={'request': request, 'with_my_ticket': True})
    return Response(serializer.data)


# ════════════════════════════════════════════════════════════════
# VIEW : mes_evenements
# GET /api/events/mes-evenements/
# ════════════════════════════════════════════════════════════════
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def mes_evenements(request):
    """
    Retourne tous les événements créés par l'utilisateur connecté.
    Inclut les brouillons, publiés et archivés.
    """
    evenements = Event.objects.select_related('organizer').filter(
        organizer          = request.user,
        deleted_at__isnull = True,
    ).order_by('-created_at')

    serializer = EventPublicSerializer(evenements, many=True, context={'request': request})
    return Response({
        'count':  evenements.count(),
        'events': serializer.data,
    })


# ════════════════════════════════════════════════════════════════
# VIEW : upload_image
# POST /api/events/upload-image/
# ════════════════════════════════════════════════════════════════
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_image(request):
    """
    Reçoit une image en base64 depuis React Native,
    l'upload sur Cloudinary et retourne l'URL publique HTTPS.

    Cloudinary gère automatiquement :
    - Compression et optimisation qualité (quality: auto)
    - Redimensionnement (max 1920px de large)
    - Distribution via CDN mondial (chargement rapide partout dans le monde)

    Body JSON :
    {
        "image": "data:image/jpeg;base64,/9j/4AAQ...",
        "name":  "cover"  ← identifiant de l'image (cover, gallery_1, gallery_2)
    }
    """
    image_data = request.data.get('image')
    image_name = request.data.get('name', 'event_image')

    if not image_data:
        return Response(
            {'detail': 'Aucune image fournie.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        result = cloudinary.uploader.upload(
            image_data,
            folder         = f'easevent/events/{request.user.id}',
            public_id      = f'{image_name}_{request.user.id}',
            overwrite      = True,
            transformation = [{'width': 1920, 'crop': 'limit', 'quality': 'auto'}]
        )
        return Response({
            'url':       result['secure_url'],
            'public_id': result['public_id'],
        })
    except Exception:
        logger.exception("Erreur d'upload Cloudinary")
        return Response(
            {'detail': "Impossible d'envoyer l'image pour le moment. Réessayez."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ════════════════════════════════════════════════════════════════
# VIEW : creer_evenement
# POST /api/events/create/
# ════════════════════════════════════════════════════════════════
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def creer_evenement(request):
    """
    Crée un nouvel événement pour l'utilisateur connecté.
    L'événement est créé en mode brouillon (status='draft').
    Il faut le publier explicitement pour qu'il apparaisse dans le fil.

    Body JSON :
    {
        "title":            "Mon Mariage",
        "event_type":       "mariage",
        "description":      "...",
        "start_date":       "2026-09-15T18:00:00",
        "end_date":         "2026-09-16T02:00:00",
        "location_address": "Château de Versailles",
        "cover_image":      "https://res.cloudinary.com/...",
        "ambiance":         "elegant",
        "palette":          {"primary": "#C4A882", "secondary": "#2C5F4A"},
        "visibility":       "public"
    }
    """
    data = request.data if isinstance(request.data, dict) else {}
    from .validation import EventInputError, clean_core
    try:
        core = clean_core(data)
    except EventInputError as exc:
        return Response({'detail': exc.message, **({exc.field: exc.message} if exc.field else {})},
                        status=status.HTTP_400_BAD_REQUEST)

    if data.get('event_type') not in Event.EventType.values:
        return Response({'detail': "Type d'événement invalide."}, status=status.HTTP_400_BAD_REQUEST)

    visibility = data.get('visibility', 'public')
    if visibility not in VISIBILITIES:
        return Response({'detail': 'Visibilité invalide (public ou private).'}, status=status.HTTP_400_BAD_REQUEST)

    # ── Billetterie & dress code (M23) + type libre et palette ────
    ticketing, ticket_errors = clean_ticketing(data)
    style, style_errors = clean_style(data, data.get('event_type'))
    ticket_errors.update(style_errors)
    ticketing.update(style)
    if ticket_errors:
        first = next(iter(ticket_errors.values()))
        return Response({'detail': first, **ticket_errors}, status=status.HTTP_400_BAD_REQUEST)

    # ── Génération du subdomain unique ───────────────────────────
    # slugify("Mon Mariage 2026") → "mon-mariage-2026"
    # On ajoute un compteur si le slug existe déjà
    base_slug = slugify(core['title'])[:80] or 'evenement'
    subdomain = base_slug
    counter   = 1
    while Event.objects.filter(subdomain=subdomain).exists():
        subdomain = f"{base_slug}-{counter}"
        counter  += 1

    try:
        event = Event.objects.create(
            organizer        = request.user,
            event_type       = data['event_type'],
            visibility       = visibility,
            status           = 'draft',  # Toujours brouillon à la création
            subdomain        = subdomain,
            **core,
            **ticketing,
        )

        serializer = EventPublicSerializer(event, context={'request': request})
        return Response({
            'message': 'Événement créé avec succès.',
            'event':   serializer.data,
        }, status=status.HTTP_201_CREATED)

    except Exception:
        logger.exception("Erreur lors de la création d'un événement")
        return Response(
            {'detail': "Impossible de créer l'événement pour le moment. Réessayez."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ════════════════════════════════════════════════════════════════
# VIEW : detail_evenement_organisateur
# GET /api/events/<event_id>/detail/
# ════════════════════════════════════════════════════════════════
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def detail_evenement_organisateur(request, event_id):
    """
    Retourne le détail complet d'un événement pour son organisateur.
    Inclut les statistiques des invitations par statut.
    """
    try:
        event = Event.objects.get(
            id                 = event_id,
            organizer          = request.user,
            deleted_at__isnull = True,
        )
    except Event.DoesNotExist:
        return Response(
            {'detail': 'Événement introuvable.'},
            status=status.HTTP_404_NOT_FOUND
        )

    from invitations.models import Invitation
    invitations_count = {
        'sent':      event.invitations.filter(status='sent').count(),
        'confirmed': event.invitations.filter(status='confirmed').count(),
        'declined':  event.invitations.filter(status='declined').count(),
        'total':     event.invitations.exclude(status='revoked').count(),
    }

    serializer = EventPublicSerializer(event, context={'request': request})
    return Response({
        # template_config (galerie…) : réservé à l'organisateur, pour l'écran « Modifier »
        'event':       {**serializer.data, 'template_config': event.template_config or {}},
        'invitations': invitations_count,
    })


# ════════════════════════════════════════════════════════════════
# VIEW : modifier_evenement
# PATCH /api/events/<event_id>/update/
# ════════════════════════════════════════════════════════════════
@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def modifier_evenement(request, event_id):
    """
    Modifie un événement existant.
    PATCH = mise à jour partielle : on n'envoie que les champs à modifier.
    Seul l'organisateur peut modifier son événement.
    """
    try:
        event = Event.objects.get(
            id                 = event_id,
            organizer          = request.user,
            deleted_at__isnull = True,
        )
    except Event.DoesNotExist:
        return Response(
            {'detail': 'Événement introuvable.'},
            status=status.HTTP_404_NOT_FOUND
        )

    data = request.data if isinstance(request.data, dict) else {}
    from .lifecycle import notify_changes, snapshot
    from .validation import EventInputError, clean_core
    try:
        core = clean_core(data, current=event)
    except EventInputError as exc:
        return Response({'detail': exc.message, **({exc.field: exc.message} if exc.field else {})},
                        status=status.HTTP_400_BAD_REQUEST)
    before = snapshot(event)
    for field, value in core.items():
        setattr(event, field, value)
    if 'event_type' in data:
        event.event_type = data['event_type']
    if 'visibility' in data:
        if data['visibility'] not in VISIBILITIES:
            return Response({'detail': 'Visibilité invalide (public ou private).'}, status=status.HTTP_400_BAD_REQUEST)
        event.visibility = data['visibility']

    # Billetterie & dress code, type libre et palette
    if 'event_type' in data and data['event_type'] not in Event.EventType.values:
        return Response({'detail': "Type d'événement invalide."}, status=status.HTTP_400_BAD_REQUEST)
    ticketing, ticket_errors = clean_ticketing(data, current=event)
    style, style_errors = clean_style(data, data.get('event_type', event.event_type))
    ticket_errors.update(style_errors)
    ticketing.update(style)
    if ticket_errors:
        first = next(iter(ticket_errors.values()))
        return Response({'detail': first, **ticket_errors}, status=status.HTTP_400_BAD_REQUEST)
    for field, value in ticketing.items():
        setattr(event, field, value)

    event.save()
    # Date, heure ou lieu modifiés : les participants sont prévenus
    notified = notify_changes(event, before)

    serializer = EventPublicSerializer(event, context={'request': request})
    return Response({
        'message': 'Événement modifié avec succès.',
        'notified': notified,
        'event':   serializer.data,
    })


# ════════════════════════════════════════════════════════════════
# VIEW : publier_evenement
# POST /api/events/<event_id>/publish/
# ════════════════════════════════════════════════════════════════
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def publier_evenement(request, event_id):
    """
    Publie ou dépublie un événement.

    Publication (draft → published) :
    - L'événement apparaît dans le fil de découverte (si public)
    - Les invités reçoivent une notification

    Dépublication (published → draft) :
    - L'événement disparaît du fil public
    - Les invitations existantes restent actives
    """
    try:
        event = Event.objects.get(
            id                 = event_id,
            organizer          = request.user,
            deleted_at__isnull = True,
        )
    except Event.DoesNotExist:
        return Response(
            {'detail': 'Événement introuvable.'},
            status=status.HTTP_404_NOT_FOUND
        )

    # ── Dépublication ─────────────────────────────────────────────
    if event.status == 'published':
        from tickets.models import Ticket
        if Ticket.objects.filter(event=event, status=Ticket.Status.GENERATED).exists():
            # Des participants ont déjà leur ticket : dépublier les priverait d'accès sans les prévenir
            return Response({
                'detail': "Des participants ont déjà leur ticket : vous ne pouvez plus dépublier cet événement. "
                          "Modifiez-le, ou supprimez-le pour l'annuler (les participants seront prévenus et remboursés).",
                'code': 'has_participants'}, status=status.HTTP_409_CONFLICT)
        event.status = 'draft'
        event.save()
        return Response({
            'message':  'Événement dépublié. Il n\'est plus visible dans le fil de découverte.',
            'status':   event.status,
        })

    # ── Publication ───────────────────────────────────────────────
    # Vérifications minimales avant publication
    if not event.title:
        return Response(
            {'detail': 'Le titre est obligatoire pour publier.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    if not event.start_date or not event.end_date:
        return Response(
            {'detail': 'Les dates sont obligatoires pour publier.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    data = request.data if isinstance(request.data, dict) else {}
    visibility = data.get('visibility', event.visibility)
    if visibility not in VISIBILITIES:
        return Response({'detail': 'Visibilité invalide (public ou private).'}, status=status.HTTP_400_BAD_REQUEST)
    event.status     = 'published'
    event.visibility = visibility
    event.save()

    # Par celui-ci :
    is_private = event.visibility == 'private'
    return Response({
    'message': (
        'Événement publié. Vos invités peuvent maintenant accéder à votre événement.'
        if is_private else
        'Événement publié avec succès. Il est maintenant visible dans le fil de découverte.'
    ),
    'status':     event.status,
    'visibility': event.visibility,
    'subdomain':  event.subdomain,
})


# ════════════════════════════════════════════════════════════════
# VIEW : supprimer_evenement
# DELETE /api/events/<event_id>/delete/
# ════════════════════════════════════════════════════════════════
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def supprimer_evenement(request, event_id):
    """
    Supprime un événement via soft delete.

    Soft delete = on ne supprime pas la ligne en base de données.
    On horodate deleted_at et on archive l'événement.
    Pourquoi ? Pour conserver l'historique et l'intégrité des données
    (invitations, statistiques, billets...).
    """
    try:
        event = Event.objects.get(
            id                 = event_id,
            organizer          = request.user,
            deleted_at__isnull = True,
        )
    except Event.DoesNotExist:
        return Response(
            {'detail': 'Événement introuvable.'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Tickets annulés (remboursés s'ils étaient payés), invitations retirées, participants prévenus
    from .lifecycle import cancel_event
    result = cancel_event(event)
    message = 'Événement supprimé.'
    if result['notified']:
        message += f" {result['notified']} participant{'s' if result['notified'] > 1 else ''} prévenu{'s' if result['notified'] > 1 else ''}."
    if result['refunds_failed']:
        message += (f" {result['refunds_failed']} remboursement(s) n'ont pas pu être faits automatiquement : "
                    "faites-les depuis votre tableau de bord Stripe.")
    return Response({'message': message, **result})


# Les invités (participants, invitations, relances) sont gérés dans
# invitations/organizer_views.py (M12, M13).


# ════════════════════════════════════════════════════════════════
# VIEW : revoquer_invitation
# DELETE /api/invitations/<invitation_id>/revoke/
# ════════════════════════════════════════════════════════════════
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def revoquer_invitation(request, invitation_id):
    """
    Révoque une invitation (status → 'revoked').
    L'invité n'aura plus accès à l'événement.
    Seul l'organisateur de l'événement peut révoquer une invitation.
    """
    from invitations.models import Invitation

    try:
        invitation = Invitation.objects.select_related('event', 'invited_user').get(
            id               = invitation_id,
            event__organizer = request.user,  # vérifie que c'est bien son événement
            event__deleted_at__isnull = True,
        )
    except Invitation.DoesNotExist:
        return Response(
            {'detail': 'Invitation introuvable ou vous n\'avez pas les droits pour la révoquer.'},
            status=status.HTTP_404_NOT_FOUND
        )

    if invitation.status == 'revoked':
        return Response({'message': 'Invitation déjà révoquée.'})
    # Le ticket de l'invité est annulé (remboursé s'il était payé) et il est prévenu
    from .lifecycle import revoke_invitation
    result = revoke_invitation(invitation)
    message = 'Invitation révoquée.'
    if result['refunds_failed']:
        message += " Le remboursement n'a pas pu être fait automatiquement : faites-le depuis votre tableau de bord Stripe."
    return Response({'message': message, **result})