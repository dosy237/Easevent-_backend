# invitations/views.py
# ════════════════════════════════════════════════════════════════
# Views pour les invitations.
# ════════════════════════════════════════════════════════════════

from rest_framework.decorators  import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response    import Response
from rest_framework             import status
from django.utils               import timezone

from .models  import Invitation
from .serializers import InvitationSerializer


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def mes_invitations(request):
    """
    GET /api/invitations/mine/
    Retourne les invitations de l'utilisateur connecté.
    Uniquement les invitations non expirées et non révoquées.
    """
    invitations = Invitation.objects.filter(
        invited_user = request.user,
        expires_at__gt = timezone.now(),   # non expirées
    ).exclude(
        status__in = ['revoked', 'expired']
    ).select_related('event').order_by('-sent_at')

    serializer = InvitationSerializer(invitations, many=True, context={'request': request})
    return Response({
        'count':       invitations.count(),
        'invitations': serializer.data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def repondre_invitation(request, invitation_id):
    """
    POST /api/invitations/<id>/repondre/
    Body : { "status": "confirmed" } ou { "status": "declined" }
    Permet à l'utilisateur de confirmer ou décliner une invitation.
    """
    try:
        invitation = Invitation.objects.get(
            id           = invitation_id,
            invited_user = request.user,
        )
    except Invitation.DoesNotExist:
        return Response(
            {'detail': 'Invitation introuvable.'},
            status=status.HTTP_404_NOT_FOUND
        )

    new_status = request.data.get('status')
    if new_status not in ['confirmed', 'declined']:
        return Response(
            {'detail': 'Statut invalide. Valeurs acceptées : confirmed, declined'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if invitation.status in ('revoked', 'expired') or not invitation.is_valid:
        return Response({'detail': "Cette invitation n'est plus valable."}, status=status.HTTP_400_BAD_REQUEST)

    from tickets.models import Ticket
    from tickets.services import TicketError, cancel_ticket, create_pending_ticket

    ticket = None
    if new_status == 'confirmed':
        # Accepter crée le ticket « en attente » à valider dans Mes tickets (MVP §5)
        try:
            ticket, _ = create_pending_ticket(invitation.event, request.user, invitation=invitation)
        except TicketError as exc:
            return Response({'detail': exc.message, 'code': exc.code}, status=exc.status)
    else:
        for pending in Ticket.objects.filter(invitation=invitation, status=Ticket.Status.PENDING):
            try:
                cancel_ticket(pending)
            except TicketError:
                pass

    invitation.status       = new_status
    invitation.responded_at = timezone.now()
    invitation.save()

    if ticket is not None and ticket.status == Ticket.Status.PENDING:
        from notifications.models import Notification
        from notifications.services import notify
        notify(request.user, Notification.Type.TICKET_TO_VALIDATE, 'Invitation acceptée.',
               f'Validez votre ticket pour {invitation.event.title} dans Mes tickets.',
               event=invitation.event, invitation=invitation, ticket=ticket,
               dedupe_key=f'to-validate:{ticket.id}')

    return Response({
        'detail':    f'Invitation {new_status}.',
        'status':    new_status,
        'ticket_id': str(ticket.id) if ticket else None,
    })