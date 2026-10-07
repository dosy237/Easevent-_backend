from django.urls import path

from . import views

urlpatterns = [
    path('<uuid:basket_id>/close/',                    views.close_basket, name='basket-close'),
    path('<uuid:basket_id>/items/',                    views.add_item,     name='basket-items'),
    path('<uuid:basket_id>/money/',                    views.add_money,    name='basket-money'),
    path('contributions/<uuid:cid>/',                  views.contribution, name='basket-contribution'),
    path('contributions/<uuid:cid>/checkout/',         views.checkout,     name='basket-checkout'),
    path('contributions/<uuid:cid>/mobile-money/',     views.mobile_money, name='basket-mobile-money'),
]
