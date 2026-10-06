from django.urls import path
from . import views
from invitations import organizer_views as guests

urlpatterns = [
    # ── Événements publics (visiteurs) ────────────────────────
    path('publics/',                        views.liste_evenements_publics,        name='events-publics'),
    path('publics/<uuid:event_id>/',        views.detail_evenement_public,         name='event-detail'),

    # ── Mes événements (organisateur) ─────────────────────────
    path('mes-evenements/',                 views.mes_evenements,                  name='mes-evenements'),
    path('create/',                         views.creer_evenement,                 name='creer-evenement'),
    path('upload-image/',                   views.upload_image,                    name='upload-image'),

    # ── Gestion d'un événement spécifique ─────────────────────
    path('<uuid:event_id>/detail/',         views.detail_evenement_organisateur,   name='event-detail-organisateur'),
    path('<uuid:event_id>/update/',         views.modifier_evenement,              name='event-update'),
    path('<uuid:event_id>/publish/',        views.publier_evenement,               name='event-publish'),
    path('<uuid:event_id>/delete/',         views.supprimer_evenement,             name='event-delete'),

    # ── Invités (M12, M13) ────────────────────────────────────
    path('<uuid:event_id>/participants/',   guests.participants,                   name='event-participants'),
    path('<uuid:event_id>/invite/',         guests.invite,                         name='event-invite'),
    path('<uuid:event_id>/remind-pending/', guests.remind_pending,                 name='event-remind-pending'),
    path('<uuid:event_id>/participants/export-link/', guests.export_link,          name='event-guests-export-link'),
    path('participants/export/<str:token>/', guests.export_csv,                    name='event-guests-export'),
]