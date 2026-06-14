from django.contrib import admin
from .models import User, UserPreferences, Domain

admin.site.register(User)
admin.site.register(UserPreferences)
admin.site.register(Domain)
