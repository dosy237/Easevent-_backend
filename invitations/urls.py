from django.urls import path
from events import views as event_views
from . import organizer_views, public_views, views

urlpatterns = [
    path('mine/',                          views.mes_invitations,             name='mes-invitations'),
    path('claim/',                         public_views.claim_invitation,     name='invitation-claim'),
    path('by-token/<str:token>/',          public_views.invitation_by_token,  name='invitation-by-token'),
    path('by-token/<str:token>/decline/',  public_views.decline_by_token,     name='invitation-decline-token'),
    path('<uuid:invitation_id>/repondre/', views.repondre_invitation,         name='repondre-invitation'),
    path('<uuid:invitation_id>/remind/',   organizer_views.remind_one,        name='invitation-remind'),
    path('<uuid:invitation_id>/revoke/',   event_views.revoquer_invitation,   name='revoquer-invitation'),
]
