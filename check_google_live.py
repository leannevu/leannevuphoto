"""Opt-in Google gallery check; samples one thumbnail and never sends email."""
import csv
from unittest.mock import patch
import requests
import app


def check_gallery(email):
    client = app.app.test_client()
    result = client.post('/api/gallery', json={'email':email})
    assert result.status_code == 200, result.json
    images = result.json['images']
    print('Google gallery:', len(images), 'photos; stage:', result.json['stage'], flush=True)
    assert images and not result.json.get('lazy')
    first = images[0]
    with requests.get(first['thumbnail'], timeout=30, stream=True) as thumbnail:
        assert thumbnail.status_code == 200, f'Thumbnail status: {thumbnail.status_code}'
        assert thumbnail.headers.get('Content-Type', '').startswith('image/'), 'Thumbnail was not an image'
        assert next(thumbnail.iter_content(32)), 'Empty thumbnail'
        print('Real Google thumbnail: image response received', flush=True)
    access = app.access_for_email(email)
    _, url = app.current_stage(access)
    with patch.object(app, 'access_for_email', return_value={url:{'stage':'choose_edits','process':'google'}}), patch.object(app, 'send_selection_email') as send:
        result = client.post('/api/submit', json={'email':email, 'files':[first]})
        assert result.status_code == 200, result.json
        assert send.call_args.args[2][0]['name'] == first['name']
        assert 'drive.google.com/file/d/' in send.call_args.args[2][0]['viewUrl']
        print('Google selection: correct filename/link; email mocked', flush=True)
    with patch.object(app, 'access_for_email', return_value={url:{'stage':'wait_for_edits','process':'google'}}):
        result = client.post('/api/gallery', json={'email':email})
        assert result.status_code == 200 and result.json['images'] == []
        print('Google waiting stage: passed', flush=True)
    assert 'drive.google.com/uc?' in first['downloadUrl']
    print('Google final download links: present', flush=True)


def main():
    with app.EMAILS_CSV.open(newline='', encoding='utf-8-sig') as source:
        emails = list(dict.fromkeys(row['email'] for row in csv.DictReader(source) if row.get('process') == 'google'))
    assert emails, 'No Google gallery is configured'
    for email in emails:
        check_gallery(email)


if __name__ == '__main__':
    main()
