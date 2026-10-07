"""
easevent/asgi.py — HTTP + WebSocket (Django Channels)

En production, le service « realtime » (daphne) sert /ws/ ; l'API HTTP
reste servie par gunicorn (WSGI). Ce point d'entrée sait faire les deux.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'easevent.settings')
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter   # noqa: E402  (après l'initialisation de Django)
from django.urls import path                                  # noqa: E402

from messaging.consumers import UserConsumer                  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    # Authentification par le premier message (JWT) : pas de cookie, donc
    # pas de détournement inter-sites ; les applications natives n'envoient
    # pas d'en-tête Origin.
    'websocket': URLRouter([path('ws/', UserConsumer.as_asgi())]),
})
