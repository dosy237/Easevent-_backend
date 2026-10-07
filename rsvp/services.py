"""
rsvp/services.py — règles des questions RSVP (M14) et des réponses (M19)
"""
from django.db import transaction

from .models import RsvpAnswer, RsvpQuestion

MAX_QUESTIONS = 5
MAX_OPTIONS = 8
LABEL_MAX = 200
OPTION_MAX = 80
TEXT_MAX = 500
KINDS = [k for k, _ in RsvpQuestion.Kind.choices]


class RsvpError(Exception):
    def __init__(self, message, code='invalid', status=400, **extra):
        super().__init__(message)
        self.message, self.code, self.status, self.extra = message, code, status, extra

    def payload(self):
        return {'detail': self.message, 'code': self.code, **self.extra}


# ─────────────────────────────────────────────────────────────
# Suggestions selon le type d'événement (M14)
# ─────────────────────────────────────────────────────────────
_DIET = {'kind': 'single', 'label': 'Avez-vous un régime alimentaire particulier ?',
         'options': ['Aucun', 'Végétarien', 'Végan', 'Sans gluten', 'Halal', 'Autre']}
_ALLERGY = {'kind': 'text', 'label': 'Allergies ou contraintes à signaler ?', 'options': []}
_PLUS_ONE = {'kind': 'yesno', 'label': 'Venez-vous accompagné(e) ?', 'options': []}
_CHILDREN = {'kind': 'yesno', 'label': 'Venez-vous avec des enfants ?', 'options': []}
_SONG = {'kind': 'text', 'label': 'Une chanson qui vous fera danser ?', 'options': []}
_COMPANY = {'kind': 'text', 'label': 'Votre entreprise ou organisation ?', 'options': []}
_ROLE = {'kind': 'text', 'label': 'Votre fonction ?', 'options': []}
_TRANSPORT = {'kind': 'single', 'label': 'Comment venez-vous ?',
              'options': ['Voiture', 'Transports en commun', 'Taxi / VTC', 'À pied', 'Je ne sais pas encore']}
_SESSIONS = {'kind': 'multiple', 'label': 'Quels moments vous intéressent ?',
             'options': ['Conférences', 'Ateliers', 'Networking', 'Cocktail']}
_LEVEL = {'kind': 'single', 'label': 'Votre niveau ?', 'options': ['Débutant', 'Intermédiaire', 'Avancé']}
_EXPECT = {'kind': 'text', 'label': "Qu'attendez-vous de cet événement ?", 'options': []}
_ACCESS = {'kind': 'text', 'label': "Un besoin d'accessibilité (mobilité, audition…) ?", 'options': []}

SUGGESTIONS = {
    'mariage':      [_DIET, _PLUS_ONE, _CHILDREN, _ALLERGY, _SONG],
    'anniversaire': [_PLUS_ONE, _DIET, _ALLERGY, _SONG],
    'soiree':       [_PLUS_ONE, _DIET, _SONG, _TRANSPORT],
    'gala':         [_DIET, _PLUS_ONE, _ALLERGY, _COMPANY],
    'conference':   [_COMPANY, _ROLE, _SESSIONS, _DIET, _ACCESS],
    'seminaire':    [_COMPANY, _ROLE, _DIET, _EXPECT, _ACCESS],
    'atelier':      [_LEVEL, _EXPECT, _ALLERGY, _ACCESS],
    'concert':      [_PLUS_ONE, _TRANSPORT, _ACCESS],
    'festival':     [_PLUS_ONE, _TRANSPORT, _ACCESS],
    'exposition':   [_PLUS_ONE, _ACCESS, _EXPECT],
}
DEFAULT_SUGGESTIONS = [_PLUS_ONE, _DIET, _ALLERGY, _ACCESS]


def suggestions(event):
    existing = {q.label.strip().lower() for q in event.rsvp_questions.all()}
    items = SUGGESTIONS.get(event.event_type, DEFAULT_SUGGESTIONS)
    return [dict(s, required=False) for s in items if s['label'].lower() not in existing]


# ─────────────────────────────────────────────────────────────
# Questions (organisateur)
# ─────────────────────────────────────────────────────────────
def _clean_text(value, limit, field):
    text = ' '.join(str(value or '').split())
    if not text:
        raise RsvpError(f'{field} est obligatoire.', 'required_field')
    if len(text) > limit:
        raise RsvpError(f'{field} : {limit} caractères maximum.', 'too_long')
    return text


