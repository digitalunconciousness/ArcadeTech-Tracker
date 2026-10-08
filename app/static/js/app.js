// Small helpers, no framework. Loaded on every page (CSP: script-src 'self').

// Copy buttons: <button data-copy="text">. The label printer's app runs on the same
// phone, so copy here and paste there.
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
