"""Vidéo de présentation : signature d'envoi direct, vérification réelle de la durée, droits."""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

import cloudinary

from events.models import Event
from events.tests_lifecycle import client_for
from events.video import folder_for
from users.models import User


class VideoTest(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email='o@x.fr', password='x', first_name='O', last_name='R', is_verified=True,
                                             subscription_plan='pro')
        self.other = User.objects.create_user(email='p@x.fr', password='x', first_name='P', last_name='R', is_verified=True)
        start = timezone.now() + timedelta(days=10)
        self.event = Event.objects.create(organizer=self.user, title='Festival', event_type='festival', visibility='public',
                                          status='published', start_date=start, end_date=start + timedelta(hours=5))
        self.c = client_for(self.user)
        self.pid = f'{folder_for(self.user)}/abc123'
        cloudinary.config(cloud_name='easevent', api_key='key', api_secret='secret')

    def tearDown(self):
        cloudinary.config(cloud_name='dummy', api_key='dummy', api_secret='dummy')

    def patch_video(self, video):
        return self.c.patch(f'/api/events/{self.event.id}/update/', {'video': video}, format='json')

    def test_signature_dans_le_dossier_de_l_organisateur(self):
        r = self.c.post('/api/events/video/signature/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['folder'], folder_for(self.user))
        self.assertEqual(r.data['max_seconds'], 45)
        self.assertTrue(r.data['upload_url'].endswith('/video/upload'))
        self.assertEqual(len(r.data['signature']), 40)

    @mock.patch('events.video.cloudinary.uploader.destroy')
    @mock.patch('events.video.cloudinary.api.resource')
    def test_video_acceptee_et_affichee(self, resource, destroy):
        resource.return_value = {'duration': 44.6, 'bytes': 9_000_000, 'format': 'mp4', 'width': 1080, 'height': 1920}
        r = self.patch_video({'public_id': self.pid, 'caption': 'Les coulisses du festival'})
        self.assertEqual(r.status_code, 200, r.data)
        detail = self.c.get(f'/api/events/publics/{self.event.id}/').data['video']
        self.assertEqual((detail['duration'], detail['width'], detail['height']), (44.6, 1080, 1920))
        self.assertEqual(detail['caption'], 'Les coulisses du festival')
        self.assertIn('q_auto', detail['url'])
        self.assertTrue(detail['poster'].endswith('.jpg'))
        # Changer seulement la légende ne réinterroge pas Cloudinary
        resource.reset_mock()
        self.assertEqual(self.patch_video({'public_id': self.pid, 'caption': 'Nouveau texte'}).status_code, 200)
        resource.assert_not_called()
        # Retirer la vidéo : supprimée de Cloudinary
        self.assertEqual(self.patch_video(None).status_code, 200)
        destroy.assert_called_with(self.pid, resource_type='video', invalidate=True)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.video)

    @mock.patch('events.video.cloudinary.uploader.destroy')
    @mock.patch('events.video.cloudinary.api.resource')
    def test_video_trop_longue_refusee_et_supprimee(self, resource, destroy):
        resource.return_value = {'duration': 61.0, 'bytes': 9_000_000, 'format': 'mp4'}
        r = self.patch_video({'public_id': self.pid})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['code'], 'too_long')
        destroy.assert_called_once()

    @mock.patch('events.video.cloudinary.api.resource')
    def test_video_d_un_autre_utilisateur_refusee(self, resource):
        r = self.patch_video({'public_id': f'{folder_for(self.other)}/vol'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['code'], 'not_found')
        resource.assert_not_called()

    @mock.patch('events.video.cloudinary.api.resource')
    def test_reservee_aux_evenements_publics(self, resource):
        self.event.visibility = 'private'
        self.event.save()
        r = self.patch_video({'public_id': self.pid})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['code'], 'video_public_only')
