// The dispatch calendar: bookings laid out by time in a lane per tech (or a column per day),
// a work order dragged from "To schedule" onto a lane books it, a booking dragged moves it,
// and its bottom edge makes it longer or shorter. Each change is saved, then the page
// reloads so what's shown is what the server has. Positions are set here: the page's CSP
// refuses style="" attributes.
(function () {
  const cal = document.querySelector('[data-dispatch]');
  if (!cal) return;
  const START = Number(cal.dataset.dayStart) * 60;
  const END = Number(cal.dataset.dayEnd) * 60;
  const SLOT = Number(cal.dataset.slot);
  const DEFAULT = Number(cal.dataset.default);
  const canBook = cal.hasAttribute('data-can-book');
  const msg = document.querySelector('[data-dispatch-msg]');
  const say = (text, bad) => {
    if (!msg) return;
    msg.textContent = text || '';
    msg.className = 'board-msg ' + (bad ? 'error' : 'ok');
  };
  // A message saved before the reload that follows a change.
  try {
    const kept = JSON.parse(sessionStorage.getItem('dispatch-msg') || 'null');
    sessionStorage.removeItem('dispatch-msg');
    if (kept) say(kept.text, kept.bad);
  } catch (e) { /* private mode */ }
  const keep = (text, bad) => {
    try { sessionStorage.setItem('dispatch-msg', JSON.stringify({ text, bad })); } catch (e) { /* ok */ }
  };

  const slotPx = (col) => col.clientHeight / ((END - START) / SLOT);
  const pad = (n) => String(n).padStart(2, '0');
  const clock = (m) => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;

  // --- layout: top and height from the times; side by side where bookings overlap -------
  function layout(col) {
    const px = slotPx(col);
    const appts = [...col.querySelectorAll('.appt')]
      .map((el) => ({ el, start: Number(el.dataset.start), end: Number(el.dataset.end) }))
      .sort((a, b) => a.start - b.start || b.end - a.end);
    let group = [], groupEnd = -1;
    const flush = () => {
      const tracks = [];
      group.forEach((a) => {
        let t = tracks.findIndex((end) => end <= a.start);
        if (t < 0) { t = tracks.length; tracks.push(0); }
        tracks[t] = a.end;
        a.track = t;
      });
      group.forEach((a) => {
        a.el.style.left = `${(a.track / tracks.length) * 100}%`;
        a.el.style.width = `calc(${100 / tracks.length}% - 3px)`;
      });
      group = [];
    };
    appts.forEach((a) => {
      if (a.start >= groupEnd && group.length) flush();
      group.push(a);
      groupEnd = Math.max(groupEnd, a.end);
      a.el.style.top = `${((a.start - START) / SLOT) * px}px`;
      a.el.style.height = `${Math.max(((a.end - a.start) / SLOT) * px - 2, 14)}px`;
    });
    if (group.length) flush();
  }
  const cols = [...cal.querySelectorAll('.cal-col')];
  const layoutAll = () => cols.forEach(layout);
  layoutAll();
  window.addEventListener('resize', layoutAll);
  // Start the day scrolled to 07:00.
  const body = cal.querySelector('.cal-body');
  if (body && cols[0]) body.scrollTop = slotPx(cols[0]) * ((7 * 60 - START) / SLOT);
  if (!canBook) return;

  async function post(url, fields) {
    const form = new FormData();
    form.set('csrf_token', cal.dataset.csrf);
    Object.entries(fields).forEach(([k, v]) => form.set(k, v));
    let resp = null, data = {};
    try {
      resp = await fetch(url, { method: 'POST', body: form, credentials: 'same-origin' });
      data = await resp.json();
    } catch (e) { /* offline, or not JSON */ }
    if (resp && resp.ok) {
      const warn = (data.warnings || []).map((w) => `Heads up: ${w}.`).join(' ');
      keep([data.message, warn].filter(Boolean).join(' '), Boolean(warn));
      window.location.reload();
      return;
    }
    say(data.error || "Couldn't save that. Reload the page and try again.", true);
    layoutAll();
  }

  // The minute under the pointer in a column, snapped, keeping a booking inside the day.
  const minuteAt = (col, clientY, offset, length) => {
    const y = clientY - col.getBoundingClientRect().top - offset;
    const m = START + Math.round(y / slotPx(col)) * SLOT;
    return Math.min(Math.max(m, START), END - length);
  };

  // --- dragging: a work order from the list, or a booking -------------------------------
  let drag = null;
  const ghost = document.createElement('div');
  ghost.className = 'appt-ghost';
  ghost.hidden = true;
  cal.addEventListener('dragstart', (event) => {
    if (event.target.closest('.resize')) { event.preventDefault(); return; }
    const item = event.target.closest('.queue-item[draggable="true"]');
    const appt = event.target.closest('.appt[draggable="true"]');
    if (item) {
      drag = { kind: 'book', wo: item.dataset.wo, length: DEFAULT, offset: 0 };
    } else if (appt) {
      const col = appt.closest('.cal-col');
      drag = { kind: 'move', url: appt.dataset.move, from: col.dataset.user,
               length: Number(appt.dataset.end) - Number(appt.dataset.start),
               offset: event.clientY - appt.getBoundingClientRect().top };
      appt.classList.add('dragging');
    } else return;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', drag.wo || drag.url);
  });
  cal.addEventListener('dragend', () => {
    cal.querySelectorAll('.appt.dragging').forEach((el) => el.classList.remove('dragging'));
    ghost.hidden = true;
    drag = null;
  });
  cal.addEventListener('dragover', (event) => {
    const col = event.target.closest('.cal-col');
    if (!drag || !col) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const m = minuteAt(col, event.clientY, drag.offset, drag.length);
    if (ghost.parentElement !== col) col.append(ghost);
    ghost.hidden = false;
    ghost.style.top = `${((m - START) / SLOT) * slotPx(col)}px`;
    ghost.style.height = `${(drag.length / SLOT) * slotPx(col)}px`;
    ghost.textContent = `${clock(m)}–${clock(m + drag.length)}`;
  });
  cal.addEventListener('drop', (event) => {
    const col = event.target.closest('.cal-col');
    if (!drag || !col) return;
    event.preventDefault();
    const m = minuteAt(col, event.clientY, drag.offset, drag.length);
    const startsAt = `${col.dataset.day}T${clock(m)}`;
    const d = drag;
    ghost.hidden = true;
    if (d.kind === 'book') {
      post(cal.dataset.book, { wo_id: d.wo, starts_at: startsAt, minutes: d.length,
                               user: col.dataset.user === '*' ? '' : col.dataset.user });
    } else {
      post(d.url, { starts_at: startsAt, minutes: d.length, from_user: d.from,
                    to_user: col.dataset.user });
    }
  });

  // --- resizing: the bottom edge of a booking -------------------------------------------
  cal.addEventListener('pointerdown', (event) => {
    const handle = event.target.closest('.resize');
    if (!handle || event.button !== 0) return;
    event.preventDefault();
    const appt = handle.closest('.appt');
    const col = appt.closest('.cal-col');
    const start = Number(appt.dataset.start);
    const y0 = event.clientY;
    const length0 = Number(appt.dataset.end) - start;
    let length = length0;
    appt.draggable = false;
    handle.setPointerCapture(event.pointerId);
    const label = appt.querySelector('[data-time]');
    const onMove = (e) => {
      const steps = Math.round((e.clientY - y0) / slotPx(col));
      length = Math.min(Math.max(length0 + steps * SLOT, SLOT), END - start);
      appt.style.height = `${(length / SLOT) * slotPx(col) - 2}px`;
      if (label) label.textContent = `${clock(start)}–${clock(start + length)}`;
    };
    const onUp = () => {
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('pointercancel', onUp);
      appt.draggable = true;
      if (length !== length0) {
        post(appt.dataset.move, { starts_at: `${col.dataset.day}T${clock(start)}`, minutes: length,
                                  from_user: '*', to_user: '*' });
      }
    };
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
  });
})();
