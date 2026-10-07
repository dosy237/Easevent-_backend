# events/serializers.py
# ════════════════════════════════════════════════════════════════
# Serializers pour les événements.
#
# C'est quoi un Serializer ?
# ──────────────────────────
# Un serializer est un traducteur entre Python et JSON.
# Django stocke les données en objets Python (Event, User...).
# Le front-end React Native parle JSON.
# Le serializer fait la traduction dans les deux sens :
#   Python → JSON (quand on envoie des données au front)
#   JSON → Python (quand on reçoit des données du front)
# ════════════════════════════════════════════════════════════════

from rest_framework import serializers
from .models        import Event


class EventPublicSerializer(serializers.ModelSerializer):
    """
    Serializer pour les événements publics.
    Expose uniquement les champs nécessaires au front-end.
    Ajoute des champs calculés : date_formatted, confirmed_count,
    cover_image et distance_km.
    """

    # SerializerMethodField = champ calculé par une méthode Python.
    # Le nom de la méthode doit être get_<nom_du_champ>.
    # Ces champs n'existent pas dans la base de données —
    # ils sont construits à la volée lors de la sérialisation.
    date_formatted  = serializers.SerializerMethodField()
    confirmed_count = serializers.SerializerMethodField()
    cover_image     = serializers.SerializerMethodField()

    # distance_km : distance entre l'utilisateur et l'événement.
    # Calculée dans la view quand l'utilisateur partage sa position GPS.
    # Vaut None si la géolocalisation est désactivée.
    distance_km     = serializers.SerializerMethodField()
    event_type_display = serializers.SerializerMethodField()
    spots_left         = serializers.SerializerMethodField()
    my_ticket          = serializers.SerializerMethodField()
    organizer          = serializers.SerializerMethodField()
    map                = serializers.SerializerMethodField()
    online_link        = serializers.SerializerMethodField()
    gallery            = serializers.SerializerMethodField()

    has_minisite = serializers.SerializerMethodField()
    likes_count = serializers.SerializerMethodField()
    liked = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()
    pass_word = serializers.SerializerMethodField()

    class Meta:
        # model : quel modèle Django ce serializer traduit
        model = Event

        # fields : liste exacte des champs envoyés au front-end.
        # On n'envoie PAS template_config, palette, deleted_at
        # (inutile pour l'affichage public et trop lourd).
        # L'ordre ici correspond à l'ordre dans la réponse JSON.
        fields = [
            'id',
            'title',
            'event_type',
            'event_type_label',    # type libre quand event_type = « autre »
            'event_type_display',  # calculé — libellé à afficher
            'description',
            'date_formatted',   # calculé — ex: "13 JUIN"
            'start_date',
            'end_date',
            'location_address',
            'latitude',
            'longitude',
            'confirmed_count',  # calculé — nombre d'invités confirmés
            'view_count',
            'ambiance',
            'ambiance_label',      # ambiance libre quand ambiance = « autre »
            'theme',               # thème de l'événement (fil conducteur du mini-site)
            'assistant_enabled',   # réponses automatiques aux questions des participants
            'timezone',            # fuseau du lieu (l'application affiche aussi l'heure locale du visiteur)
            'palette',             # couleurs choisies librement {primary, secondary}
            'subdomain',
            'cover_image',      # calculé — URL de l'image de couverture
            'distance_km',      # calculé — distance en km (ou null)
            # Billetterie (M23) — prix en chaîne décimale "25.00"
            'is_paid',
            'price',
            'currency',
            'max_guests',
            'dress_code',
            'visibility',
            'status',
            'is_online',
            'online_link',
            'spots_left',          # places restantes (null = illimité)
            'my_ticket',           # ticket actif de l'utilisateur connecté (détail)
            'organizer',           # nom de l'organisateur
            'map',                 # carte du lieu + liens Google Maps (voir, itinéraire)
            'gallery',             # photos de la galerie (URL absolues)
            'has_minisite',        # un mini-site a été choisi (affiché dans l'application)
            'likes_count',         # nombre de « J'aime »
            'liked',               # l'utilisateur connecté a aimé
            'share_url',           # lien de partage avec aperçu (événements publics publiés)
            'online_link_public',  # lien en ligne visible par tous (sinon : dans le billet / l'invitation)
            'pass_word',           # « invitation » ou « billet » (formes et accords)
            'video',               # vidéo de présentation : url, poster, légende, durée, dimensions
        ]

    def get_pass_word(self, obj):
        from .wording import pass_word
        return pass_word(obj)

    def get_likes_count(self, obj):
        n = getattr(obj, 'likes_n', None)                 # annoté dans les listes (une seule requête)
        return n if n is not None else obj.likes.count()

    def get_liked(self, obj):
        flag = getattr(obj, 'liked_by_me', None)
        if flag is not None:
            return bool(flag)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        return bool(user and user.is_authenticated and obj.likes.filter(user=user).exists())

    def get_share_url(self, obj):
        if obj.status != 'published' or obj.visibility != 'public' or obj.deleted_at is not None:
            return None
        from easevent.media import absolute_url
        return absolute_url(f'/e/{obj.id}/', self.context.get('request'))

    def get_has_minisite(self, obj):
        return bool((obj.minisite_config or {}).get('spec'))

    def get_date_formatted(self, obj):
        """
        obj : l'objet Event Python en cours de sérialisation.

        Formate start_date en chaîne lisible : '14 JUIN'
        Cette chaîne est affichée directement sur les cards.

        Pourquoi ne pas formater la date côté front-end ?
        Parce que le backend connaît la langue (français),
        et ça évite de dupliquer la logique de formatage.
        """
        if not obj.start_date:
            return ''
        mois = {
            1:'JAN', 2:'FÉV', 3:'MAR', 4:'AVR',
            5:'MAI', 6:'JUIN', 7:'JUIL', 8:'AOÛ',
            9:'SEP', 10:'OCT', 11:'NOV', 12:'DÉC'
        }
        from .tz import local
        start = local(obj.start_date, obj)               # jour dans le fuseau de l'événement
        return f"{start.day} {mois[start.month]}"

    def get_confirmed_count(self, obj):
        """
        Compte les invitations avec status='confirmed' pour cet événement.

        obj.invitations : relation inverse définie dans models.py
        via related_name='invitations' sur le ForeignKey event.

        .filter() : requête SQL WHERE status='confirmed'
        .count()  : COUNT(*) — plus efficace que len(queryset)
                    car il ne charge pas tous les objets en mémoire,
                    juste le nombre.
        """
        return obj.invitations.filter(status='confirmed').count()

    def get_cover_image(self, obj):
        """
        URL ABSOLUE de l'image de couverture, servie par le backend
        (ou Cloudinary pour les photos uploadées depuis l'application).
        """
        from easevent.media import public_url

        # Priorité 0 : champ image direct (uploadé ou seedé)
        url = obj.cover_image

        # Priorité 1 : photo uploadée et traitée via EventMedia
        if not url:
            media = obj.media.filter(
                media_type        = 'photo',
                processing_status = 'done',
                is_approved       = True,
            ).first()
            if media:
                url = media.processed_url or media.original_url

        # Priorité 2 : URL dans la configuration du template
        if not url and obj.template_config:
            url = obj.template_config.get('cover_image')

        return public_url(url, self.context.get('request'))

    def get_event_type_display(self, obj):
        """« Baptême » pour un type libre, sinon le libellé du type (« Conférence »)."""
        if obj.event_type == 'autre' and obj.event_type_label:
            return obj.event_type_label
        return obj.get_event_type_display()

    def get_spots_left(self, obj):
        if not obj.max_guests:
            return None
        from tickets.services import spots_left
        return spots_left(obj)

    def get_my_ticket(self, obj):
        """Uniquement dans les vues détail (context['with_my_ticket']) : évite N requêtes sur les listes."""
        request = self.context.get('request')
        if not self.context.get('with_my_ticket') or not request or not request.user.is_authenticated:
            return None
        from tickets.services import active_ticket
        ticket = active_ticket(obj, request.user)
        if not ticket:
            return None
        return {'id': str(ticket.id), 'status': ticket.status, 'payment_status': ticket.payment_status}

    def get_online_link(self, obj):
        """
        Le lien de connexion d'un événement en ligne n'est donné qu'à
        l'organisateur et aux participants qui ont un ticket généré : sinon
        un événement payant serait accessible sans payer.
        """
        if not obj.is_online or not obj.online_link:
            return None
        if obj.online_link_public:                 # l'organisateur a choisi : ouvert à tous
            return obj.online_link
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return None
        from .team import role_of
        if role_of(obj, user):
            return obj.online_link
        from tickets.models import Ticket
        has_ticket = Ticket.objects.filter(event=obj, user=user, status=Ticket.Status.GENERATED).exists()
        return obj.online_link if has_ticket else None

    def get_gallery(self, obj):
        from easevent.media import public_url
        items = (obj.template_config or {}).get('gallery') or []
        request = self.context.get('request')
        return [public_url(u, request) for u in items if isinstance(u, str) and u][:6]

    def get_map(self, obj):
        from .geo import maps_links, static_map_url
        if obj.is_online or not (obj.location_address or obj.latitude is not None):
            return None
        lat = float(obj.latitude) if obj.latitude is not None else None
        lng = float(obj.longitude) if obj.longitude is not None else None
        return {
            'image': static_map_url(lat, lng, self.context.get('request')),
            'lat': lat, 'lng': lng,
            **maps_links(obj.location_address, lat, lng),
        }

    def get_organizer(self, obj):
        return {'id': str(obj.organizer_id), 'name': obj.organizer.full_name}

    def get_distance_km(self, obj):
        """
        Retourne la distance en km entre l'utilisateur et l'événement.

        Cette valeur n'est PAS calculée ici — elle est calculée
        dans la view (liste_evenements_publics) avec la formule
        de Haversine, puis attachée dynamiquement à l'objet Event
        via obj._distance_km.

        getattr(obj, '_distance_km', None) :
        - Si _distance_km existe sur l'objet → retourne sa valeur
        - Sinon → retourne None (géolocalisation désactivée)

        Exemples de valeurs retournées :
        - 2.3  → "2.3 km" affiché sur la card
        - 0.5  → "500 m" (le front-end peut formater)
        - None → pas de distance affichée (visiteur sans GPS)
        """
        return getattr(obj, '_distance_km', None)