"""
Recopie les anciennes questions RSVP (app invitations) dans l'app rsvp.
Les réponses ne sont reprises que si l'invitation est liée à un compte.
"""
import json

from django.db import migrations

KINDS = {'text': 'text', 'radio': 'single', 'checkbox': 'multiple', 'yes_no': 'yesno'}


def _value(kind, raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    if kind == 'yesno':
        return raw.lower() in ('oui', 'yes', 'true', '1')
    if kind == 'multiple':
        try:
            data = json.loads(raw)
            return [str(v) for v in data] if isinstance(data, list) else [raw]
        except ValueError:
            return [raw]
    return raw


def copy(apps, schema_editor):
    Old = apps.get_model('invitations', 'RSVPQuestion')
    OldResponse = apps.get_model('invitations', 'RSVPResponse')
    Question = apps.get_model('rsvp', 'RsvpQuestion')
    Answer = apps.get_model('rsvp', 'RsvpAnswer')
    for old in Old.objects.all().order_by('event_id', 'order'):
        kind = KINDS.get(old.question_type, 'text')
        q = Question.objects.create(
            id=old.id, event_id=old.event_id, position=old.order, kind=kind,
            label=(old.question_text or '')[:200] or 'Question', required=old.is_required,
            options=[str(o)[:80] for o in (old.options or [])][:8] if kind in ('single', 'multiple') else [])
        for resp in OldResponse.objects.filter(question=old).select_related('invitation'):
            user_id = resp.invitation.invited_user_id
            value = _value(kind, resp.answer)
            if user_id and value is not None:
                Answer.objects.get_or_create(question=q, user_id=user_id, defaults={'value': value})


class Migration(migrations.Migration):
    dependencies = [
        ('rsvp', '0001_initial'),
        ('invitations', '0003_secure_existing_data'),
    ]
    operations = [migrations.RunPython(copy, migrations.RunPython.noop)]
