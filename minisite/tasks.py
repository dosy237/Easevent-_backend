from celery import shared_task


@shared_task(name='minisite.generate', ignore_result=True, soft_time_limit=150, time_limit=180)
def generate_minisite(generation_id):
    from .services import run
    run(generation_id)
