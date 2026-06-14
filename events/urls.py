from django.urls import path
from . import views

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
    path('<uuid:event_id>/participants/',   views.participants_evenement,           name='event-participants'),
    path('<uuid:event_id>/invite/',         views.inviter_participant,             name='event-invite'),
]