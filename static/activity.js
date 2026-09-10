(() => {
  const $ = id => document.getElementById(id);
  let visits = [], filter = 'all', busy = false, timer, initialized = false, known = new Set();
  const formatter = new Intl.DateTimeFormat(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit', second:'2-digit'});
  function render() {
    const query = $('activity-search').value.trim().toLowerCase();
    const visible = visits.filter(visit => (filter === 'all' || visit.status === 'viewing') && `${visit.email} ${visit.gallery}`.toLowerCase().includes(query));
    $('activity-rows').replaceChildren(...visible.map(visit => {
      const row = document.createElement('tr');
      const client = document.createElement('td');
      const email = document.createElement('span'); email.className='activity-client'; email.textContent=visit.email;
      const gallery = document.createElement('span'); gallery.className='activity-gallery'; gallery.textContent=visit.gallery || 'Gallery';
      client.append(email, gallery);
      const status = document.createElement('td');
      const badge = document.createElement('span'); badge.className=`visit-status ${visit.status}`;
      badge.textContent={viewing:'Viewing now', idle:'Away', left:'Not viewing', unknown:'Status unavailable'}[visit.status];
      status.append(badge); row.append(client,status);
      for (const field of ['started_at','last_seen_at']) {
        const cell = document.createElement('td'); cell.className='visit-date';
        const time = document.createElement('time'); time.dateTime=visit[field]; time.textContent=formatter.format(new Date(visit[field]));
        cell.append(time); row.append(cell);
      }
      return row;
    }));
    $('activity-empty').hidden=visible.length>0;
    $('activity-empty').textContent=filter==='viewing' ? 'No one is viewing a gallery right now.' : query ? 'No matching arrivals.' : 'No gallery arrivals yet. New visits will appear here automatically.';
  }
  async function refresh() {
    if (busy) return;
    clearTimeout(timer); busy=true; $('activity-refresh').disabled=true;
    try {
      const response = await fetch('/api/photographer/activity', {cache:'no-store', signal:AbortSignal.timeout(10000)});
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.visits) || !data.summary) throw new Error(data.error || 'Activity could not be loaded.');
      const arrivals = data.visits.filter(visit => !known.has(visit.id));
      visits=data.visits;
      known = new Set(visits.map(visit => visit.id));
      if (initialized && arrivals.length) $('activity-update').textContent=`New arrival: ${arrivals[0].email} opened ${arrivals[0].gallery || 'a gallery'}${arrivals.length>1 ? ` (+${arrivals.length-1} more)` : ''}.`;
      initialized=true;
      $('active-count').textContent=data.summary.active_now;
      $('visit-count').textContent=data.summary.visits;
      $('client-count').textContent=data.summary.clients;
      $('connection-state').textContent='Live · every 5 seconds';
      document.querySelector('.connection').className='connection live';
      $('activity-error').hidden=true;
      $('activity-updated').textContent=`Updated ${new Date().toLocaleTimeString()}`;
      render();
    } catch (error) {
      visits=visits.map(visit => ({...visit,status:'unknown'})); render();
      if (!initialized) $('activity-empty').textContent='Activity could not be loaded.';
      $('active-count').textContent='—';
      $('connection-state').textContent='Connection interrupted';
      document.querySelector('.connection').className='connection failed';
      $('activity-error').hidden=false;
      $('activity-error').textContent=`${error.message} Retrying automatically.`;
    } finally {
      busy=false; $('activity-refresh').disabled=false;
      if (!document.hidden) timer=setTimeout(refresh,5000);
    }
  }
  document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
    filter=button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(item => item.setAttribute('aria-pressed',String(item===button)));
    render();
  }));
  $('activity-search').addEventListener('input',render);
  $('activity-refresh').addEventListener('click',refresh);
  document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden) refresh(); });
  refresh();
})();
