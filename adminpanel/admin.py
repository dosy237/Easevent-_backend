from django import forms
from django.contrib import admin, messages

from . import key_checks, keys
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
        # Guillemets, espaces ou « NOM= » collés par erreur : retirés avant la vérification
        value = key_checks.clean(data.get('value'))
        data['value'] = value
        if not self.instance.pk and not value:
            raise forms.ValidationError('Saisissez la valeur de la clé.')
        if value:
            if len(value) > 500:
                raise forms.ValidationError('Valeur trop longue.')
            error = key_checks.check_format(data.get('name') or self.instance.name, value)
            if error:
                self.add_error('value', error)
        return data


@admin.register(ServiceKey)
class ServiceKeyAdmin(admin.ModelAdmin):
    form = ServiceKeyForm
    list_display = ('__str__', 'masked', 'updated_at', 'updated_by')
    readonly_fields = ('masked', 'updated_at', 'updated_by')
    actions = ('test_keys',)

    @admin.action(description='Tester les clés sélectionnées (lecture seule, aucun envoi)')
    def test_keys(self, request, queryset):
        for obj in queryset:
            ok, msg = key_checks.live_check(obj.name, keys.get_key)
            level = messages.SUCCESS if ok else (messages.INFO if ok is None else messages.ERROR)
            self.message_user(request, f'{obj.get_name_display()} : {msg}', level)

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
        if value:
            for warning in key_checks.warnings(obj.name, value):
                self.message_user(request, warning, messages.WARNING)
            # Test immédiat : on sait tout de suite si le service accepte la clé
            ok, msg = key_checks.live_check(obj.name, lambda n: value if n == obj.name else keys.get_key(n))
            if ok is not None:
                self.message_user(request, f'Test en direct — {obj.get_name_display()} : {msg}',
                                  messages.SUCCESS if ok else messages.ERROR)
        AdminAction.objects.create(actor=request.user, action='servicekey.save', target_type='servicekey',
                                   target_id=obj.name, detail={'changed': bool(value)})
