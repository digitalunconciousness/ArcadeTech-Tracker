// Loaded in <head>, before the page draws: blurred money stays blurred from the first frame.
try {
  if (localStorage.getItem('blur') === '1') document.documentElement.classList.add('blurred');
} catch (e) { /* storage blocked: start unblurred */ }
