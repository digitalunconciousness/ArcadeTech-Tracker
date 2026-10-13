// The desktop (1024 px and up): records open beside their list, column choices, ticking
// rows for bulk actions, and the command bar (Ctrl K) with a few keyboard shortcuts.
// Plain JS; every feature is a shortcut for something the pages already do without it.
(function () {
  const wide = window.matchMedia('(min-width: 64rem)');
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  // --- split view ------------------------------------------------------------------------
  // Only record pages, on this site, ever load into the pane.
  const RECORD = /^\/(work|work\/jobs|estimates|customers|assets|invoices|payments|parts|purchasing\/orders|credit-memos|traces)\/\d+\/?(#.*)?$/;
  const split = document.querySelector('[data-split]');
  const pane = document.getElementById('pane');
  let current = null;

  const listUrl = () => { const u = new URL(window.location.href); u.searchParams.delete('pane'); return u; };
  const markRow = (path) => {
    if (!split) return;
    split.querySelectorAll('tr.current').forEach((tr) => tr.classList.remove('current'));
    if (path) split.querySelectorAll(`tr[data-href="${CSS.escape(path)}"]`).forEach((tr) => tr.classList.add('current'));
  };
  const closePane = (push) => {
    if (!pane) return;
    pane.hidden = true;
    pane.textContent = '';
    split.classList.remove('open');
    current = null;
    markRow(null);
    if (push) window.history.pushState({}, '', listUrl());
  };
  async function openPane(path, push) {
    if (!pane || !RECORD.test(path)) { window.location.href = path; return; }
    let resp;
    try {
      resp = await fetch(path, { headers: { 'X-Partial': '1' }, credentials: 'same-origin' });
    } catch (e) { window.location.href = path; return; }
    const landed = new URL(resp.url);
    if (!resp.ok || landed.origin !== window.location.origin || !RECORD.test(landed.pathname)) {
      window.location.href = path;
      return;
    }
    // Our own server's HTML for our own page; inline scripts can't run under the CSP anyway.
    pane.innerHTML = await resp.text();
    const bar = document.createElement('div');
    bar.className = 'pane-bar';
    const full = document.createElement('a');
    full.href = path; full.className = 'btn small'; full.textContent = 'Open full page';
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'btn small'; close.textContent = 'Close';
    close.setAttribute('data-pane-close', '');
    bar.append(full, close);
    pane.prepend(bar);
    pane.hidden = false;
    pane.scrollTop = 0;
    split.classList.add('open');
    current = path;
    markRow(path);
    if (push) {
      const u = listUrl();
      u.searchParams.set('pane', path);
      window.history.pushState({ pane: path }, '', u);
    }
  }
  if (split && pane) {
    split.addEventListener('click', (event) => {
      if (!wide.matches || event.defaultPrevented || event.button !== 0 ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (event.target.closest('[data-pane-close]')) { closePane(true); return; }
      if (pane.contains(event.target)) return;              // the pane's own links navigate
      if (event.target.closest('input, select, button, label, [data-no-pane]')) return;
      const link = event.target.closest('a[href]');
      const row = event.target.closest('tr[data-href]');
      const href = link ? link.getAttribute('href') : (row ? row.dataset.href : null);
      if (!href || !RECORD.test(href) || (link && (link.target || link.hasAttribute('download')))) return;
      event.preventDefault();
      if (href === current) { closePane(true); return; }
      openPane(href, true);
    });
    window.addEventListener('popstate', () => {
      const path = new URL(window.location.href).searchParams.get('pane');
      if (path && wide.matches) openPane(path, false); else closePane(false);
    });
    const start = new URL(window.location.href).searchParams.get('pane');
    if (start && wide.matches) openPane(start, false);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && current && !document.querySelector('dialog[open]')) closePane(true);
    });
  }

  // --- columns and ticks -----------------------------------------------------------------
  document.querySelectorAll('.power[data-page]').forEach((panel) => {
    const page = panel.dataset.page;
    const table = document.querySelector(`table.power-table[data-page="${CSS.escape(page)}"]`);
    if (!table) return;
    const hidden = new Set(store.get(`cols:${page}`) || []);
    const apply = () => {
      table.querySelectorAll('[data-col]').forEach((cell) => { cell.hidden = hidden.has(cell.dataset.col); });
      panel.querySelectorAll('[data-col-toggle]').forEach((box) => { box.checked = !hidden.has(box.dataset.colToggle); });
    };
    panel.addEventListener('change', (event) => {
      const box = event.target.closest('[data-col-toggle]');
      if (!box) return;
      if (box.checked) hidden.delete(box.dataset.colToggle); else hidden.add(box.dataset.colToggle);
      store.set(`cols:${page}`, [...hidden]);
      apply();
    });
    apply();
  });
  document.addEventListener('change', (event) => {
    const all = event.target.closest('[data-pick-all]');
    if (!all) return;
    all.closest('table').querySelectorAll('tbody input[type="checkbox"][name]').forEach((box) => { box.checked = all.checked; });
  });

  // --- command bar -------------------------------------------------------------------------
  const dialog = document.getElementById('cmd');
  if (!dialog) return;
  const input = document.getElementById('cmd-input');
  const list = document.getElementById('cmd-list');
  const commands = JSON.parse(dialog.dataset.commands || '[]');
  let items = [], active = 0, timer = null, ticket = 0;

  const render = () => {
    list.textContent = '';
    items.forEach((item, i) => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.className = i === active ? 'active' : '';
      const kind = document.createElement('span'); kind.className = 'cmd-kind'; kind.textContent = item.kind;
      const label = document.createElement('span'); label.className = 'cmd-label'; label.textContent = item.label;
      const sub = document.createElement('span'); sub.className = 'cmd-sub'; sub.textContent = item.sub || item.keys || '';
      li.append(kind, label, sub);
      li.addEventListener('click', () => go(item));
      list.append(li);
    });
  };
  const matchCommands = (text) => {
    const t = text.trim().toLowerCase();
    return commands.filter((c) => !t || c.label.toLowerCase().includes(t))
      .map((c) => ({ kind: 'Go to', label: c.label, url: c.url, keys: c.keys }));
  };
  const go = (item) => { if (item && item.url) window.location.href = item.url; };
  const search = (text) => {
    const mine = ++ticket;
    items = matchCommands(text).slice(0, text.trim() ? 6 : 30);
    active = 0;
    render();
    if (text.trim().length < 2) return;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const url = new URL(dialog.dataset.search, window.location.origin);
        url.searchParams.set('q', text.trim());
        url.searchParams.set('format', 'json');
        const resp = await fetch(url, { credentials: 'same-origin' });
        if (!resp.ok || mine !== ticket) return;
        const found = (await resp.json()).items || [];
        items = found.concat(matchCommands(text).slice(0, 4));
        active = 0;
        render();
      } catch (e) { /* keep the commands */ }
    }, 150);
  };
  const openBar = () => {
    if (dialog.open) return;
    dialog.showModal();
    input.value = '';
    search('');
    input.focus();
  };
  input.addEventListener('input', () => search(input.value));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { active = Math.min(active + 1, items.length - 1); render(); event.preventDefault(); }
    else if (event.key === 'ArrowUp') { active = Math.max(active - 1, 0); render(); event.preventDefault(); }
    else if (event.key === 'Enter') { go(items[active]); event.preventDefault(); }
    // A search box swallows the first Esc to clear itself; one press should close.
    else if (event.key === 'Escape') { dialog.close(); event.preventDefault(); }
  });
  dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => input.blur());
  document.addEventListener('click', (event) => { if (event.target.closest('[data-cmd]')) openBar(); });

  // --- keyboard shortcuts --------------------------------------------------------------------
  const typing = (el) => el && !el.closest('dialog:not([open])') &&
    (el.isContentEditable || /^(input|select|textarea)$/i.test(el.tagName));
  const goKeys = Object.fromEntries(commands.filter((c) => c.keys.startsWith('g ')).map((c) => [c.keys.slice(2), c.url]));
  let pendingG = 0;
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openBar(); return; }
    if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target) || dialog.open) return;
    if (event.key === '/') { event.preventDefault(); openBar(); return; }
    if (Date.now() - pendingG < 1200 && goKeys[event.key]) { window.location.href = goKeys[event.key]; pendingG = 0; return; }
    pendingG = event.key === 'g' ? Date.now() : 0;
    if (event.key === 'n' && wide.matches) {
      const button = document.querySelector('.desk-only [data-new], .head a.btn.primary[href]');
      if (button) window.location.href = button.getAttribute('href');
    }
  });
})();
