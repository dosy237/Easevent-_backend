"""
python manage.py fix_demo_covers
Remplace les couvertures des événements de démonstration par des images
cohérentes avec leur type et leur titre (voir events/demo_covers.py).
Ne touche jamais aux photos envoyées par les organisateurs (Cloudinary).
Idempotent : peut être lancé à chaque démarrage.
"""
from django.core.management.base import BaseCommand

from events.demo_covers import cover_for, is_demo_cover
from events.models import Event


class Command(BaseCommand):
    help = "Associe des couvertures cohérentes aux événements de démonstration"

    def handle(self, *args, **options):
        updated = 0
        for event in Event.objects.exclude(cover_image__isnull=True).only('id', 'title', 'event_type', 'cover_image'):
            if not is_demo_cover(event.cover_image):
                continue
            cover = cover_for(event.event_type, event.title)
            if cover != event.cover_image:
                Event.objects.filter(pk=event.pk).update(cover_image=cover)
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"Couvertures de démo mises à jour : {updated}"))
