from django.contrib import admin

from .models import RsvpQuestion


@admin.register(RsvpQuestion)
class RsvpQuestionAdmin(admin.ModelAdmin):
    list_display = ('label', 'kind', 'required', 'event')
    list_filter = ('kind',)
    search_fields = ('label',)
