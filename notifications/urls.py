from django.urls import path

from . import views

urlpatterns = [
    path('',                            views.list_notifications, name='notifications'),
    path('unread-count/',               views.unread_count,       name='notifications-unread'),
    path('read-all/',                   views.mark_all_read,      name='notifications-read-all'),
    path('preferences/',                views.preferences,        name='notifications-preferences'),
    path('devices/',                    views.devices,            name='notifications-devices'),
    path('<uuid:notification_id>/read/', views.mark_read,         name='notification-read'),
]
