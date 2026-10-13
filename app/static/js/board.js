// The work board: drag a card to another column (or use its Move menu) and the work
// order's status is saved, as the status form on its page would. A card carries the status
// it was in; if someone changed it meanwhile, the server refuses and the card goes back.
(function () {
  const board = document.querySelector('[data-board]');
  if (!board) return;
  const csrf = board.dataset.csrf;
  const msg = document.querySelector('[data-board-msg]');
  const say = (text, bad) => {
    if (!msg) return;
    msg.textContent = text || '';
    msg.className = 'board-msg ' + (bad ? 'error' : 'ok');
  };
  const column = (status) => board.querySelector(`[data-column="${CSS.escape(status)}"]`);
  const recount = () => board.querySelectorAll('[data-column]').forEach((col) => {
    const cards = col.querySelectorAll('.card').length;
    col.querySelector('[data-count]').textContent = cards;
    const empty = col.querySelector('.board-empty');
    if (empty) empty.hidden = cards > 0;
  });
  const place = (card, status, before) => {
    const col = column(status);
    if (!col) { card.remove(); return; }           // a status the board doesn't show
    const list = col.querySelector('.board-list');
    if (before && before.parentElement === list) list.insertBefore(card, before);
    else list.prepend(card);
    card.dataset.status = status;
    const form = card.querySelector('form.card-move');
    if (form) { form.elements.status.value = status; form.elements.from.value = status; }
  };

  async function move(card, to) {
    const from = card.dataset.status;
    if (!to || to === from) return;
    if (to === 'completed' && card.hasAttribute('data-unbilled') &&
        !window.confirm(`${card.dataset.number} has lines that aren't billed yet. Mark it completed anyway?`)) {
      place(card, from, card.nextElementSibling);
      return;
    }
    const home = card.nextElementSibling;
    place(card, to);
    recount();
    const body = new FormData();
    body.set('csrf_token', csrf);
    body.set('status', to);
    body.set('from', from);
    let resp = null, data = {};
    try {
      resp = await fetch(card.dataset.action, {
        method: 'POST', body, credentials: 'same-origin', headers: { Accept: 'application/json' },
      });
      data = await resp.json();
    } catch (e) { /* offline, or not JSON */ }
    if (resp && resp.ok) { say(data.message); return; }
    const real = data.status || from;
    place(card, real, real === from ? home : null);
    recount();
    say(data.error || "Couldn't save that. Reload the page and try again.", true);
  }

  // Dragging (a mouse on a wide screen; phones use the Move menu).
  let dragging = null;
  board.addEventListener('dragstart', (event) => {
    const card = event.target.closest('.card[draggable="true"]');
    if (!card) return;
    dragging = card;
    card.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', card.dataset.number);
  });
  board.addEventListener('dragend', () => {
    if (dragging) dragging.classList.remove('dragging');
    dragging = null;
    board.querySelectorAll('.board-col.drop').forEach((col) => col.classList.remove('drop'));
  });
  board.addEventListener('dragover', (event) => {
    const col = event.target.closest('[data-column]');
    if (!dragging || !col) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    board.querySelectorAll('.board-col.drop').forEach((c) => { if (c !== col) c.classList.remove('drop'); });
    col.classList.add('drop');
  });
  board.addEventListener('drop', (event) => {
    const col = event.target.closest('[data-column]');
    if (!dragging || !col) return;
    event.preventDefault();
    col.classList.remove('drop');
    move(dragging, col.dataset.column);
  });

  // The Move menu, without leaving the page.
  board.addEventListener('submit', (event) => {
    const form = event.target.closest('form.card-move');
    if (!form) return;
    event.preventDefault();
    move(form.closest('.card'), form.elements.status.value);
  });
  recount();
})();
