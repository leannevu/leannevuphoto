import unittest
from unittest.mock import Mock, patch

import app


class SelectionEmailTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(app.os.environ, {
            'RESEND_API_KEY': 'test-key',
            'EMAIL_FROM': 'Leanne Vu Photo <notifications@leannevuphoto.com>',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.post = patch.object(app.requests, 'post').start()
        self.addCleanup(patch.stopall)
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {'data': [{'id': 'owner'}, {'id': 'client'}]}
        self.photo = {'id': '1', 'name': 'photo<&>.jpg'}

    def test_separate_recipients_and_client_copy(self):
        app.send_selection_email('client@example.com', 'https://example.com/folder', [self.photo], added=[self.photo])
        owner, client = self.post.call_args.kwargs['json']
        self.assertTrue(self.post.call_args.args[0].endswith('/emails/batch'))
        self.assertEqual(owner['to'], ['leannevuphoto@gmail.com'])
        self.assertEqual(owner['reply_to'], 'client@example.com')
        self.assertIn('Update: 1 added, 0 removed.', owner['text'])
        self.assertIn('Current list (1): [photo<&>.jpg]', owner['text'])
        self.assertEqual(client['to'], ['client@example.com'])
        self.assertEqual(client['reply_to'], 'leannevuphoto@gmail.com')
        self.assertEqual(client['from'], owner['from'])
        self.assertIn('Thank you', client['text'])
        self.assertIn('photo&lt;&amp;&gt;.jpg', client['html'])
        self.assertNotIn('photo<&>.jpg', client['html'])

    def test_removing_last_photo(self):
        app.send_selection_email('client@example.com', 'https://example.com/folder', [], removed=[self.photo])
        client = self.post.call_args.kwargs['json'][1]
        self.assertIn('Removed from your edit list (1)', client['text'])
        self.assertIn('No photos are currently selected', client['text'])

    def test_photographer_picks_only_notify_owner(self):
        self.post.return_value.json.return_value = {'id': 'owner'}
        app.send_selection_email('client@example.com', 'https://example.com/folder', [self.photo], photographer=True)
        payload = self.post.call_args.kwargs['json']
        self.assertEqual(payload['to'], ['leannevuphoto@gmail.com'])
        self.assertTrue(self.post.call_args.args[0].endswith('/emails'))
        self.assertIn('Client selections are unchanged.', payload['text'])

    def test_incomplete_confirmation_fails(self):
        for result in ({'data': [{'id': 'owner'}]}, {'data': [{'id': 'owner'}, {}]}, None):
            with self.subTest(result=result):
                self.post.return_value.json.return_value = result
                with self.assertRaises(app.EmailDeliveryError):
                    app.send_selection_email('client@example.com', 'https://example.com/folder', [self.photo])

    def test_provider_rejection_fails(self):
        self.post.return_value.status_code = 403
        with self.assertRaises(app.EmailDeliveryError):
            app.send_selection_email('client@example.com', 'https://example.com/folder', [self.photo])


if __name__ == '__main__':
    unittest.main()
