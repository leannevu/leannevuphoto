"""On-demand NordLocker decryption using its public client in an isolated browser.

Only metadata and a bounded memory cache are kept. No archive or photo files
are saved. Playwright objects stay on one dedicated thread.
"""
import atexit
import base64
import os
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path


class NordLockerError(RuntimeError):
    pass


class NordLockerBridge:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='nordlocker')
        self.capacity = threading.BoundedSemaphore(24)
        self.browser = None
        self.playwright = None
        self.sessions = OrderedDict()
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.bootstrap = Path(__file__).with_name('bootstrap.js').read_text(encoding='utf-8')

    def _start(self):
        if self.browser and self.browser.is_connected():
            return
        if self.playwright:
            self.playwright.stop()
            self.sessions.clear()
        # Local development tooling; normal deployments install requirements.txt.
        local_tools = Path(__file__).resolve().parents[2] / '.test-tools'
        if local_tools.is_dir():
            sys.path.insert(0, str(local_tools))
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            channel = os.getenv('NORDLOCKER_BROWSER_CHANNEL', 'msedge' if sys.platform == 'win32' else '')
            self.browser = self.playwright.chromium.launch(headless=True, **({'channel': channel} if channel else {}))
        except Exception as exc:
            raise NordLockerError('The NordLocker viewer could not start. Please contact Leanne.') from exc

    def _session(self, url):
        self._start()
        now = time.monotonic()
        # Refresh metadata/authentication after five minutes, and bound open shares.
        for old_url, session in list(self.sessions.items()):
            if now - session['created'] > 300:
                session['context'].close()
                del self.sessions[old_url]
                self._clear_cache(old_url)
        if url in self.sessions:
            self.sessions.move_to_end(url)
            return self.sessions[url]
        while len(self.sessions) >= 3:
            old_url, old_session = self.sessions.popitem(last=False)
            old_session['context'].close()
            self._clear_cache(old_url)
        context = self.browser.new_context(accept_downloads=False)
        page = context.new_page()
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            page.get_by_role('button', name='Download all', exact=True).wait_for(timeout=60000)
            files = page.evaluate(self.bootstrap)
        except Exception:
            context.close()
            raise
        session = dict(context=context, page=page, files=files, created=time.monotonic())
        self.sessions[url] = session
        return session

    def _clear_cache(self, url):
        for key in list(self.cache):
            if key[0] == url:
                self.cache_bytes -= len(self.cache.pop(key)[0])

    def _run(self, url, file_id=None, kind='thumbnail'):
        try:
            session = self._session(url)
            if file_id is None:
                return session['files']
            if file_id not in {item['id'] for item in session['files']}:
                raise NordLockerError('This photo is no longer in your gallery. Please reopen it.')
            # A full-quality preview is identical to the original download.
            key = (url, file_id, 'preview' if kind == 'original' else kind)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            result = session['page'].evaluate('''async ({id, kind}) => {
                let timer;
                try {
                    return await Promise.race([
                        window.photoBridge.photo(id, kind),
                        new Promise((_, reject) => {timer = setTimeout(() => reject(new Error('Photo timed out')), 70000);})
                    ]);
                } finally {clearTimeout(timer);}
            }''', dict(id=file_id, kind=kind))
            value = (base64.b64decode(result['data'], validate=True), result['mimeType'], result['name'])
            if kind != 'original':
                self.cache[key] = value
                self.cache_bytes += len(value[0])
                while self.cache_bytes > 32 * 1024 * 1024:
                    _, old = self.cache.popitem(last=False)
                    self.cache_bytes -= len(old[0])
            return value
        except NordLockerError:
            raise
        except Exception as exc:
            session = self.sessions.pop(url, None)
            if session:
                session['context'].close()
            self._clear_cache(url)
            raise NordLockerError('NordLocker could not load this gallery or photo. Please try again; the share may have expired.') from exc

    def _call(self, url, file_id=None, kind='thumbnail'):
        if not self.capacity.acquire(blocking=False):
            raise NordLockerError('The gallery is busy. Please try again shortly.')
        future = self.executor.submit(self._run, url, file_id, kind)
        future.add_done_callback(lambda _: self.capacity.release())
        try:
            return future.result(timeout=180)
        except TimeoutError as exc:
            future.cancel()
            raise NordLockerError('NordLocker is taking too long. Please try again.') from exc

    def list_images(self, url):
        return self._call(url)

    def photo(self, url, file_id, kind):
        return self._call(url, file_id, kind)

    def close(self):
        def cleanup():
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        try:
            self.executor.submit(cleanup).result(timeout=10)
        except RuntimeError:
            pass  # The interpreter may already have shut down the executor.
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)


bridge = NordLockerBridge()
atexit.register(bridge.close)
