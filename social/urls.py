from django.urls import path

from . import views

urlpatterns = [
    path('',                                   views.friends,      name='friends'),
    path('requests/',                          views.send_request, name='friend-request'),
    path('requests/<uuid:friendship_id>/accept/', views.accept,    name='friend-accept'),
    path('requests/<uuid:friendship_id>/',     views.remove,       name='friend-remove'),
]
