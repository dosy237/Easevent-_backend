"""
rsvp/views.py — Questions RSVP

Organisateur (M14)
  GET  /api/events/<id>/rsvp-questions/            questions + suggestions selon le type
  POST /api/events/<id>/rsvp-questions/            { kind, label, options?, required? }
  PATCH / DELETE /api/events/<id>/rsvp-questions/<qid>/
  POST /api/events/<id>/rsvp-questions/reorder/    { order: [qid, …] }
  GET  /api/events/<id>/rsvp-answers/              synthèse des réponses

Invité (M19)
  GET  /api/events/<id>/rsvp/                      questions + mes réponses
  POST /api/events/<id>/rsvp/                      { answers: { qid: valeur } }
Les réponses peuvent aussi accompagner l'acceptation (« rsvp_answers »).
"""
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from events.models import Event

from . import services
from .models import RsvpQuestion


class RsvpThrottle(UserRateThrottle):
    scope = 'rsvp'


def _own_event(request, event_id):
    from events.team import managed_event
    event = managed_event(request.user, event_id)
    if event is None:
        raise Http404
    return event


def _error(exc):
    return Response(exc.payload(), status=exc.status)


def _list(event):
    from django.db.models import Count
    questions = [services.serialize_question(q, q.n) for q in event.rsvp_questions.annotate(n=Count('answers')).order_by('position', 'created_at')]
    return {'questions': questions, 'max': services.MAX_QUESTIONS,
            'suggestions': services.suggestions(event) if len(questions) < services.MAX_QUESTIONS else []}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([RsvpThrottle])
def questions(request, event_id):
    event = _own_event(request, event_id)
    if request.method == 'POST':
        try:
            services.create_question(event, request.data)
        except services.RsvpError as exc:
            return _error(exc)
        return Response(_list(event), status=status.HTTP_201_CREATED)
    return Response(_list(event))


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
@throttle_classes([RsvpThrottle])
def question(request, event_id, question_id):
    event = _own_event(request, event_id)
    q = RsvpQuestion.objects.filter(pk=question_id, event=event).first()
    if q is None:
        raise Http404
    if request.method == 'DELETE':
        services.delete_question(q)
        return Response(_list(event))
    try:
        services.update_question(q, request.data)
    except services.RsvpError as exc:
        return _error(exc)
    return Response(_list(event))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([RsvpThrottle])
def reorder(request, event_id):
    event = _own_event(request, event_id)
    try:
        services.reorder(event, request.data.get('order'))
    except services.RsvpError as exc:
        return _error(exc)
    return Response(_list(event))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def answers_summary(request, event_id):
    event = _own_event(request, event_id)
    return Response({'questions': services.summary(event)})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([RsvpThrottle])
def my_rsvp(request, event_id):
    event = Event.objects.filter(id=event_id, deleted_at__isnull=True).first()
    if event is None or not services.can_answer(event, request.user):
        raise Http404
    if request.method == 'POST':
        try:
            services.save_answers(event, request.user, request.data.get('answers'))
        except services.RsvpError as exc:
            return _error(exc)
    return Response({
        'event':     {'id': str(event.id), 'title': event.title},
        'questions': [services.serialize_question(q) for q in event.rsvp_questions.all()],
        'answers':   services.answers_for(event, request.user),
    })
