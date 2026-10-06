"""
easevent/settings.py
═══════════════════════════════════════════════════════════════
Configuration Django - Easevent
Production-ready pour Render.com
═══════════════════════════════════════════════════════════════
"""

import os
import sys
from pathlib import Path
from decouple import config
from datetime import timedelta
import cloudinary

BASE_DIR = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────
# SÉCURITÉ
# ─────────────────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1').split(',')
BASE_URL = config('BASE_URL', default='http://127.0.0.1:8003')

# Adresse publique HTTPS du serveur (ex. https://easevent.nitypulse.com).
# Sert à construire les URL absolues des images renvoyées à l'application
# mobile : Android refuse les images en http:// dans un APK de production.
PUBLIC_BASE_URL = config('PUBLIC_BASE_URL', default=BASE_URL).rstrip('/')

# Optionnel : URL d'une version web de l'application. Vide (cas de l'APK) →
# les liens des emails ouvrent des pages HTML servies par ce backend.
FRONTEND_URL = config('FRONTEND_URL', default='').rstrip('/')

TESTING = 'test' in sys.argv

# En-tête proxy SSL (obligatoire sur Render)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ─────────────────────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────────────────────
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_HEADERS = [
    'accept', 'accept-encoding', 'authorization', 'content-type',
    'dnt', 'origin', 'user-agent', 'x-csrftoken', 'x-requested-with',
]

CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000,http://127.0.0.1:3000').split(',')
CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=DEBUG, cast=bool)

# ─────────────────────────────────────────────────────────────
# MODÈLE UTILISATEUR
# ─────────────────────────────────────────────────────────────
AUTH_USER_MODEL = 'users.User'

# ─────────────────────────────────────────────────────────────
# APPLICATIONS
# ─────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    # Avant staticfiles : en local aussi, WhiteNoise sert /static/ (comme en production)
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_spectacular',
    'users',
    'events',
    'invitations',
    'analytics',
    'subscriptions',
    'tickets',
    'notifications',
    'messaging',
]

# ─────────────────────────────────────────────────────────────
# MIDDLEWARE
# ─────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'easevent.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'easevent.wsgi.application'

# ─────────────────────────────────────────────────────────────
# BASE DE DONNÉES
# ─────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='easevent_db'),
        'USER': config('DB_USER', default='easevent_user'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default='db'),
        'PORT': config('DB_PORT', default='5432'),
        'CONN_MAX_AGE': 60,
    }
}

# ─────────────────────────────────────────────────────────────
# DJANGO REST FRAMEWORK + JWT
# ─────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # OWASP API4 — limitation du nombre de requêtes (anti force brute / abus)
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon':           '120/min',
        'user':           '600/min',
        'auth_login':     '10/min',
        'auth_register':  '10/hour',
        'auth_email':     '6/hour',
        'password_reset': '6/hour',
        # Invitations (M12, M31)
        'user_search':    '60/min',
        'invite_send':    '30/hour',
        'invite_token':   '30/min',
        'messages':       '30/min',
        'phone_code':     '5/hour',
    },
}

# ─────────────────────────────────────────────────────────────
# MOTS DE PASSE (OWASP A07)
# Argon2id en premier : les anciens hachages PBKDF2 sont migrés
# automatiquement à la prochaine connexion de l'utilisateur.
# ─────────────────────────────────────────────────────────────
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.ScryptPasswordHasher',
]
if TESTING:
    # Hachage rapide uniquement pour la suite de tests
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Lien de réinitialisation du mot de passe valable 1 heure
PASSWORD_RESET_TIMEOUT = 60 * 60


SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# ─────────────────────────────────────────────────────────────
# REDIS + CELERY
# ─────────────────────────────────────────────────────────────
REDIS_URL = config('REDIS_URL', default='redis://localhost:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
    }
}
if TESTING:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

# Celery : worker (tâches de fond) + beat (planificateur), voir easevent/celery.py
# Dans Docker, REDIS_URL doit viser le service : redis://redis:6379/0
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Europe/Paris'
CELERY_TASK_IGNORE_RESULT = True              # aucun résultat stocké (sobriété)
CELERY_TASK_ACKS_LATE = True                  # une tâche interrompue est rejouée
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Côté application web : si Redis ne répond pas, l'envoi se fait directement
CELERY_BROKER_TRANSPORT_OPTIONS = {'socket_connect_timeout': 2, 'socket_timeout': 2, 'max_retries': 1}
if TESTING:
    CELERY_TASK_ALWAYS_EAGER = True           # tests : tâches exécutées sur place

