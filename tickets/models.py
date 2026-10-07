"""
tickets/models.py
═══════════════════════════════════════════════════════════════
Ticket : chaque participation donne un ticket (MVP §5).

  (accepter une invitation)       ─┐
  (clic Participer / Payer)        ├─► pending
  (rattachement d'un lien token)  ─┘
  pending ── gratuit : validate ─────────────► generated  (not_required)
  pending ── payant : paiement Stripe réussi ─► generated  (paid)
  pending ── paiement échoué / abandonné ─────► pending    (failed / pending)
  pending ── annuler ─────────────────────────► cancelled
  pending / generated ── fin + 7 j ───────────► expired

Un seul ticket actif (pending ou generated) par couple (event, user).
Une place n'est comptée qu'au passage à « generated ».
═══════════════════════════════════════════════════════════════
"""
import secrets
import uuid

from django.core import signing
from django.db import models
from django.db.models import Q

ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # sans 0/O/1/I : lisible à l'entrée


def new_ticket_number():
    return 'EV-' + ''.join(secrets.choice(ALPHABET) for _ in range(8))


class Ticket(models.Model):

    class Status(models.TextChoices):
        PENDING   = 'pending',   'En attente'
        GENERATED = 'generated', 'Généré'
        CANCELLED = 'cancelled', 'Annulé'
        EXPIRED   = 'expired',   'Expiré'

    class PaymentStatus(models.TextChoices):
        NOT_REQUIRED = 'not_required', 'Gratuit'
        PENDING      = 'pending',      'En attente de paiement'
        PROCESSING   = 'processing',   'Paiement en cours de confirmation'
        PAID         = 'paid',         'Payé'
        FAILED       = 'failed',       'Paiement échoué'
        REFUNDED     = 'refunded',     'Remboursé'

    ACTIVE = (Status.PENDING, Status.GENERATED)

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number     = models.CharField(max_length=11, unique=True, default=new_ticket_number, editable=False)
    event      = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='tickets')
    user       = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='tickets')
    invitation = models.ForeignKey('invitations.Invitation', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='tickets')

    status         = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # Copie du prix au moment de la création : ne change pas si l'organisateur modifie le prix
    price          = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    currency       = models.CharField(max_length=3, default='EUR')
    payment_status = models.CharField(max_length=12, choices=PaymentStatus.choices,
                                      default=PaymentStatus.NOT_REQUIRED)

    # Stripe : seuls des identifiants, jamais de données de carte
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default='')
    stripe_payment_intent_id   = models.CharField(max_length=255, blank=True, default='')
    # Mobile Money (Notch Pay) : référence du paiement et montant réellement débité en FCFA
    mobile_money_reference     = models.CharField(max_length=64, blank=True, default='', db_index=True)
    # Billet offert : la personne qui l'a payé (le titulaire reste « user »)
    purchased_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='tickets_offered')
    mobile_money_amount        = models.PositiveIntegerField(null=True, blank=True)

    dress_code   = models.CharField(max_length=80, blank=True, default='')
    generated_at = models.DateTimeField(null=True, blank=True)
    # Contrôle à l'entrée (scanner de l'organisateur)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tickets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['event', 'status']),
            models.Index(fields=['stripe_checkout_session_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'user'],
                condition=Q(status__in=['pending', 'generated']),
                name='one_active_ticket_per_event_user',
            ),
        ]

    def __str__(self):
        return f"{self.number} — {self.event_id} ({self.status})"

    @property
    def is_free(self):
        return self.price <= 0

    @property
    def qr_payload(self):
        """Identifiant signé (HMAC, SECRET_KEY) présenté à l'entrée : infalsifiable, sans donnée personnelle."""
        return signing.dumps({'t': str(self.id)}, salt='easevent.ticket', compress=True)

    @staticmethod
    def read_qr_payload(payload):
        """Retourne l'id du ticket si la signature est valide, sinon None."""
        try:
            return signing.loads(payload, salt='easevent.ticket')['t']
        except (signing.BadSignature, KeyError, TypeError):
            return None


class TicketGift(models.Model):
    """
    Un billet offert à un proche (« Payer pour un proche »).
    L'acheteur paie ; le billet est ensuite créé AU NOM du proche :
      - membre Easevent : aussitôt, dans ses Invitations et sa messagerie ;
      - pas encore inscrit : il reçoit un email ou un SMS, et le billet l'attend ;
        il est rattaché à son compte dès que cet email ou ce numéro est vérifié.
    """
    class Status(models.TextChoices):
        AWAITING_PAYMENT = 'awaiting_payment', 'En attente de paiement'
        PAID = 'paid', 'Payé, en attente du proche'
        DELIVERED = 'delivered', 'Remis au proche'
        CANCELLED = 'cancelled', 'Annulé'
        REFUND_NEEDED = 'refund_needed', 'À rembourser'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='gifts')
    buyer = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='gifts_sent')
    recipient = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='gifts_received')
    recipient_name = models.CharField(max_length=80, blank=True, default='')
    recipient_email = models.EmailField(blank=True, default='')
    # Numéro chiffré + empreinte pour le retrouver à la vérification du téléphone (comme les invitations)
    recipient_phone = models.CharField(max_length=255, blank=True, default='')
    recipient_phone_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    message = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING_PAYMENT)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='EUR')
    payment_status = models.CharField(max_length=12, choices=Ticket.PaymentStatus.choices, default=Ticket.PaymentStatus.PENDING)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default='')
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, default='')
    mobile_money_reference = models.CharField(max_length=64, blank=True, default='', db_index=True)
    mobile_money_amount = models.PositiveIntegerField(null=True, blank=True)
    ticket = models.OneToOneField(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name='gift')
    paid_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['recipient_email', 'status']), models.Index(fields=['buyer', 'status'])]

    @property
    def is_free(self):
        return self.price <= 0
