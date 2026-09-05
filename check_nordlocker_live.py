"""Opt-in live test: decrypt one thumbnail and one preview; never send email."""
from unittest.mock import patch
import app

def main():
    client = app.app.test_client()
    email = 'leannevuphoto@gmail.com'
    try:
        result = client.post('/api/gallery', json={'email': email})
        assert result.status_code == 200, result.json
        files = result.json['images']
        print('Gallery:', len(files), 'photos; lazy:', result.json['lazy'], flush=True)
        item = files[0]
        for key in ('thumbnail', 'previewUrl'):
            result = client.get(item[key])
            assert result.status_code == 200, result.json
            assert result.data.startswith(b'\xff\xd8'), 'Not a JPEG'
            print(key, len(result.data), 'bytes', flush=True)
            result.close()
        with patch.object(app, 'send_selection_email') as send:
            result = client.post('/api/submit', json={'email': email, 'files': [dict(item, name='forged')]})
            assert result.status_code == 200, result.json
            assert send.call_args.args[2][0]['name'] == item['name']
            assert 'cloud.nordlocker.com' in send.call_args.args[2][0]['viewUrl']
            print('Selection: correct original filename and NordLocker link; email mocked', flush=True)
        assert client.get('/api/nordlocker/photos/forged').status_code == 404
        assert client.get(item['downloadUrl']).status_code == 409
        print('Forged photo link and premature final download: blocked', flush=True)
    finally:
        app.bridge.close()

if __name__ == '__main__':
    main()
