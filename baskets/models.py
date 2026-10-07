"""
baskets/models.py — le « panier » d'un événement (cagnotte réinventée)

Chacun y ajoute ce qu'il apporte : un objet (« 2 bouteilles de jus ») ou une
somme payée dans l'application (carte ou Orange Money / MTN MoMo), versée à
l'organisateur, sans commission Easevent. Le bilan montre qui a ajouté quoi.
"""
import uuid

from django.conf import settings
from django.db import models


class Basket(models.Model):
    class Status(models.TextChoices):
        OPEN   = 'open',   'Ouvert'
        CLOSED = 'closed', 'Fermé'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='baskets')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True, default='')
    goal_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='EUR')
    allow_items = models.BooleanField(default=True)
    allow_money = models.BooleanField(default=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['event'], condition=models.Q(status='open'),
                                               name='basket_one_open_per_event')]

    def __str__(self):
        return f'Panier « {self.title} » ({self.event_id})'


class Contribution(models.Model):
    class Kind(models.TextChoices):
        ITEM  = 'item',  'Objet'
        MONEY = 'money', 'Argent'

    class Status(models.TextChoices):
        CONFIRMED        = 'confirmed',        'Ajouté'           # objet
        AWAITING_PAYMENT = 'awaiting_payment', 'Paiement en attente'
        PROCESSING       = 'processing',       'Paiement en cours'
        PAID             = 'paid',             'Payé'
        FAILED           = 'failed',           'Paiement échoué'
        CANCELLED        = 'cancelled',        'Annulé'
        REFUNDED         = 'refunded',         'Remboursé'
        REFUND_NEEDED    = 'refund_needed',    'Remboursement à faire'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    basket = models.ForeignKey(Basket, on_delete=models.CASCADE, related_name='contributions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='basket_contributions')
    kind = models.CharField(max_length=5, choices=Kind.choices)
    label = models.CharField(max_length=120, blank=True, default='')
    quantity = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='EUR')
    message = models.CharField(max_length=200, blank=True, default='')
    anonymous = models.BooleanField(default=False, verbose_name="Montant masqué aux autres invités")
    status = models.CharField(max_length=20, choices=Status.choices)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default='')
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, default='')
    mobile_money_reference = models.CharField(max_length=64, blank=True, default='', db_index=True)
    mobile_money_amount = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['basket', 'status'])]
