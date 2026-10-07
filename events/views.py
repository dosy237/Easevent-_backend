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
    if event.visibility == 'private':
        # Un visiteur non connecté ne peut jamais voir un événement privé
        if not request.user.is_authenticated:
            return Response(
                {'error': 'Cet événement est privé. Vous devez être invité pour y accéder.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # L'organisateur a toujours accès à son propre événement
        is_organizer = (event.organizer == request.user)

        if not is_organizer:
            # Vérifier qu'il y a une invitation valide pour cet utilisateur
            from invitations.models import Invitation
            has_invitation = Invitation.objects.filter(
                event        = event,
                invited_user = request.user,
            ).exclude(status__in=['revoked', 'expired']).exists()

            if not has_invitation:
                return Response(
                    {'error': 'Cet événement est privé. Vous n\'avez pas été invité.'},
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
    data = request.data

    # ── Validation des champs obligatoires ───────────────────────
    required = ['title', 'event_type', 'start_date', 'end_date']
    for field in required:
        if not data.get(field):
            return Response(
                {'detail': f'Le champ "{field}" est obligatoire.'},
                status=status.HTTP_400_BAD_REQUEST
            )

    # ── Conversion des dates ISO → objets datetime Python ────────
    # parse_datetime("2026-09-15T18:00:00") → datetime(2026, 9, 15, 18, 0, 0)
    # Sans cette conversion Django plante avec "'str' object has no attribute 'day'"
    start_date_parsed = parse_datetime(data['start_date'])
    end_date_parsed   = parse_datetime(data['end_date'])

    if not start_date_parsed:
        return Response(
            {'detail': 'Format de date de début invalide. Utilisez YYYY-MM-DDTHH:MM:SS'},
            status=status.HTTP_400_BAD_REQUEST
        )
    if not end_date_parsed:
        return Response(
            {'detail': 'Format de date de fin invalide. Utilisez YYYY-MM-DDTHH:MM:SS'},
            status=status.HTTP_400_BAD_REQUEST
        )
    if end_date_parsed <= start_date_parsed:
        return Response(
            {'detail': 'La date de fin doit être après la date de début.'},
            status=status.HTTP_400_BAD_REQUEST
        )

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
    base_slug = slugify(data.get('title', ''))[:80]
    subdomain = base_slug
    counter   = 1
    while Event.objects.filter(subdomain=subdomain).exists():
        subdomain = f"{base_slug}-{counter}"
        counter  += 1

    try:
        event = Event.objects.create(
            organizer        = request.user,
            title            = str(data['title']).strip()[:100],
            event_type       = data['event_type'],
            description      = data.get('description', ''),
            start_date       = start_date_parsed,
            end_date         = end_date_parsed,
            location_address = data.get('location_address', ''),
            latitude         = data.get('latitude'),
            longitude        = data.get('longitude'),
            is_online        = data.get('is_online', False),
            online_link      = data.get('online_link'),
            cover_image      = data.get('cover_image'),
            visibility       = visibility,
            status           = 'draft',  # Toujours brouillon à la création
            subdomain        = subdomain,
            template_config  = data.get('template_config'),
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

    data = request.data

    # Mise à jour uniquement des champs présents dans la requête
    if 'title' in data:
        title = (data['title'] or '').strip()
        if not title:
            return Response({'detail': 'Le titre est obligatoire.'}, status=status.HTTP_400_BAD_REQUEST)
        event.title = title[:100]
    if 'description'      in data: event.description      = data['description']
    if 'event_type'       in data: event.event_type       = data['event_type']
    if 'location_address' in data:
        if data['location_address'] != event.location_address and 'latitude' not in data:
            # Nouvelle adresse saisie sans suggestion : anciennes coordonnées invalides
            event.latitude = event.longitude = None
        event.location_address = data['location_address']
    for field in ('latitude', 'longitude'):
        if field in data:
            try:
                value = None if data[field] in (None, '') else float(data[field])
            except (TypeError, ValueError):
                return Response({'detail': 'Coordonnées invalides.'}, status=status.HTTP_400_BAD_REQUEST)
            setattr(event, field, value)
    if 'is_online'        in data: event.is_online        = data['is_online']
    if 'online_link'      in data: event.online_link      = data['online_link']
    if 'cover_image'      in data: event.cover_image      = data['cover_image']
    if 'visibility'       in data:
        if data['visibility'] not in VISIBILITIES:
            return Response({'detail': 'Visibilité invalide (public ou private).'}, status=status.HTTP_400_BAD_REQUEST)
        event.visibility = data['visibility']
    if 'template_config'  in data: event.template_config  = data['template_config']

    if 'start_date' in data:
        parsed = parse_datetime(data['start_date'])
        if parsed: event.start_date = parsed

    if 'end_date' in data:
        parsed = parse_datetime(data['end_date'])
        if parsed: event.end_date = parsed

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

    serializer = EventPublicSerializer(event, context={'request': request})
    return Response({
        'message': 'Événement modifié avec succès.',
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

    visibility = request.data.get('visibility', event.visibility)
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

    event.deleted_at = timezone.now()
    event.status     = 'archived'
    event.save()

    return Response({'message': 'Événement supprimé avec succès.'})


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
        invitation = Invitation.objects.get(
            id               = invitation_id,
            event__organizer = request.user,  # vérifie que c'est bien son événement
        )
    except Invitation.DoesNotExist:
        return Response(
            {'detail': 'Invitation introuvable ou vous n\'avez pas les droits pour la révoquer.'},
            status=status.HTTP_404_NOT_FOUND
        )

    invitation.status = 'revoked'
    invitation.save()

    return Response({'message': 'Invitation révoquée avec succès.'})