def clean_question(data, current=None):
    """Valide une question (création, ou modification partielle si current)."""
    kind = data.get('kind', current.kind if current else None)
    if kind not in KINDS:
        raise RsvpError('Type de question invalide.', 'invalid_kind')
    out = {'kind': kind}
    if 'label' in data or current is None:
        out['label'] = _clean_text(data.get('label'), LABEL_MAX, 'La question')
    if 'required' in data or current is None:
        out['required'] = bool(data.get('required', False))

    if kind in ('single', 'multiple'):
        raw = data.get('options', current.options if current else [])
        if not isinstance(raw, list):
            raise RsvpError('Les choix doivent être une liste.', 'invalid_options')
        options, seen = [], set()
        for item in raw:
            text = ' '.join(str(item or '').split())
            if not text or text.lower() in seen:
                continue
            if len(text) > OPTION_MAX:
                raise RsvpError(f'Chaque choix : {OPTION_MAX} caractères maximum.', 'too_long')
            seen.add(text.lower())
            options.append(text)
        if len(options) < 2:
            raise RsvpError('Proposez au moins 2 choix.', 'invalid_options')
        if len(options) > MAX_OPTIONS:
            raise RsvpError(f'{MAX_OPTIONS} choix maximum.', 'invalid_options')
        out['options'] = options
    else:
        out['options'] = []
    return out


def create_question(event, data):
    with transaction.atomic():
        # Verrou sur l'événement : pas de 6e question par deux requêtes simultanées
        type(event).objects.select_for_update().get(pk=event.pk)
        count = event.rsvp_questions.count()
        if count >= MAX_QUESTIONS:
            raise RsvpError(f'{MAX_QUESTIONS} questions maximum par événement.', 'max_questions')
        return RsvpQuestion.objects.create(event=event, position=count, **clean_question(data))


def update_question(question, data):
    values = clean_question(data, current=question)
    kind_changed = values['kind'] != question.kind
    for field, value in values.items():
        setattr(question, field, value)
    with transaction.atomic():
        question.save()
        if kind_changed:
            # Les anciennes réponses ne correspondent plus au nouveau type
            question.answers.all().delete()
        elif question.kind in ('single', 'multiple'):
            _drop_removed_options(question)
    return question


def _drop_removed_options(question):
    allowed = set(question.options)
    for answer in question.answers.all():
        if question.kind == 'single' and answer.value not in allowed:
            answer.delete()
        elif question.kind == 'multiple':
            kept = [v for v in answer.value if v in allowed] if isinstance(answer.value, list) else []
            if not kept:
                answer.delete()
            elif kept != answer.value:
                answer.value = kept
                answer.save(update_fields=['value', 'updated_at'])


def reorder(event, ids):
    questions = {str(q.id): q for q in event.rsvp_questions.all()}
    if not isinstance(ids, list) or sorted(map(str, ids)) != sorted(questions):
        raise RsvpError('Ordre invalide.', 'invalid_order')
    with transaction.atomic():
        for position, qid in enumerate(ids):
            RsvpQuestion.objects.filter(pk=questions[str(qid)].pk).update(position=position)


def delete_question(question):
    event = question.event
    question.delete()
    for position, q in enumerate(event.rsvp_questions.all()):
        if q.position != position:
            RsvpQuestion.objects.filter(pk=q.pk).update(position=position)


# ─────────────────────────────────────────────────────────────
# Réponses (invité)
# ─────────────────────────────────────────────────────────────
def can_answer(event, user):
    from tickets.services import can_access_event
    return event.deleted_at is None and event.organizer_id != user.id and can_access_event(event, user)


def _clean_value(question, value):
    """Valeur normalisée, ou None si la réponse est vide."""
    if question.kind == 'text':
        text = str(value or '').strip()
        if len(text) > TEXT_MAX:
            raise RsvpError(f'Réponse trop longue ({TEXT_MAX} caractères maximum).', 'too_long', question=str(question.id))
        return text or None
    if question.kind == 'yesno':
        if value in (None, ''):
            return None
        if isinstance(value, bool):
            return value
        raise RsvpError('Répondez par oui ou par non.', 'invalid_answer', question=str(question.id))
    if question.kind == 'single':
        if value in (None, ''):
            return None
        if value not in question.options:
            raise RsvpError('Choix invalide.', 'invalid_answer', question=str(question.id))
        return value
    # multiple
    if value in (None, ''):
        return None
    if not isinstance(value, list) or any(v not in question.options for v in value):
        raise RsvpError('Choix invalide.', 'invalid_answer', question=str(question.id))
    kept = [o for o in question.options if o in value]      # ordre des choix, sans doublon
    return kept or None


