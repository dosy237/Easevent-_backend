from celery import shared_task


@shared_task(name='minisite.generate', ignore_result=True, soft_time_limit=270, time_limit=300)
def generate_minisite(generation_id):
    from .services import run
    run(generation_id)
