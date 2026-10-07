from django.urls import path

from . import checkin, views

urlpatterns = [
    path('tickets/mine/',                     views.my_tickets,        name='tickets-mine'),
    path('tickets/counts/',                   views.ticket_counts,     name='tickets-counts'),
    path('tickets/<uuid:ticket_id>/',         views.ticket_detail,     name='ticket-detail'),
    path('tickets/<uuid:ticket_id>/validate/', views.validate_ticket,  name='ticket-validate'),
    path('tickets/<uuid:ticket_id>/checkout/', views.checkout_ticket,  name='ticket-checkout'),
    path('tickets/<uuid:ticket_id>/cancel/',  views.cancel_ticket,     name='ticket-cancel'),
    path('tickets/<uuid:ticket_id>/pdf-link/', views.ticket_pdf_link,  name='ticket-pdf-link'),
    path('tickets/pdf/<str:token>/',          views.ticket_pdf,        name='ticket-pdf'),
    path('events/<uuid:event_id>/tickets/',   views.take_ticket,       name='event-take-ticket'),
    path('events/<uuid:event_id>/check-in/',  checkin.check_in,        name='event-check-in'),
    path('payments/connect/status/',          views.connect_status,    name='connect-status'),
    path('payments/connect/onboard/',         views.connect_onboard,   name='connect-onboard'),
    path('payments/connect/dashboard/',       views.connect_dashboard, name='connect-dashboard'),
    path('payments/return/',                  views.payment_return,    name='payment-return'),
    path('stripe/webhook/',                   views.stripe_webhook,    name='stripe-webhook'),
]
