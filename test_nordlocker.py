import unittest
from unittest.mock import patch
import app
from nordlocker import NordLockerError

SHARE = 'https://cloud.nordlocker.com/shares/unlock/11111111-1111-1111-1111-111111111111#' + 'a' * 43
EMAIL = 'client@example.com'
FILES = [{'id':'11111111-2222-3333-4444-555555555555', 'name':'proof.JPG'}]


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.access = patch.object(app, 'access_for_email', return_value={SHARE:'choose_edits'}).start()
        self.listing = patch.object(app.bridge, 'list_images', return_value=FILES).start()
        self.photo = patch.object(app.bridge, 'photo', return_value=(b'image', 'image/jpeg', 'proof.JPG')).start()
        self.send = patch.object(app, 'send_selection_email').start()
        self.addCleanup(patch.stopall)

    def gallery(self):
        response = self.client.post('/api/gallery', json={'email':EMAIL})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_gallery_is_metadata_only_and_tokens_do_not_expose_share_key(self):
        result = self.gallery()
        self.assertTrue(result['lazy'])
        self.assertEqual(result['count'], 1)
        self.photo.assert_not_called()
        self.assertNotIn(SHARE, str(result))
        self.assertNotIn('a' * 43, str(result))
        response = self.client.get(result['images'][0]['thumbnail'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        response.close()

    def test_stages_and_revocation(self):
        item = self.gallery()['images'][0]
        self.assertEqual(self.client.get(item['downloadUrl']).status_code, 409)
        self.access.return_value = {SHARE:'final_edits'}
        response = self.client.get(item['downloadUrl'])
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        response.close()
        self.access.return_value = {SHARE:'wait_for_edits'}
        self.assertEqual(self.gallery()['images'], [])
        self.assertEqual(self.client.get(item['thumbnail']).status_code, 404)
        self.access.return_value = {SHARE.replace('11111111-1111', '22222222-1111'):'choose_edits'}
        self.assertEqual(self.client.get(item['thumbnail']).status_code, 404)
        self.access.return_value = None
        self.assertEqual(self.client.get(item['thumbnail']).status_code, 404)

    def test_forged_expired_and_invalid_photo_requests(self):
        self.assertEqual(self.client.get('/api/nordlocker/photos/forged').status_code, 404)
        item = self.gallery()['images'][0]
        with patch('time.time', return_value=1):
            token = app.photo_tokens.dumps([EMAIL, app.share_fingerprint(SHARE), FILES[0]['id']])
        self.assertEqual(self.client.get('/api/nordlocker/photos/' + token).status_code, 404)
        self.assertEqual(self.client.get(item['thumbnail'] + '?kind=bad').status_code, 404)
        self.photo.assert_not_called()

    def test_selection_uses_server_filenames_and_rejects_foreign_ids(self):
        item = dict(FILES[0], name='forged', viewUrl='https://example.com')
        result = self.client.post('/api/submit', json={'email':EMAIL, 'files':[item, item]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.send.call_args.args[2], [dict(FILES[0], viewUrl=SHARE)])
        self.send.reset_mock()
        result = self.client.post('/api/submit', json={'email':EMAIL, 'files':[{'id':'unknown'}]})
        self.assertEqual(result.status_code, 400)
        self.send.assert_not_called()

    def test_upstream_failure_and_drive_regression(self):
        self.listing.side_effect = NordLockerError('NordLocker is unavailable.')
        self.assertEqual(self.client.post('/api/gallery', json={'email':EMAIL}).status_code, 502)
        self.access.return_value = {'https://drive.google.com/drive/folders/abc':'choose_edits'}
        with patch.object(app, 'list_images', return_value=FILES) as drive:
            result = self.gallery()
            self.assertNotIn('lazy', result)
            drive.assert_called_once_with('abc')


if __name__ == '__main__':
    unittest.main()
