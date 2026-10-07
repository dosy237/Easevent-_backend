from django.contrib import admin

from .models import AdminAction, Announcement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'priority', 'is_active', 'starts_at', 'ends_at')


@admin.register(AdminAction)
class AdminActionAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'target_type', 'target_id')
    readonly_fields = [f.name for f in AdminAction._meta.fields]
