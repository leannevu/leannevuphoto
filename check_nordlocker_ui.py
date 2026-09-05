"""Opt-in browser check against the real public gallery; no email is sent."""
import sys
import threading
from pathlib import Path
from unittest.mock import patch
from werkzeug.serving import make_server, WSGIRequestHandler
import app

sys.path.insert(0, str(Path(__file__).parent / '.test-tools'))
from playwright.sync_api import sync_playwright

class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass  # Do not log signed photo URLs.

def main():
    server = make_server('127.0.0.1', 0, app.app, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p, patch.object(app, 'send_selection_email') as send:
            browser = p.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width':1280,'height':900}, reduced_motion='reduce')
                errors = []
                photo_requests = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: photo_requests.append(req.url) if '/api/nordlocker/photos/' in req.url else None)
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('#email').fill('leannevuphoto@gmail.com')
                page.locator('#gallery-form button').click()
                page.locator('#gallery-section').wait_for(state='visible', timeout=120000)
                page.wait_for_function("document.querySelector('#gallery img')?.naturalWidth > 0", timeout=120000)
                assert page.locator('.photo').count() == 173
                print('Visible gallery: 173 cards; first thumbnail decrypted', flush=True)
                assert len(photo_requests) < 173
                print('On-demand photo requests:', len(photo_requests), flush=True)
                page.locator('.select-button').first.click()
                assert page.locator('#selected-count').inner_text() == '1'
                page.locator('.photo').first.click()
                page.wait_for_function("document.querySelector('#lightbox-image').naturalWidth > 600 && document.querySelector('#lightbox-image').src.includes('preview')", timeout=120000)
                print('Lightbox: larger decrypted preview displayed', flush=True)
                page.locator('#lightbox-close').click()
                page.locator('#tray-toggle').click()
                page.locator('#submit-selection').click()
                page.wait_for_function("document.querySelector('#submit-message').textContent.includes('successfully')")
                assert send.call_count == 1
                print('Selection workflow: passed with mocked email', flush=True)
                page.set_viewport_size({'width':390,'height':844})
                page.locator('.photo').first.scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
                print('Mobile width and browser script errors: passed', flush=True)
            finally:
                browser.close()
    finally:
        server.shutdown()
        app.bridge.close()

if __name__ == '__main__':
    main()
