// App UI behaviour. Plain JS, no inline handlers (the CSP forbids them).

// Copy buttons: <button data-copy="text">.
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  const text = button.dataset.copy;
  try {
    await navigator.clipboard.writeText(text);
    const label = button.textContent;
    button.textContent = 'Copied';
    button.classList.add('copied');
    setTimeout(() => { button.textContent = label; button.classList.remove('copied'); }, 1500);
  } catch (err) {
    window.prompt('Copy this:', text);
  }
});

// Share buttons: <button data-share="url" data-share-title="...">: the phone's share
// sheet (text, email…) where there is one, else copy.
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-share]');
  if (!button) return;
  const url = button.dataset.share;
  if (navigator.share) {
    try { await navigator.share({ title: button.dataset.shareTitle || '', url }); } catch (err) { /* cancelled */ }
    return;
  }
  try { await navigator.clipboard.writeText(url); button.textContent = 'Copied'; }
  catch (err) { window.prompt('Copy this:', url); }
});

// Destructive buttons: <button data-confirm="Remove this line?">.
document.addEventListener('submit', (event) => {
  const button = event.submitter;
  const message = button && button.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

// Running timers: <span data-elapsed="ISO start time"> shows h:mm since then.
function tick() {
  for (const el of document.querySelectorAll('[data-elapsed]')) {
    const start = Date.parse(el.dataset.elapsed);
    if (Number.isNaN(start)) continue;
    const minutes = Math.max(0, Math.floor((Date.now() - start) / 60000));
    el.textContent = `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`;
  }
}
tick();
setInterval(tick, 15000);
