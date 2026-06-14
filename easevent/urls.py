# easevent/urls.py
# ═══════════════════════════════════════════════════════
# Fichier de routage principal de Django.
# Connecte toutes les URLs de toutes les apps.
# ═══════════════════════════════════════════════════════

from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    # Interface d'administration Django
    path('admin/', admin.site.urls), 

    # API Events — /api/events/publics/
    path('api/events/', include('events.urls')),
    path('api/auth/',   include('users.urls')),
    path('api/invitations/', include('invitations.urls')),

    # Swagger / OpenAPI documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]