def save_answers(event, user, raw):
    """raw : {question_id: valeur}. Enregistre (ou efface) les réponses fournies."""
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise RsvpError('Réponses invalides.', 'invalid_answer')
    questions = {str(q.id): q for q in event.rsvp_questions.all()}
    cleaned = {}
    for qid, value in raw.items():
        question = questions.get(str(qid))
        if question is None:
            continue                      # question supprimée entre-temps : on ignore
        cleaned[question] = _clean_value(question, value)
    with transaction.atomic():
        for question, value in cleaned.items():
            if value is None:
                RsvpAnswer.objects.filter(question=question, user=user).delete()
            else:
                RsvpAnswer.objects.update_or_create(question=question, user=user, defaults={'value': value})


def answers_for(event, user):
    return {str(a.question_id): a.value
            for a in RsvpAnswer.objects.filter(question__event=event, user=user)}


def missing_required(event, user):
    answered = set(RsvpAnswer.objects.filter(question__event=event, user=user).values_list('question_id', flat=True))
    return [q for q in event.rsvp_questions.all() if q.required and q.id not in answered]


def check_required(event, user, raw=None):
    """
    À l'acceptation (ou à la prise de ticket) :
    - sans « rsvp_answers » et sans réponse enregistrée : renvoie les questions
      (code rsvp_questions) pour que l'application les affiche, même facultatives ;
    - sinon enregistre les réponses fournies et refuse s'il manque une réponse obligatoire.
    """
    questions = list(event.rsvp_questions.all())
    if not questions:
        return
    if raw is None and not RsvpAnswer.objects.filter(question__event=event, user=user).exists():
        raise RsvpError("L'organisateur a quelques questions pour vous.", 'rsvp_questions',
                        **_payload(event, user, questions))
    save_answers(event, user, raw)
    missing = missing_required(event, user)
    if missing:
        raise RsvpError("Répondez aux questions de l'organisateur pour confirmer votre venue.",
                        'rsvp_required', **_payload(event, user, questions))


def _payload(event, user, questions):
    answered = set(RsvpAnswer.objects.filter(question__event=event, user=user).values_list('question_id', flat=True))
    return {'event': {'id': str(event.id), 'title': event.title},
            'questions': [serialize_question(q) for q in questions],
            'answers': answers_for(event, user),
            'missing': [str(q.id) for q in questions if q.required and q.id not in answered]}


def serialize_question(q, answers_count=None):
    data = {'id': str(q.id), 'kind': q.kind, 'label': q.label, 'options': q.options,
            'required': q.required, 'position': q.position}
    if answers_count is not None:
        data['answers_count'] = answers_count
    return data


def display_value(question, value):
    if value is None:
        return ''
    if question.kind == 'yesno':
        return 'Oui' if value else 'Non'
    if question.kind == 'multiple':
        return ', '.join(value) if isinstance(value, list) else str(value)
    return str(value)


def answers_by_user(event, user_ids):
    """{user_id: [{question_id, label, value, display}]} pour la liste des invités (M13)."""
    questions = list(event.rsvp_questions.all())
    if not questions or not user_ids:
        return {}
    by_q = {q.id: q for q in questions}
    out = {}
    for a in RsvpAnswer.objects.filter(question__in=questions, user_id__in=user_ids):
        q = by_q[a.question_id]
        out.setdefault(a.user_id, []).append({'question_id': str(q.id), 'label': q.label, 'position': q.position,
                                              'value': a.value, 'display': display_value(q, a.value)})
    for items in out.values():
        items.sort(key=lambda x: x['position'])
    return out


def summary(event):
    """Synthèse par question : décompte des choix ou dernières réponses libres."""
    from django.db.models import Count
    result = []
    for q in event.rsvp_questions.annotate(n=Count('answers')).order_by('position', 'created_at'):
        answers = list(q.answers.select_related('user').order_by('-updated_at'))
        item = serialize_question(q, answers_count=q.n)
        if q.kind == 'yesno':
            item['counts'] = [{'label': 'Oui', 'count': sum(1 for a in answers if a.value is True)},
                              {'label': 'Non', 'count': sum(1 for a in answers if a.value is False)}]
        elif q.kind in ('single', 'multiple'):
            counts = {o: 0 for o in q.options}
            for a in answers:
                for v in (a.value if isinstance(a.value, list) else [a.value]):
                    if v in counts:
                        counts[v] += 1
            item['counts'] = [{'label': o, 'count': c} for o, c in counts.items()]
        else:
            item['texts'] = [{'name': a.user.full_name, 'text': a.value} for a in answers[:50]]
        result.append(item)
    return result
