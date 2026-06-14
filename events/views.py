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


# ════════════════════════════════════════════════════════════════
# VIEW : liste_evenements_publics
# GET /api/events/publics/
# ════════════════════════════════════════════════════════════════
from datetime import datetime

from django.db import connection

@api_view(['GET'])
@permission_classes([AllowAny])
def liste_evenements_publics(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) 
            FROM events 
            WHERE status = 'published' 
            AND deleted_at IS NULL
        """)
        count = cursor.fetchone()[0]

    return Response({
        'raw_count_from_db': count,
        'debug_version': 'v4-raw-sql'
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

    serializer = EventPublicSerializer(event)
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
    evenements = Event.objects.filter(
        organizer          = request.user,
        deleted_at__isnull = True,
    ).order_by('-created_at')

    serializer = EventPublicSerializer(evenements, many=True)
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
    except Exception as e:
        return Response(
            {'detail': f'Erreur upload Cloudinary : {str(e)}'},
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
            title            = data['title'],
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
            ambiance         = data.get('ambiance', ''),
            palette          = data.get('palette'),
            visibility       = data.get('visibility', 'draft'),
            status           = 'draft',  # Toujours brouillon à la création
            subdomain        = subdomain,
            template_config  = data.get('template_config'),
        )

        serializer = EventPublicSerializer(event)
        return Response({
            'message': 'Événement créé avec succès.',
            'event':   serializer.data,
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response(
            {'detail': f'Erreur lors de la création : {str(e)}'},
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

    serializer = EventPublicSerializer(event)
    return Response({
        'event':       serializer.data,
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
    if 'title'            in data: event.title            = data['title']
    if 'description'      in data: event.description      = data['description']
    if 'event_type'       in data: event.event_type       = data['event_type']
    if 'location_address' in data: event.location_address = data['location_address']
    if 'is_online'        in data: event.is_online        = data['is_online']
    if 'online_link'      in data: event.online_link      = data['online_link']
    if 'cover_image'      in data: event.cover_image      = data['cover_image']
    if 'ambiance'         in data: event.ambiance         = data['ambiance']
    if 'palette'          in data: event.palette          = data['palette']
    if 'visibility'       in data: event.visibility       = data['visibility']
    if 'template_config'  in data: event.template_config  = data['template_config']

    if 'start_date' in data:
        parsed = parse_datetime(data['start_date'])
        if parsed: event.start_date = parsed

    if 'end_date' in data:
        parsed = parse_datetime(data['end_date'])
        if parsed: event.end_date = parsed

    event.save()

    serializer = EventPublicSerializer(event)
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

    event.status     = 'published'
    event.visibility = request.data.get('visibility', event.visibility)
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


# ════════════════════════════════════════════════════════════════
# VIEW : participants_evenement
# GET /api/events/<event_id>/participants/
# ════════════════════════════════════════════════════════════════
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def participants_evenement(request, event_id):
    """
    Retourne la liste complète des invités d'un événement.
    Inclut les confirmés, en attente, et déclinés.
    Les invitations révoquées sont exclues.
    Réservé exclusivement à l'organisateur.
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

    invitations = Invitation.objects.filter(
        event = event,
    ).exclude(status='revoked').select_related('invited_user')

    data = []
    for inv in invitations:
        entry = {
            'id':           str(inv.id),
            'status':       inv.status,
            'channel':      inv.channel,
            'sent_at':      inv.sent_at.isoformat()      if inv.sent_at      else None,
            'responded_at': inv.responded_at.isoformat() if inv.responded_at else None,
        }
        if inv.invited_user:
            entry['user'] = {
                'id':         str(inv.invited_user.id),
                'first_name': inv.invited_user.first_name,
                'last_name':  inv.invited_user.last_name,
                'email':      inv.invited_user.email,
                'avatar_url': inv.invited_user.avatar_url,
            }
        else:
            # Contact externe invité par SMS
            entry['user'] = {
                'phone_number': inv.phone_number,
            }
        data.append(entry)

    return Response({
        'count':        len(data),
        'participants': data,
    })


