"""As found against as left (Phase 9b): the same rail before and after the work, as one
chart. JSON for Chart.js on screen (charts.js, kind "overlay"), SVG for the service report
(WeasyPrint draws it; print-first: grey for as found, the accent for as left)."""

import json
from decimal import Decimal

from markupsafe import Markup, escape

SVG_POINTS = 400            # each trace thinned to about this many points for print


def _s(value):
    return None if value is None else str(value)


def overlay_json(cmp):
    if cmp is None:
        return None
    series = [{"name": "As found", "points": [[str(t), _s(v)] for t, v in cmp["found_points"]]},
              {"name": "As left", "points": [[str(t), _s(v)] for t, v in cmp["left_points"]]}]
    return json.dumps({"kind": "overlay", "unit": cmp["unit"], "series": series,
                       "lo": _s(cmp["lo"]), "hi": _s(cmp["hi"]), "alarm": _s(cmp["alarm"])})


def _thin(points):
    points = [(t, v) for t, v in points if v is not None]
    step = max(1, len(points) // SVG_POINTS)
    return points[::step]


def overlay_svg(cmp, *, accent="#b0126f", width=640, height=220):
    """Both traces on one time axis (seconds from each one's start), with the window."""
    if cmp is None:
        return None
    found, left = _thin(cmp["found_points"]), _thin(cmp["left_points"])
    if not found or not left:
        return None
    levels = [Decimal(v) for v in (cmp["lo"], cmp["hi"], cmp["alarm"]) if v is not None]
    values = [Decimal(v) for _, v in found + left] + levels
    lo_v, hi_v = min(values), max(values)
    pad = (hi_v - lo_v) * Decimal("0.08") or Decimal(1)
    lo_v, hi_v = lo_v - pad, hi_v + pad
    t_max = max(Decimal(found[-1][0]), Decimal(left[-1][0])) or Decimal(1)
    top, bottom, left_m, right_m = 22, 26, 40, 8
    plot_w, plot_h = width - left_m - right_m, height - top - bottom

    def x(t):
        return left_m + float(Decimal(t) / t_max) * plot_w  # float-ok: pixel geometry

    def y(v):
        return top + float((hi_v - Decimal(v)) / (hi_v - lo_v)) * plot_h  # float-ok: pixels

    font = 'font-family="sans-serif" font-size="9"'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'width="100%" role="img">']
    for value, colour, dash in ((cmp["lo"], "#444", "4 3"), (cmp["hi"], "#444", "4 3"),
                                (cmp["alarm"], "#000", "1 2")):
        if value is not None:
            parts.append(f'<line x1="{left_m}" y1="{y(value):.1f}" x2="{width - right_m}" '
                         f'y2="{y(value):.1f}" stroke="{colour}" stroke-width="0.8" '
                         f'stroke-dasharray="{dash}"/>'
                         f'<text x="{left_m - 3}" y="{y(value) + 3:.1f}" text-anchor="end" '
                         f'{font}>{escape(str(value))}</text>')
    for points, colour, w in ((found, "#9a9a9a", 1.2), (left, accent, 1.6)):
        path = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in points)
        parts.append(f'<polyline points="{path}" fill="none" stroke="{colour}" '
                     f'stroke-width="{w}"/>')
    parts.append(f'<line x1="{left_m}" y1="{height - bottom}" x2="{width - right_m}" '
                 f'y2="{height - bottom}" stroke="#444" stroke-width="0.8"/>'
                 f'<text x="{width - right_m}" y="{height - 8}" text-anchor="end" {font}>'
                 f'{escape(str(t_max.quantize(Decimal("0.1"))))} s</text>'
                 f'<rect x="{left_m}" y="6" width="10" height="3" fill="#9a9a9a"/>'
                 f'<text x="{left_m + 14}" y="10" {font}>As found</text>'
                 f'<rect x="{left_m + 70}" y="6" width="10" height="3" fill="{accent}"/>'
                 f'<text x="{left_m + 84}" y="10" {font}>As left</text>'
                 f'<text x="{width - right_m}" y="10" text-anchor="end" {font}>'
                 f'{escape(cmp["unit"])}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


def readings_json(rows):
    """Manual readings with both an as-found and an as-left value, one chart per unit:
    [{"unit", "json"}]. `rows` are docs.reading_rows()."""
    by_unit = {}
    for row in rows:
        before, after = row.get("as_found"), row.get("as_left")
        if before is None or after is None or before.unit != after.unit:
            continue
        d = by_unit.setdefault(after.unit, {"kind": "readings", "unit": after.unit,
                                            "labels": [], "found": [], "left": [], "lo": [],
                                            "hi": []})
        d["labels"].append(row["test_point"])
        d["found"].append(str(before.value))
        d["left"].append(str(after.value))
        d["lo"].append(_s(after.spec_lo))
        d["hi"].append(_s(after.spec_hi))
    return [{"unit": unit, "json": json.dumps(d)} for unit, d in by_unit.items()]
