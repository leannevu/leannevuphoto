"""Opt-in live Google browser check. Email and original downloads are mocked."""
import csv
import sys
import threading
from pathlib import Path
from unittest.mock import patch
from werkzeug.serving import make_server
from check_nordlocker_ui import QuietHandler
import app

sys.path.insert(0, str(Path(__file__).parent / '.test-tools'))
from playwright.sync_api import sync_playwright


def main():
    with app.EMAILS_CSV.open(newline='', encoding='utf-8-sig') as source:
        row = next(row for row in csv.DictReader(source) if row.get('process') == 'google')
    server = make_server('127.0.0.1', 0, app.app, threaded=True, request_handler=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p, patch.object(app, 'send_selection_email') as send, patch.object(app.bridge, 'list_images', side_effect=AssertionError('Google called NordLocker')):
            browser = p.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width':1280,'height':900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('#email').fill(row['email'])
                page.locator('#gallery-form button').click()
                page.wait_for_function("!document.querySelector('#gallery-section').hidden || document.querySelector('#form-message').textContent", timeout=120000)
                assert page.locator('#gallery-section').is_visible(), page.locator('#form-message').inner_text()
                count = page.locator('.photo').count()
                assert count > 0
                assert page.locator('body').get_attribute('data-stage') == 'final_edits'
                print('Google final gallery:', count, 'photos loaded', flush=True)
                page.locator('.photo').first.click()
                page.wait_for_function("document.querySelector('#lightbox-image').naturalWidth > 0")
                page.locator('#lightbox-close').click()
                page.locator('.select-button').first.click()
                page.locator('#tray-toggle').click()
                page.evaluate("window.testDownloads=[]; HTMLAnchorElement.prototype.click=function(){window.testDownloads.push(this.href)}")
                page.locator('#submit-selection').click()
                page.wait_for_timeout(600)
                print('Download diagnostic:', page.evaluate("({stage:state.stage, selected:state.selected.size, clicks:window.testDownloads.length, message:document.querySelector('#submit-message').textContent})"), 'errors:', errors, flush=True)
                page.wait_for_function('window.testDownloads.length >= 1')
                assert 'drive.google.com/uc?' in page.evaluate('window.testDownloads[0]')
                print('Google lightbox and final download action: passed (original download intercepted)', flush=True)
                with patch.object(app, 'access_for_email', return_value={row['folder_url']:{'stage':'choose_edits','process':'google'}}):
                    page.locator('#gallery-form button').click()
                    page.wait_for_function("document.body.dataset.stage === 'choose_edits'")
                    page.locator('.select-button').first.click()
                    if not page.locator('#submit-selection').is_visible():
                        page.locator('#tray-toggle').click()
                    page.locator('#submit-selection').click()
                    page.wait_for_function("document.querySelector('#submit-message').textContent.includes('successfully')")
                    assert send.call_count == 1
                    print('Google choose-edits submission: passed (email mocked)', flush=True)
                assert not errors, errors
                print('Browser script errors: none', flush=True)
            finally:
                browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