# ════════════════════════════════════════════════════════════════
# VIEW : inviter_participant
# POST /api/events/<event_id>/invite/
# ════════════════════════════════════════════════════════════════
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def inviter_participant(request, event_id):
    """
    Envoie une invitation à un participant.

    Deux canaux possibles :
    1. Email  → si l'email correspond à un compte Easevent :
                notification in-app + email
                sinon : email avec lien d'accès direct
    2. Téléphone → SMS avec lien d'accès (via Twilio en production)

    Body JSON (l'un ou l'autre) :
    { "email": "sarah@example.com" }
    { "phone_number": "+33612345678" }

    Erreurs possibles :
    - Vous ne pouvez pas vous inviter vous-même
    - Cette personne a déjà une invitation active
    - Email ou téléphone manquant
    """
    from invitations.models import Invitation
    from users.models       import User as UserModel

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

    email        = request.data.get('email', '').strip()
    phone_number = request.data.get('phone_number', '').strip()

    if not email and not phone_number:
        return Response(
            {'detail': 'Veuillez fournir un email ou un numéro de téléphone pour envoyer l\'invitation.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # ── Empêcher l'organisateur de s'inviter lui-même ─────────────
    if email and email.lower() == request.user.email.lower():
        return Response(
            {'detail': 'Vous ne pouvez pas vous inviter vous-même. Vous êtes déjà l\'organisateur de cet événement.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Date d'expiration : 7 jours après la fin de l'événement
    expires_at = event.end_date + timezone.timedelta(days=7)

    # ── Invitation par email ──────────────────────────────────────
    if email:
        # Chercher si cet email correspond à un compte Easevent
        invited_user = UserModel.objects.filter(email__iexact=email).first()

        # Vérifier qu'une invitation active n'existe pas déjà
        if invited_user:
            already_invited = Invitation.objects.filter(
                event        = event,
                invited_user = invited_user,
            ).exclude(status='revoked').exists()
        else:
            already_invited = False

        if already_invited:
            return Response(
                {'detail': f'{email} a déjà reçu une invitation à cet événement. Vous pouvez la révoquer et en envoyer une nouvelle si nécessaire.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Créer l'invitation
        invitation = Invitation.objects.create(
            event        = event,
            invited_user = invited_user,       # None si non inscrit
            phone_number = None,
            token        = secrets.token_urlsafe(32),
            status       = 'sent',
            channel      = 'platform_notification' if invited_user else 'sms',
            expires_at   = expires_at,
        )

        # Envoyer un email de notification
        try:
            organizer_name = f"{request.user.first_name} {request.user.last_name}"
            subject = f'Invitation à "{event.title}"'

            if invited_user:
                # Utilisateur inscrit → message personnalisé
                message = f"""
Bonjour {invited_user.first_name},

{organizer_name} vous invite à "{event.title}".

Connectez-vous à Easevent pour voir votre invitation et confirmer votre présence.

À bientôt,
L'équipe Easevent
                """.strip()
            else:
                # Utilisateur non inscrit → lien d'accès direct
                access_link = f"https://easevent.app/invitation/{invitation.token}"
                message = f"""
Bonjour,

{organizer_name} vous invite à son événement "{event.title}".

Cliquez sur ce lien pour accéder à votre invitation :
{access_link}

Ce lien est personnel et non transférable.

À bientôt,
L'équipe Easevent
                """.strip()

            send_mail(
                subject        = subject,
                message        = message,
                from_email     = 'dosyca35@gmail.com',
                recipient_list = [email],
                fail_silently  = True,  # Ne pas planter si l'email échoue
            )
        except Exception as e:
            # L'invitation est créée même si l'email échoue
            print(f'Erreur envoi email invitation: {e}')

        return Response({
            'message':       f'Invitation envoyée à {email}.',
            'invitation_id': str(invitation.id),
        }, status=status.HTTP_201_CREATED)

    # ── Invitation par téléphone (SMS) ────────────────────────────
    if phone_number:
        already_invited = Invitation.objects.filter(
            event        = event,
            phone_number = phone_number,
        ).exclude(status='revoked').exists()

        if already_invited:
            return Response(
                {'detail': f'Le numéro {phone_number} a déjà reçu une invitation à cet événement.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        invitation = Invitation.objects.create(
            event        = event,
            invited_user = None,
            phone_number = phone_number,
            token        = secrets.token_urlsafe(32),
            status       = 'sent',
            channel      = 'sms',
            expires_at   = expires_at,
        )

        # TODO Production : envoyer le SMS via Twilio
        # from twilio.rest import Client
        # client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        # client.messages.create(
        #     body = f'Vous êtes invité à "{event.title}". Accédez à votre invitation : https://easevent.app/invitation/{invitation.token}',
        #     from_ = settings.TWILIO_PHONE_NUMBER,
        #     to    = phone_number,
        # )

        return Response({
            'message':       f'Invitation créée pour le {phone_number}. (SMS en production via Twilio)',
            'invitation_id': str(invitation.id),
        }, status=status.HTTP_201_CREATED)


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