from django.urls import path

from . import views

urlpatterns = [
    path('stats/', views.stats, name='admin-stats'),
    path('users/', views.users, name='admin-users'),
    path('users/<uuid:user_id>/', views.user_detail, name='admin-user'),
    path('events/', views.events, name='admin-events'),
    path('events/<uuid:event_id>/', views.event_detail, name='admin-event'),
    path('announcements/', views.announcements, name='admin-announcements'),
    path('announcements/<uuid:announcement_id>/', views.announcement_detail, name='admin-announcement'),
]
