"""
adminpanel/models.py — annonces de l'équipe et journal des actions d'administration
════════════════════════════════════════════════════════════════
Announcement : message de l'équipe Easevent (texte, vidéo de 45 s au plus, lien),
  affiché EN TÊTE du fil de tous les utilisateurs pendant sa période de diffusion,
  du plus prioritaire au moins prioritaire.
AdminAction  : chaque modification faite depuis l'administration est tracée
  (qui, quoi, sur quoi, quand) — indispensable pour un outil aussi puissant.
════════════════════════════════════════════════════════════════
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AnnouncementQuerySet(models.QuerySet):
    def live(self, now=None):
        now = now or timezone.now()
        return self.filter(is_active=True, starts_at__lte=now).filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now)).order_by('-priority', '-starts_at')


class Announcement(models.Model):
    class Priority(models.IntegerChoices):
        NORMAL = 10, 'Normale'
        HIGH = 50, 'Haute'
        URGENT = 90, 'Urgente'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=90)
    body = models.CharField(max_length=400, blank=True)
    link_url = models.URLField(max_length=300, blank=True)
    link_label = models.CharField(max_length=30, blank=True)
    # Même format que la vidéo d'un événement (events/video.py)
    video_public_id = models.CharField(max_length=200, blank=True)
    video = models.JSONField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(choices=Priority.choices, default=Priority.NORMAL)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ['-priority', '-starts_at']
        indexes = [models.Index(fields=['is_active', 'starts_at'])]

    def __str__(self):
        return self.title


class AdminAction(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    action = models.CharField(max_length=40)
    target_type = models.CharField(max_length=20)
    target_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ServiceKey(models.Model):
    """
    Clé d'un service externe (IA, paiement, cartes…), saisie dans l'administration Django et
    stockée CHIFFRÉE (Fernet : AES-128 + HMAC). Elle remplace la variable d'environnement de
    même nom dès son enregistrement, sans redémarrer le serveur (voir adminpanel/keys.py).
    """
    class Name(models.TextChoices):
        GEMINI_API_KEY = 'GEMINI_API_KEY', 'Google Gemini (IA)'
        GROQ_API_KEY = 'GROQ_API_KEY', 'Groq (IA)'
        OPENROUTER_API_KEY = 'OPENROUTER_API_KEY', 'OpenRouter (IA)'
        MISTRAL_API_KEY = 'MISTRAL_API_KEY', 'Mistral (IA, direction artistique)'
        STRIPE_SECRET_KEY = 'STRIPE_SECRET_KEY', 'Stripe — clé secrète'
        STRIPE_WEBHOOK_SECRET = 'STRIPE_WEBHOOK_SECRET', 'Stripe — secret du webhook (whsec_…)'
        NOTCHPAY_PUBLIC_KEY = 'NOTCHPAY_PUBLIC_KEY', 'Notch Pay — clé publique (Mobile Money)'
        NOTCHPAY_HASH_KEY = 'NOTCHPAY_HASH_KEY', 'Notch Pay — clé de hachage des webhooks'
        GOOGLE_MAPS_API_KEY = 'GOOGLE_MAPS_API_KEY', 'Google Maps (recherche d’adresses, cartes)'
        TWILIO_ACCOUNT_SID = 'TWILIO_ACCOUNT_SID', 'Twilio — Account SID (SMS)'
        TWILIO_AUTH_TOKEN = 'TWILIO_AUTH_TOKEN', 'Twilio — Auth Token (SMS)'
        TWILIO_MESSAGING_SERVICE_SID = 'TWILIO_MESSAGING_SERVICE_SID', 'Twilio — Messaging Service SID'
        TWILIO_FROM_NUMBER = 'TWILIO_FROM_NUMBER', 'Twilio — numéro d’envoi'
        SENDGRID_API_KEY = 'SENDGRID_API_KEY', 'SendGrid (emails)'
        CLOUDINARY_CLOUD_NAME = 'CLOUDINARY_CLOUD_NAME', 'Cloudinary — nom du cloud (photos, vidéos)'
        CLOUDINARY_API_KEY = 'CLOUDINARY_API_KEY', 'Cloudinary — API Key'
        CLOUDINARY_API_SECRET = 'CLOUDINARY_API_SECRET', 'Cloudinary — API Secret'
        EXPO_ACCESS_TOKEN = 'EXPO_ACCESS_TOKEN', 'Expo — jeton des notifications push'

    name = models.CharField(max_length=40, choices=Name.choices, unique=True, verbose_name='Service')
    encrypted_value = models.TextField(verbose_name='Valeur chiffrée')
    last4 = models.CharField(max_length=4, blank=True, verbose_name='4 derniers caractères')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Clé de service'
        verbose_name_plural = 'Clés de service (chiffrées)'
        ordering = ['name']

    def __str__(self):
        return self.get_name_display()


def _forget_key(sender, instance, **kwargs):
    from .keys import forget
    forget(instance.name)


models.signals.post_save.connect(_forget_key, sender=ServiceKey)
models.signals.post_delete.connect(_forget_key, sender=ServiceKey)