# ─────────────────────────────────────────────────────────────
# FICHIERS STATIQUES & MÉDIAS
# ─────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
# Images de l'application mobile (logo, illustrations) : static/app/
STATICFILES_DIRS = [BASE_DIR / 'static']
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 7   # cache navigateur / app : 7 jours
from easevent.media import static_headers as _static_headers  # noqa: E402
WHITENOISE_ADD_HEADERS_FUNCTION = _static_headers

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
# Django sert /media/ lui-même (photos d'événements) si nginx ne le fait pas
SERVE_MEDIA = config('SERVE_MEDIA', default=True, cast=bool)

# ─────────────────────────────────────────────────────────────
# AUTRES CONFIGURATIONS
# ─────────────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Europe/Paris'
USE_I18N = True
USE_TZ = True

# ─────────────────────────────────────────────────────────────
# EMAIL + CLOUDINARY
# ─────────────────────────────────────────────────────────────
EMAIL_BACKEND = config('EMAIL_BACKEND', default='sendgrid_backend.SendgridBackend')
SENDGRID_API_KEY = config('SENDGRID_API_KEY')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='dosyca35@gmail.com')
SENDGRID_SANDBOX_MODE_IN_DEBUG = False

cloudinary.config(
    cloud_name=config('CLOUDINARY_CLOUD_NAME'),
    api_key=config('CLOUDINARY_API_KEY'),
    api_secret=config('CLOUDINARY_API_SECRET'),
    secure=True,
)

# ─────────────────────────────────────────────────────────────
# SWAGGER / SPECTACULAR
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# STRIPE (paiements) — clés UNIQUEMENT dans le .env du serveur
# ─────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY      = config('STRIPE_SECRET_KEY', default='')
STRIPE_WEBHOOK_SECRET  = config('STRIPE_WEBHOOK_SECRET', default='')
# Commission Easevent prélevée sur chaque ticket payant (en %, ex. 3)
PLATFORM_FEE_PERCENT   = config('PLATFORM_FEE_PERCENT', default=3, cast=float)
STRIPE_CONNECT_COUNTRY = config('STRIPE_CONNECT_COUNTRY', default='FR')

# ─────────────────────────────────────────────────────────────
# INVITATIONS (M12, M29–M31)
# ─────────────────────────────────────────────────────────────
# Invités par événement selon le plan (cahier des charges §2.5) — None = illimité
PLAN_GUEST_LIMITS = {'free': 50, 'standard': 500, 'pro': None}
INVITE_BATCH_MAX     = 100   # adresses / numéros par envoi
INVITE_REMIND_DELAY_HOURS = 24  # une relance par invité et par jour au plus
# ── Application mobile : ouverture depuis un lien, stores ──────────────
ANDROID_PACKAGE   = config('ANDROID_PACKAGE', default='com.eranis.easevent')
IOS_BUNDLE_ID     = config('IOS_BUNDLE_ID', default='com.eranis.easevent')
# Pages des stores (vides tant que l'app n'est pas publiée) et lien direct de l'APK
ANDROID_STORE_URL = config('ANDROID_STORE_URL', default='')
IOS_STORE_URL     = config('IOS_STORE_URL', default='')
APP_DOWNLOAD_URL  = config('APP_DOWNLOAD_URL', default='')
# Liens https ouverts directement dans l'app (Android App Links / iOS Universal Links)
ANDROID_CERT_SHA256 = [f.strip() for f in config('ANDROID_CERT_SHA256', default='').split(',') if f.strip()]
APPLE_TEAM_ID       = config('APPLE_TEAM_ID', default='')

INVITE_WAVE_SIZE    = 20     # invitations par vague d'envoi (worker Celery)
INVITE_WAVE_SECONDS = 15     # écart entre deux vagues

# SMS via Twilio (API REST). Sans ces variables, les invitations par SMS
# sont créées mais marquées « canal non configuré ».
# Clé de chiffrement des numéros (par défaut dérivée de SECRET_KEY)
PHONE_ENCRYPTION_KEY = config('PHONE_ENCRYPTION_KEY', default='')

TWILIO_ACCOUNT_SID          = config('TWILIO_ACCOUNT_SID', default='')
TWILIO_AUTH_TOKEN           = config('TWILIO_AUTH_TOKEN', default='')
TWILIO_FROM_NUMBER          = config('TWILIO_FROM_NUMBER', default='')
TWILIO_MESSAGING_SERVICE_SID = config('TWILIO_MESSAGING_SERVICE_SID', default='')

# ─────────────────────────────────────────────────────────────
# EN-TÊTES DE SÉCURITÉ (OWASP A05)
# ─────────────────────────────────────────────────────────────
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # À activer (ex. 31536000) une fois le domaine servi uniquement en HTTPS
    SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=0, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Easevent API',
    'DESCRIPTION': 'Easevent Backend API Documentation',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}