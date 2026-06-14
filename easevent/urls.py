# easevent/urls.py
# ═══════════════════════════════════════════════════════
# Fichier de routage principal de Django.
# Connecte toutes les URLs de toutes les apps.
# ═══════════════════════════════════════════════════════

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Interface d'administration Django
    path('admin/', admin.site.urls),

    # API Events — /api/events/publics/
    path('api/events/', include('events.urls')),
    path('api/auth/',   include('users.urls')),
    path('api/invitations/', include('invitations.urls')),

]