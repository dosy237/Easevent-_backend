"""
easevent/settings.py
═══════════════════════════════════════════════════════════════
Configuration Django - Easevent
Production-ready pour Render.com
═══════════════════════════════════════════════════════════════
"""

import os
from pathlib import Path
from decouple import config
from datetime import timedelta
import dj_database_url
import cloudinary

BASE_DIR = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────
# SÉCURITÉ
# ─────────────────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)

# ALLOWED_HOSTS - Version robuste et définitive
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    'easevent-backend.onrender.com',
    '.onrender.com',
]

# Ajoute les hôtes supplémentaires depuis les variables d'environnement
extra_hosts = config('ALLOWED_HOSTS', default='').split(',')
for host in extra_hosts:
    if host.strip() and host.strip() not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(host.strip())

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

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOWED_ORIGINS = []
else:
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOWED_ORIGINS = ["https://easevent-backend.onrender.com"]

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
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'whitenoise.runserver_nostatic',
    'users',
    'events',
    'invitations',
    'analytics',
    'subscriptions',
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
if 'DATABASE_URL' in os.environ:
    DATABASES = {
        'default': dj_database_url.config(conn_max_age=600, ssl_require=True)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME', default='easevent_db'),
            'USER': config('DB_USER', default='easevent_user'),
            'PASSWORD': config('DB_PASSWORD'),
            'HOST': config('DB_HOST', default='localhost'),
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
}

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

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Europe/Paris'

# ─────────────────────────────────────────────────────────────
# FICHIERS STATIQUES & MÉDIAS
# ─────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

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
EMAIL_BACKEND = 'sendgrid_backend.SendgridBackend'
SENDGRID_API_KEY = config('SENDGRID_API_KEY')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='dosyca35@gmail.com')
SENDGRID_SANDBOX_MODE_IN_DEBUG = False

cloudinary.config(
    cloud_name=config('CLOUDINARY_CLOUD_NAME'),
    api_key=config('CLOUDINARY_API_KEY'),
    api_secret=config('CLOUDINARY_API_SECRET'),
    secure=True,
)