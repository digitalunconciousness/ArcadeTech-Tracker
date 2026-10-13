// Charts: Chart.js (vendored) on screen, from data in each canvas's
// data-chart attribute. Printing shows the server-drawn copy next to it instead.
(function () {
  if (!window.Chart) return;
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  const colors = [v('--magenta'), v('--cyan'), v('--lime')];
  const whole = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
  const cents = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  // A GATBOX trace: the reading over seconds, with the window and the alarm as lines.
  const line = (canvas, d) => {
    const pts = d.points.map(([t, val]) => ({ x: Number(t), y: val === null ? null : Number(val) }));
    const last = pts.length ? pts[pts.length - 1].x : 0;
    const level = (value, label, color) => ({
      label, data: [{ x: 0, y: Number(value) }, { x: last, y: Number(value) }],
      borderColor: color, borderDash: [6, 4], borderWidth: 1, pointRadius: 0, showLine: true,
    });
    const sets = [{ label: d.unit, data: pts, borderColor: v('--cyan'), borderWidth: 1.5,
                    pointRadius: 0, showLine: true, spanGaps: false }];
    if (d.lo !== null) sets.push(level(d.lo, 'window', v('--lime')));
    if (d.hi !== null) sets.push(level(d.hi, 'window', v('--lime')));
    if (d.alarm !== null) sets.push(level(d.alarm, 'alarm', v('--red')));
    new window.Chart(canvas, {
      type: 'scatter',
      data: { datasets: sets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false },
                   tooltip: { callbacks: { label: (ctx) => `${ctx.raw.y} ${d.unit} at ${ctx.raw.x} s` } } },
        scales: {
          x: { type: 'linear', ticks: { color: v('--dim') }, grid: { color: v('--border') } },
          y: { ticks: { color: v('--dim') }, grid: { color: v('--border') } },
        },
      },
    });
  };
  // As found against as left: both traces on one time axis, with the window and alarm.
  const overlay = (canvas, d) => {
    const sets = d.series.map((s, i) => ({
      label: s.name, borderColor: i === 0 ? v('--amber') : v('--cyan'), borderWidth: i === 0 ? 1.2 : 1.6,
      pointRadius: 0, showLine: true, spanGaps: false,
      data: s.points.map(([t, val]) => ({ x: Number(t), y: val === null ? null : Number(val) })),
    }));
    const last = Math.max(0, ...sets.map((s) => (s.data.length ? s.data[s.data.length - 1].x : 0)));
    const level = (value, label, color) => ({
      label, data: [{ x: 0, y: Number(value) }, { x: last, y: Number(value) }],
      borderColor: color, borderDash: [6, 4], borderWidth: 1, pointRadius: 0, showLine: true,
    });
    if (d.lo !== null) sets.push(level(d.lo, 'window', v('--lime')));
    if (d.hi !== null) sets.push(level(d.hi, 'window', v('--lime')));
    if (d.alarm !== null) sets.push(level(d.alarm, 'alarm', v('--red')));
    new window.Chart(canvas, {
      type: 'scatter',
      data: { datasets: sets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { labels: { color: v('--text'), filter: (item) => item.text.startsWith('As ') } },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.raw.y} ${d.unit} at ${ctx.raw.x} s` } },
        },
        scales: {
          x: { type: 'linear', ticks: { color: v('--dim') }, grid: { color: v('--border') } },
          y: { ticks: { color: v('--dim') }, grid: { color: v('--border') } },
        },
      },
    });
  };
  // Manual readings per test point: as found → as left, over the spec band.
  const readings = (canvas, d) => {
    const num = (list) => list.map((x) => (x === null ? null : Number(x)));
    const found = num(d.found), left = num(d.left), lo = num(d.lo), hi = num(d.hi);
    const band = /^#[0-9a-f]{6}$/i.test(v('--lime')) ? `${v('--lime')}33` : v('--hover');
    const point = (label, data, color) => ({
      type: 'line', label, data, showLine: false, pointRadius: 5, borderColor: color, backgroundColor: color,
    });
    new window.Chart(canvas, {
      type: 'bar',
      data: {
        labels: d.labels,
        datasets: [
          point('As found', found, v('--amber')),
          point('As left', left, v('--cyan')),
          { label: 'spec', grouped: false, backgroundColor: band, borderWidth: 0, barPercentage: 0.7,
            data: lo.map((l, i) => (l !== null && hi[i] !== null ? [l, hi[i]] : null)) },
          { label: 'change', grouped: false, backgroundColor: v('--dim'), barThickness: 3,
            data: found.map((f, i) => [f, left[i]]) },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { labels: { color: v('--text'), filter: (item) => item.text !== 'change' } },
          tooltip: {
            filter: (item) => item.dataset.label !== 'change',
            callbacks: { label: (ctx) => (Array.isArray(ctx.raw)
              ? `spec ${ctx.raw[0]}–${ctx.raw[1]} ${d.unit}` : `${ctx.dataset.label}: ${ctx.raw} ${d.unit}`) },
          },
        },
        scales: {
          x: { ticks: { color: v('--dim') }, grid: { display: false } },
          y: { ticks: { color: v('--dim') }, grid: { color: v('--border') } },
        },
      },
    });
  };
  for (const canvas of document.querySelectorAll('canvas[data-chart]')) {
    const d = JSON.parse(canvas.dataset.chart);
    if (d.kind === 'line') { line(canvas, d); continue; }
    if (d.kind === 'overlay') { overlay(canvas, d); continue; }
    if (d.kind === 'readings') { readings(canvas, d); continue; }
    const money = (n, exact) => (n < 0 ? '-' : '') + '$' + (exact ? cents : whole).format(Math.abs(n));
    const value = (n, exact) => (d.money ? money(n, exact) : whole.format(n));
    const axis = { ticks: { color: v('--dim'), callback: (n) => value(n) }, grid: { color: v('--border') } };
    const labels = { ticks: { color: v('--dim') }, grid: { display: false } };
    new window.Chart(canvas, {
      type: 'bar',
      data: {
        labels: d.labels,
        datasets: d.series.map((s, i) => ({
          label: s.name, data: s.data.map(Number), backgroundColor: colors[i % colors.length],
          maxBarThickness: 40,
        })),
      },
      options: {
        indexAxis: d.horizontal ? 'y' : 'x',
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        plugins: {
          legend: { display: d.series.length > 1, labels: { color: v('--text') } },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${value(ctx.raw, true)}` } },
        },
        scales: d.horizontal ? { x: axis, y: labels } : { x: labels, y: axis },
      },
    });
  }
})();
