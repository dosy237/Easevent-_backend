from django.contrib import admin
from .models import Invitation, RSVPQuestion, RSVPResponse

admin.site.register(Invitation)
admin.site.register(RSVPQuestion)
admin.site.register(RSVPResponse)
