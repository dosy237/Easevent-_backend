from django.urls import path

from . import views

urlpatterns = [
    path('',                                     views.conversations, name='conversations'),
    path('attachments/<str:token>/',             views.attachment,    name='conversation-attachment'),
    path('unread-count/',                        views.unread_count,  name='conversations-unread'),
    path('<uuid:conversation_id>/',              views.detail,        name='conversation-detail'),
    path('<uuid:conversation_id>/messages/',     views.messages,      name='conversation-messages'),
    path('<uuid:conversation_id>/typing/',       views.typing,        name='conversation-typing'),
]
