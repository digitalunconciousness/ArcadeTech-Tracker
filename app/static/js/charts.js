// The dashboard's charts: Chart.js (vendored) on screen, from data in each canvas's
// data-chart attribute. Printing shows the server-drawn copy next to it instead.
(function () {
  if (!window.Chart) return;
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  const colors = [v('--magenta'), v('--cyan'), v('--lime')];
  const whole = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
  const cents = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  for (const canvas of document.querySelectorAll('canvas[data-chart]')) {
    const d = JSON.parse(canvas.dataset.chart);
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
