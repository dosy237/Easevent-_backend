from celery import shared_task


@shared_task(name='events.broadcast_likes', ignore_result=True)
def broadcast_likes(event_id):
    from .engagement import broadcast_likes_now
    broadcast_likes_now(event_id)
