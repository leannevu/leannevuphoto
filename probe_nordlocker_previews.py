"""Inspect public share previews without downloading the gallery archive."""
import csv
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent / '.test-tools'))
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('email', help='Client email configured in data/emails.csv')
args = parser.parse_args()
with open('data/emails.csv', newline='', encoding='utf-8-sig') as source:
    url = next(row['folder_url'] for row in csv.DictReader(source)
               if row['email'].casefold() == args.email.casefold())

with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    try:
        page = browser.new_page()
        traffic = []
        def record(response):
            parsed = urlsplit(response.url)
            if response.request.resource_type in ('fetch', 'xhr', 'image'):
                traffic.append(dict(host=parsed.netloc,
                                    status=response.status,
                                    content_type=response.headers.get('content-type'),
                                    length=response.headers.get('content-length')))
        page.on('response', record)
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        page.get_by_role('button', name='Download all', exact=True).wait_for(timeout=60000)
        page.wait_for_timeout(6000)
        print('LIST IMAGES:', page.locator('img').evaluate_all(
            '(els) => els.map(e => ({alt:e.alt,src:e.src.startsWith("blob:") ? "blob:" : e.src.split("?")[0],width:e.naturalWidth,height:e.naturalHeight}))'), flush=True)
        print('INITIAL REQUESTS:', json.dumps(traffic), flush=True)
        traffic.clear()
        first = page.get_by_text(re.compile(r'\.jpe?g$', re.IGNORECASE)).first
        first.dblclick()
        page.wait_for_timeout(12000)
        print('PREVIEW TEXT:', page.locator('body').inner_text()[-5000:], flush=True)
        print('PREVIEW IMAGES:', page.locator('img').evaluate_all(
            '(els) => els.map(e => ({alt:e.alt,src:e.src.startsWith("blob:") ? "blob:" : e.src.split("?")[0],width:e.naturalWidth,height:e.naturalHeight}))'), flush=True)
        print('PREVIEW REQUESTS:', json.dumps(traffic), flush=True)
    finally:
        browser.close()
