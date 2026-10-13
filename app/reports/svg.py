"""Bar charts as SVG, drawn on the server from the same data Chart.js gets on screen: for
the browser's print, the P&L PDF and the year-end package (WeasyPrint renders SVG).
Print-first: black, grey and the document accent on white. Presentation attributes, not
style="": the page's CSP refuses inline styles, inline SVG included."""

from decimal import ROUND_HALF_UP, Decimal

from markupsafe import Markup, escape

COLORS = ("#222222", "{accent}", "#9a9a9a")
FONT = 'font-family="sans-serif" font-size="{size}"'


def _num(value, money):
    v = Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return f"{'-' if v < 0 else ''}${abs(v):,}" if money else f"{v:,}"


def _colors(accent):
    return [c.format(accent=accent) for c in COLORS]


def bars(data, *, money=True, accent="#b0126f", width=640, height=260, title=None):
    """Grouped vertical bars, one group per label, one bar per series."""
    labels, series = data["labels"], data["series"]
    colors = _colors(accent)
    top, bottom, left, right = (34 if title else 14) + (16 if len(series) > 1 else 0), 40, 8, 8
    plot_h, plot_w = height - top - bottom, width - left - right
    peak = max([Decimal(v) for _, vals in series for v in vals] + [Decimal(0)])
    floor = min([Decimal(v) for _, vals in series for v in vals] + [Decimal(0)])
    span = (peak - floor) or Decimal(1)
    base_y = top + plot_h * peak / span
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'width="100%" role="img">']
    if title:
        parts.append(f'<text x="{left}" y="16" {FONT.format(size=13)} font-weight="bold">'
                     f'{escape(title)}</text>')
    if len(series) > 1:
        x = left
        for i, (name, _) in enumerate(series):
            y = top - 12
            parts.append(f'<rect x="{x}" y="{y - 9}" width="10" height="10" fill="{colors[i % 3]}"/>'
                         f'<text x="{x + 14}" y="{y}" {FONT.format(size=11)}>'
                         f'{escape(name)}</text>')
            x += 20 + 7 * len(name)
    n = max(len(labels), 1)
    group = plot_w / n
    bar_w = min(group * 0.8 / max(len(series), 1), 48)
    for i, label in enumerate(labels):
        gx = left + group * i + (group - bar_w * len(series)) / 2
        for j, (_, vals) in enumerate(series):
            v = Decimal(vals[i])
            h = float(plot_h * abs(v) / span)  # float-ok: pixel geometry, not money
            y = float(base_y) - (h if v >= 0 else 0)  # float-ok: pixel geometry
            x = gx + bar_w * j
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                         f'fill="{colors[j % 3]}"/>')
            if v:
                ty = y - 3 if v >= 0 else y + h + 10
                parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{ty:.1f}" text-anchor="middle" '
                             f'{FONT.format(size=8)}>{escape(_num(v, money))}</text>')
        parts.append(f'<text x="{left + group * (i + 0.5):.1f}" y="{height - bottom + 14}" '
                     f'text-anchor="middle" {FONT.format(size=9)}>'
                     f'{escape(str(label)[:14])}</text>')
    axis_y = float(base_y)  # float-ok: pixel geometry, not money
    parts.append(f'<line x1="{left}" y1="{axis_y:.1f}" x2="{width - right}" '
                 f'y2="{axis_y:.1f}" stroke="#444" stroke-width="1"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def hbars(data, *, money=True, accent="#b0126f", width=640, title=None):
    """Horizontal bars, one per label (long names, such as customers)."""
    labels, (_, vals) = data["labels"], data["series"][0]
    row, label_w, top = 22, 190, 24 if title else 6
    height = top + row * max(len(labels), 1) + 6
    peak = max([Decimal(v) for v in vals] + [Decimal(1)])
    plot_w = width - label_w - 70
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'width="100%" role="img">']
    if title:
        parts.append(f'<text x="0" y="16" {FONT.format(size=13)} font-weight="bold">'
                     f'{escape(title)}</text>')
    for i, (label, v) in enumerate(zip(labels, vals, strict=True)):
        y = top + row * i
        w = float(plot_w * max(Decimal(v), Decimal(0)) / peak)  # float-ok: pixel geometry
        parts.append(f'<text x="{label_w - 6}" y="{y + 14}" text-anchor="end" '
                     f'{FONT.format(size=10)}>{escape(str(label)[:28])}</text>'
                     f'<rect x="{label_w}" y="{y + 4}" width="{w:.1f}" height="14" '
                     f'fill="{_colors(accent)[0]}"/>'
                     f'<text x="{label_w + w + 4:.1f}" y="{y + 15}" '
                     f'{FONT.format(size=9)}>{escape(_num(v, money))}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))
