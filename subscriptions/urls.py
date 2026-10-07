from django.urls import path

from . import views

urlpatterns = [
    path('',          views.overview, name='subscriptions'),
    path('checkout/', views.checkout, name='subscriptions-checkout'),
    path('cancel/',   views.cancel,   name='subscriptions-cancel'),
    path('resume/',   views.resume,   name='subscriptions-resume'),
    path('portal/',   views.portal,   name='subscriptions-portal'),
]
