// Presence only: no duration, click history, IP address or device tracking.
(() => {
  let visit = null;
  let context = null;
  let starting = false;
  let nextAttempt = 0;
  let sequence = 0;
  let generation = 0;
  const viewing = () => document.visibilityState === 'visible' && document.hasFocus();

  function ping(ended = false, beacon = false) {
    if (!visit) return;
    const payload = JSON.stringify({visit_id: visit, sequence: ++sequence, active: viewing(), ended});
    if (beacon && navigator.sendBeacon?.('/api/activity/heartbeat', new Blob([payload], {type: 'application/json'}))) return;
    fetch('/api/activity/heartbeat', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: payload, keepalive: true,
    }).catch(() => {}); // Tracking failures must never interrupt a gallery.
  }

  async function start() {
    if (!context || starting || visit || Date.now() < nextAttempt) return;
    const current = generation;
    starting = true;
    nextAttempt = Date.now() + 30000;
    try {
      const response = await fetch('/api/activity/start', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(context), signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) return;
      const data = await response.json();
      if (current !== generation) {
        // A gallery changed while its start request was in flight.
        fetch('/api/activity/heartbeat', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({visit_id:data.visit_id, sequence:1, active:false, ended:true}), keepalive:true}).catch(() => {});
        return;
      }
      visit = data.visit_id;
      sequence = 0;
      ping();
    } catch { /* The same UUID makes a later start retry idempotent. */ }
    finally { starting = false; }
  }

  function stop() {
    ping(true, true);
    generation++;
    visit = null;
    context = null;
    nextAttempt = 0;
  }

  window.galleryActivity = {
    open(email, galleryId) {
      if (document.body.dataset.photographer === 'true') return;
      if (!globalThis.crypto?.randomUUID) return;
      if (context?.email === email && context.gallery_id === galleryId) return;
      stop();
      context = {email, gallery_id: galleryId, visit_id: crypto.randomUUID()};
      start();
    },
    stop,
  };
  setInterval(() => visit ? ping() : start(), 15000);
  document.addEventListener('visibilitychange', () => ping(false, true));
  window.addEventListener('focus', () => ping());
  window.addEventListener('blur', () => ping(false, true));
  window.addEventListener('pagehide', () => {
    ping(true, true);
    generation++;
    visit = null;
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted && context) {
      context.visit_id = crypto.randomUUID();
      nextAttempt = 0;
      start();
    }
  });
})();
