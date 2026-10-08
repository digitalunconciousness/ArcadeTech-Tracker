// Finger signatures: <canvas data-pad> inside <form data-signature-form>, with a hidden
// input named "signature" that gets the drawing as a PNG data URL on submit.
(() => {
  for (const form of document.querySelectorAll('[data-signature-form]')) {
    const pad = form.querySelector('[data-pad]');
    const out = form.querySelector('input[name="signature"]');
    const error = form.querySelector('[data-pad-error]');
    const ctx = pad.getContext('2d');
    let drawn = false;
    let last = null;

    // Phones fire "resize" whenever the address bar shows or hides while scrolling. Only a
    // real change of width (rotation) resizes the canvas, and the drawing is kept.
    let width = 0;
    function size() {
      const ratio = window.devicePixelRatio || 1;
      const rect = pad.getBoundingClientRect();
      if (Math.round(rect.width) === width) return;
      let copy = null;
      if (drawn) {
        copy = document.createElement('canvas');
        copy.width = pad.width;
        copy.height = pad.height;
        copy.getContext('2d').drawImage(pad, 0, 0);
      }
      width = Math.round(rect.width);
      pad.width = Math.round(rect.width * ratio);
      pad.height = Math.round(rect.height * ratio);
      if (copy) ctx.drawImage(copy, 0, 0, pad.width, pad.height);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.strokeStyle = '#000';
      ctx.fillStyle = '#000';
    }
    function point(event) {
      const rect = pad.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }
    pad.addEventListener('pointerdown', (event) => {
      pad.setPointerCapture(event.pointerId);
      last = point(event);
      ctx.beginPath();
      ctx.arc(last.x, last.y, 1, 0, Math.PI * 2);
      ctx.fill();
    });
    pad.addEventListener('pointermove', (event) => {
      if (!last) return;
      const p = point(event);
      ctx.beginPath();
      ctx.moveTo(last.x, last.y);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
      last = p;
      drawn = true;
      if (error) error.hidden = true;
    });
    for (const name of ['pointerup', 'pointercancel', 'pointerleave']) {
      pad.addEventListener(name, () => { last = null; });
    }
    const clear = form.querySelector('[data-pad-clear]');
    if (clear) {
      clear.addEventListener('click', () => {
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, pad.width, pad.height);
        ctx.restore();
        drawn = false;
      });
    }
    form.addEventListener('submit', (event) => {
      if (!drawn) {
        event.preventDefault();
        if (error) error.hidden = false;
        return;
      }
      out.value = pad.toDataURL('image/png');
    });
    size();
    window.addEventListener('resize', size);
  }
})();
