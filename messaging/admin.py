from django.contrib import admin

from .models import Conversation


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    # Les contenus des messages ne sont pas affichés dans l'administration (vie privée)
    list_display = ('event', 'organizer', 'participant', 'last_message_at')
    raw_id_fields = ('event', 'organizer', 'participant')
