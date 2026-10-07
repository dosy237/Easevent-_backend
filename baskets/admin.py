from django.contrib import admin

from .models import Basket, Contribution


@admin.register(Basket)
class BasketAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'status', 'currency', 'created_at')
    list_filter = ('status',)
    search_fields = ('title', 'event__title')


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = ('basket', 'user', 'kind', 'label', 'amount', 'currency', 'status', 'created_at')
    list_filter = ('kind', 'status')
    search_fields = ('mobile_money_reference', 'user__email', 'basket__event__title')
    readonly_fields = ('stripe_checkout_session_id', 'stripe_payment_intent_id', 'mobile_money_reference', 'mobile_money_amount')
