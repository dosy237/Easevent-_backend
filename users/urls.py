from django.urls import path
from . import views
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('login/',               views.login_view,             name='login'),
    path('register/',            views.register_view,          name='register'),
    path('verify/<str:token>/',  views.verify_email_view,      name='verify-email'),
    path('resend-verification/', views.resend_verification_view, name='resend-verification'),
    path('me/',                  views.me_view,                name='me'),
    path('me/update/',           views.update_profile_view,    name='update-profile'),
    path('change-password/',     views.change_password_view,   name='change-password'),
    path('delete-account/',      views.delete_account_view,    name='delete-account'),
     path('token/refresh/',       TokenRefreshView.as_view(),   name='token-refresh'),
]