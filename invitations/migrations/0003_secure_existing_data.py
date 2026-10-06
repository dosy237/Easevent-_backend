"""Sécurise les invitations existantes.

- Tokens stockés en clair → empreinte SHA-256 (les anciens liens
  easevent.app/invitation/<token> n'ont jamais été servis).
- Numéros de téléphone en clair → chiffrés + empreinte HMAC.
- Invitations « sms » sans numéro : c'étaient des emails.
"""
import hashlib

from django.db import migrations


def forwards(apps, schema_editor):
    from invitations.crypto import blind_index, decrypt, encrypt

    Invitation = apps.get_model('invitations', 'Invitation')
    for inv in Invitation.objects.all():
        fields = []
        if len(inv.token) != 64:
            inv.token = hashlib.sha256(inv.token.encode()).hexdigest()
            fields.append('token')
        if inv.phone_number and not decrypt(inv.phone_number):
            plain = inv.phone_number.replace(' ', '')
            inv.phone_number, inv.phone_hash = encrypt(plain), blind_index(plain)
            fields += ['phone_number', 'phone_hash']
        if inv.channel == 'sms' and not inv.phone_number:
            inv.channel = 'email'
            fields.append('channel')
        if fields:
            inv.save(update_fields=fields)


class Migration(migrations.Migration):
    dependencies = [('invitations', '0002_invitation_email_delivery')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
