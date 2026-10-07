# easevent/urls.py
# ═══════════════════════════════════════════════════════
# Fichier de routage principal de Django.
# Connecte toutes les URLs de toutes les apps.
# ═══════════════════════════════════════════════════════

from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve as static_serve
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from events import geo_views
from invitations import organizer_views as guests, public_views as invitation_pages

from events import engagement as event_engagement

urlpatterns = [
    # Interface d'administration Django
    path('admin/', admin.site.urls), 

    # API Events — /api/events/publics/
    path('api/events/', include('events.urls')),
    path('api/subscriptions/', include('subscriptions.urls')),
    path('api/auth/',   include('users.urls')),
    path('api/invitations/', include('invitations.urls')),
    path('api/', include('tickets.urls')),
    path('api/notifications/', include('notifications.urls')),
    path('api/conversations/', include('messaging.urls')),
    path('api/friends/', include('social.urls')),

    # Annuaire des membres (M12 mode Membres, M29)
    path('api/users/search/', guests.search_users,  name='users-search'),
    path('api/users/lookup/', guests.lookup_emails, name='users-lookup'),

    # Adresses et cartes
    path('api/geo/search/',            geo_views.search,     name='geo-search'),
    path('api/geo/place/<str:place_id>/', geo_views.place,   name='geo-place'),
    path('api/geo/static-map/',        geo_views.static_map, name='geo-static-map'),

    # Pages web publiques : lien d'invitation (M31) et confidentialité
    path('i/<str:token>/',     invitation_pages.invitation_page, name='invitation-page'),
    path('e/<uuid:event_id>/', event_engagement.share_page,      name='event-share-page'),
    path('confidentialite/',   invitation_pages.privacy_page,    name='privacy-page'),
    path('.well-known/assetlinks.json', invitation_pages.assetlinks, name='assetlinks'),
    path('.well-known/apple-app-site-association', invitation_pages.apple_app_site_association, name='aasa'),

]

# Swagger / OpenAPI : pas exposé en production (carte de l'API pour un attaquant)
if settings.API_DOCS:
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    ]

# Photos des événements (/media/...) servies par Django quand nginx ne le
# fait pas : l'application mobile charge toutes ses images depuis le serveur.
if settings.SERVE_MEDIA:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', static_serve, {'document_root': settings.MEDIA_ROOT}),
    ]
