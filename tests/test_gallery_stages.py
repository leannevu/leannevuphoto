import unittest
from unittest.mock import patch

import app


class GalleryStagesTests(unittest.TestCase):
    def setUp(self):
        self.proofs = 'https://drive.google.com/drive/folders/proofs'
        self.finals = 'https://drive.google.com/drive/folders/finals'
        self.access = {
            self.proofs: dict(stage='choose_edits', process='google', gallery='Portraits', date='2026-07-20'),
            self.finals: dict(stage='final_edits', process='google', gallery=' portraits ', date='2026-07-20'),
        }

    def test_matching_shoot_opens_finals_and_allows_return_to_proofs(self):
        def selections(email, url, stage):
            return dict(stage=stage, saved=[{'id': 'saved'}], sent=[])

        with patch.object(app, 'access_for_email', return_value=self.access), \
             patch.object(app.photographer_auth, 'open_workspace', return_value=None), \
             patch.object(app, 'selection_state', side_effect=selections), \
             patch.object(app, 'list_images', return_value=[{'id': 'photo', 'name': 'photo.jpg'}]) as images:
            client = app.app.test_client()
            payload = {'email': 'client@example.com'}
            result = client.post('/api/gallery', json=payload)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['stage'], 'final_edits')
            self.assertEqual(len(result.json['galleries']), 1)
            self.assertEqual(len(result.json['gallery']['stages']), 2)
            images.assert_called_with('finals')
            result = client.post('/api/gallery', json=dict(payload, gallery_id=app.share_fingerprint(self.proofs), choose_more=True))
            self.assertEqual(result.json['stage'], 'choose_edits')
            self.assertEqual(result.json['selections']['saved'], [{'id': 'saved'}])
            images.assert_called_with('proofs')
            self.assertEqual(client.post('/api/gallery', json=dict(payload, gallery_id='foreign')).status_code, 400)

    def test_owner_browses_client_picks_without_separate_selection_api(self):
        client = app.app.test_client()
        client.post('/api/gallery', json={'email': app.photographer_auth.OWNER_EMAIL})
        saved = [{'id': 'saved', 'name': 'saved.jpg'}]
        sent = [{'id': 'sent', 'name': 'sent.jpg'}]
        with patch.object(app, 'access_for_email', return_value=self.access), \
             patch.object(app, 'selection_state', return_value=dict(saved=saved, sent=sent, stage='wait_for_edits')), \
             patch.object(app, 'list_images', return_value=saved + sent):
            response = client.post('/api/gallery', json={
                'email': 'client@example.com', 'photographer_mode': True,
                'gallery_id': app.share_fingerprint(self.proofs),
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['selections']['client_saved'], saved)
        self.assertEqual(response.json['selections']['client_sent'], sent)
        self.assertEqual(response.json['selections']['saved'], [])
        self.assertEqual(response.json['images'], saved + sent)
        self.assertEqual(client.post('/api/photographer/selections', json={}).status_code, 404)
        html = client.get('/photographer').get_data(as_text=True)
        self.assertIn('Client picks', html)
        self.assertNotIn('Photographer picks', html)
        self.assertNotIn('copy-my-photos', html)

    def test_different_dates_names_and_missing_metadata_stay_separate(self):
        for field, value in [('date', '2026-07-21'), ('gallery', 'Wedding'), ('date', ''), ('gallery', '')]:
            with self.subTest(field=field, value=value):
                access = {url: dict(config) for url, config in self.access.items()}
                access[self.finals][field] = value
                self.assertEqual(len(app.gallery_choices(access)), 2)

    def test_latest_stage_wins_regardless_of_row_order(self):
        self.assertEqual(app.gallery_choices(dict(reversed(list(self.access.items()))))[0]['stage'], 'final_edits')
        self.access[self.finals]['stage'] = 'wait_for_edits'
        self.assertEqual(app.gallery_choices(self.access)[0]['stage'], 'wait_for_edits')


if __name__ == '__main__':
    unittest.main()
