from django.contrib import admin

from django import forms

from . import keys
from .models import AdminAction, Announcement, ServiceKey


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'priority', 'is_active', 'starts_at', 'ends_at')


@admin.register(AdminAction)
class AdminActionAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'target_type', 'target_id')
    readonly_fields = [f.name for f in AdminAction._meta.fields]


class ServiceKeyForm(forms.ModelForm):
    value = forms.CharField(
        label='Nouvelle valeur', required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password', 'size': 60}),
        help_text='Collez la clé : elle est chiffrée avant d’être enregistrée et ne sera plus jamais affichée. '
                  'Laissez vide pour garder la valeur actuelle.')

    class Meta:
        model = ServiceKey
        fields = ('name',)

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get('value'):
            raise forms.ValidationError('Saisissez la valeur de la clé.')
        if data.get('value') and (len(data['value']) > 500 or any(c.isspace() for c in data['value'])):
            raise forms.ValidationError('Valeur invalide (une clé ne contient pas d’espace).')
        return data


@admin.register(ServiceKey)
class ServiceKeyAdmin(admin.ModelAdmin):
    form = ServiceKeyForm
    list_display = ('__str__', 'masked', 'updated_at', 'updated_by')
    readonly_fields = ('masked', 'updated_at', 'updated_by')

    @admin.display(description='Valeur')
    def masked(self, obj):
        return f'•••• {obj.last4}' if obj.last4 else '••••'

    def has_module_permission(self, request):
        return request.user.is_superuser

    has_view_permission = has_change_permission = has_add_permission = has_delete_permission = \
        lambda self, request, obj=None: request.user.is_superuser

    def save_model(self, request, obj, form, change):
        value = form.cleaned_data.get('value')
        if value:
            obj.encrypted_value = keys.encrypt(value)
            obj.last4 = value[-4:]
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        AdminAction.objects.create(actor=request.user, action='servicekey.save', target_type='servicekey',
                                   target_id=obj.name, detail={'changed': bool(value)})
