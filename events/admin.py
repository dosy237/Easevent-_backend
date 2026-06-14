from django.contrib import admin
from .models import Event, EventMedia, TemplateGeneration, EventCollaborator

admin.site.register(Event)
admin.site.register(EventMedia)
admin.site.register(TemplateGeneration)
admin.site.register(EventCollaborator)
