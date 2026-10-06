from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path('login/',                   views.login_view,                   name='login'),
    path('register/',                views.register_view,                name='register'),
    path('logout/',                  views.logout_view,                  name='logout'),
    path('verify/<str:token>/',      views.verify_email_view,            name='verify-email'),
    path('verify-email/',            views.verify_email_api_view,        name='verify-email-api'),
    path('resend-verification/',     views.resend_verification_view,     name='resend-verification'),
    path('password-reset/',          views.password_reset_request_view,  name='password-reset'),
    path('password-reset/confirm/',  views.password_reset_confirm_view,  name='password-reset-confirm'),
    path('me/',                      views.me_view,                      name='me'),
    path('me/update/',               views.update_profile_view,          name='update-profile'),
    path('me/stats/',                views.me_stats_view,                name='me-stats'),
    path('me/export/',               views.export_data_view,             name='me-export'),
    path('change-password/',         views.change_password_view,         name='change-password'),
    path('delete-account/',          views.delete_account_view,          name='delete-account'),
    path('token/refresh/',           TokenRefreshView.as_view(),         name='token-refresh'),
]
