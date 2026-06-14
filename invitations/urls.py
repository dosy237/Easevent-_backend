from django.urls import path
from events import views as event_views
from . import views

urlpatterns = [
    path('mine/',                       views.mes_invitations,       name='mes-invitations'),
    path('<uuid:invitation_id>/repondre/', views.repondre_invitation, name='repondre-invitation'),
    path('<uuid:invitation_id>/revoke/',  event_views.revoquer_invitation, name='revoquer-invitation'),
]