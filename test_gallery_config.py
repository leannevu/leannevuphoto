import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app

GOOGLE = 'https://drive.google.com/drive/folders/abc'
NORD = 'https://cloud.nordlocker.com/shares/unlock/11111111-1111-1111-1111-111111111111#' + 'a' * 43
EMAIL = 'client@example.com'


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.working = tempfile.TemporaryDirectory()
        self.addCleanup(self.working.cleanup)
        self.path = Path(self.working.name) / 'emails.csv'
        self.env = patch.dict(os.environ, {'CLIENT_GALLERIES_JSON':''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.csv_patch = patch.object(app, 'EMAILS_CSV', self.path)
        self.csv_patch.start()
        self.addCleanup(self.csv_patch.stop)

    def write(self, rows):
        with self.path.open('w', newline='', encoding='utf-8-sig') as output:
            csv.writer(output).writerows(rows)

    def test_explicit_process_routes_both_providers(self):
        for url, process in [(GOOGLE,'google'), (NORD,'nord')]:
            with self.subTest(process=process):
                self.write([['email','folder_url','stage','process'], [EMAIL,url,'choose_edits',process.upper()]])
                with patch.object(app, 'list_images', return_value=[{'id':'g','name':'photo.JPG'}]) as google, patch.object(app.bridge, 'list_images', return_value=[{'id':'n','name':'photo.JPG'}]) as nord:
                    result = app.app.test_client().post('/api/gallery', json={'email':EMAIL.upper()})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(google.call_count, int(process == 'google'))
                    self.assertEqual(nord.call_count, int(process == 'nord'))

    def test_mismatched_or_unknown_process_has_clear_error(self):
        for url, process in [(NORD,'google'), (GOOGLE,'nord'), (GOOGLE,'other'), (GOOGLE,'')]:
            self.write([['email','folder_url','stage','process'], [EMAIL,url,'choose_edits',process]])
            result = app.app.test_client().post('/api/gallery', json={'email':EMAIL})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json['code'], 'GALLERY_CONFIG_ERROR')
            self.assertIn('process', result.json['error'])

    def test_old_formats_and_stage_priority(self):
        self.write([['email','folder_url','stage'], [EMAIL,GOOGLE,'choose_edits'], [EMAIL,NORD,'final_edits']])
        access = app.access_for_email(EMAIL)
        self.assertEqual(app.current_stage(access), ('final_edits',NORD))
        self.assertEqual(app.gallery_process(access,GOOGLE), 'google')
        self.write([['email','galleries'], [EMAIL,str({GOOGLE:'choose_edits'})]])
        self.assertEqual(app.current_stage(app.access_for_email(EMAIL)), ('choose_edits',GOOGLE))

    def test_json_supports_old_and_new_entries(self):
        for config in ['choose_edits', {'stage':'choose_edits','process':'google'}]:
            with patch.dict(os.environ, {'CLIENT_GALLERIES_JSON':json.dumps({EMAIL:{GOOGLE:config}})}):
                access = app.access_for_email(EMAIL)
                self.assertEqual(app.gallery_process(access,GOOGLE), 'google')
                self.assertEqual(app.current_stage(access), ('choose_edits',GOOGLE))

    def test_unrelated_invalid_row_does_not_break_client(self):
        self.write([['email','folder_url','stage','process'], ['other@example.com','bad'], [EMAIL,GOOGLE,'choose_edits','google']])
        self.assertEqual(app.current_stage(app.access_for_email(EMAIL)), ('choose_edits',GOOGLE))


if __name__ == '__main__':
    unittest.main()
