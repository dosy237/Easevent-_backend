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
            'subdomain',
            'cover_image',      # calculé — URL de l'image de couverture
            'distance_km',      # calculé — distance en km (ou null)
        ]

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
        return f"{obj.start_date.day} {mois[obj.start_date.month]}"

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
        Retourne l'URL de l'image de couverture de l'événement.

        Ordre de priorité :
        1. Photo uploadée par l'organisateur (traitement terminé + approuvée)
        2. URL stockée dans template_config (image externe configurée)
        3. None si aucune image disponible

        Pourquoi ce champ est calculé et non stocké directement ?
        Parce que l'image peut venir de plusieurs sources selon
        l'avancement de la création de l'événement.

        obj.media : relation inverse vers EventMedia
        processing_status='done' : traitement Celery terminé
        is_approved=True : validée par l'organisateur
        .first() : retourne le premier résultat ou None
        """
        # Priorité 1 : photo uploadée et traitée
        media = obj.media.filter(
            media_type        = 'photo',
            processing_status = 'done',
            is_approved       = True,
        ).first()

        if media:
            # processed_url = photo après traitement (compression, harmonisation)
            # original_url  = photo brute si le traitement n'a pas encore produit
            #                 de version traitée
            return media.processed_url or media.original_url

        # Priorité 2 : URL dans la configuration du template
        # template_config est un champ JSONB — on accède aux clés
        # comme un dictionnaire Python
        if obj.template_config:
            return obj.template_config.get('cover_image')

        # Aucune image disponible
        return None

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