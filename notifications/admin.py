from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('type', 'user', 'title', 'created_at', 'read_at')
    list_filter = ('type', 'category')
    raw_id_fields = ('user', 'actor', 'event', 'invitation', 'ticket')
