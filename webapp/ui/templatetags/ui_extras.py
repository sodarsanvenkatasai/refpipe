"""Presentation helpers: labels, badges, formatting and offline SVG charts."""
from __future__ import annotations

import json
from datetime import datetime

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

REASON_LABELS = {
    "VALID": "Valid",
    "MALFORMED_ROW": "Malformed row",
    "MISSING_RECORD_ID": "Missing record id",
    "MISSING_CODE": "Missing code",
    "MISSING_EVENT_DATE": "Missing event date",
    "INVALID_DATE": "Invalid date",
    "INVALID_AMOUNT": "Invalid amount",
    "DUPLICATE_RECORD": "Duplicate record",
    "UNKNOWN_CODE": "Unknown code",
    "INACTIVE_CODE": "Inactive code",
    "DEPRECATED_CODE": "Deprecated code",
    "CODE_NOT_YET_EFFECTIVE": "Not yet effective",
    "CODE_EXPIRED": "Code expired",
    "ORPHAN_ENTITY": "Orphan entity",
    "SCHEMA_MISMATCH": "Schema mismatch",
}

WARN_REASONS = {"DUPLICATE_RECORD", "MALFORMED_ROW"}
REF_REASONS = {"UNKNOWN_CODE", "DEPRECATED_CODE", "CODE_NOT_YET_EFFECTIVE", "CODE_EXPIRED"}


@register.filter
def reason_label(code):
    return REASON_LABELS.get(code, code or "—")


@register.filter
def spaces(value):
    return str(value).replace("_", " ")


@register.filter
def reason_class(code):
    if code in WARN_REASONS:
        return "badge-amber"
    if code in REF_REASONS:
        return "badge-info"
    return "badge-red"


@register.filter
def reason_tone(code):
    if code in WARN_REASONS:
        return "amber"
    if code in REF_REASONS:
        return "info"
    return "red"


@register.filter
def status_class(status):
    return {"SUCCESS": "badge-green", "FAILED": "badge-red", "RUNNING": "badge-amber",
            "STARTING": "badge-amber", "ACTIVE": "badge-green", "DEPRECATED": "badge-amber",
            "INACTIVE": "badge-gray", "VALID": "badge-green", "REJECTED": "badge-red"}.get(status, "badge-gray")


@register.filter
def dt(value):
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return str(value)


@register.filter
def comma(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value if value is not None else "—"


@register.filter
def secs(value):
    try:
        v = float(value)
        return f"{v:.2f} s" if v >= 0.01 else f"{v * 1000:.1f} ms"
    except (TypeError, ValueError):
        return "—"


@register.filter
def pretty_json(value):
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return json.dumps(value, indent=2, sort_keys=True)


@register.simple_tag
def donut_svg(valid, rejected, size=148):
    """Offline SVG donut: share of valid rows (signal green) vs rejected (rose)."""
    try:
        valid, rejected = int(valid or 0), int(rejected or 0)
    except (TypeError, ValueError):
        valid = rejected = 0
    total = valid + rejected
    pct = round(100 * valid / total, 1) if total else 0
    dash = pct  # circle circumference is 100 with r=15.9155
    svg = f"""
<svg viewBox="0 0 42 42" width="{size}" height="{size}" role="img"
     aria-label="{pct}% valid, {100 - pct if total else 0}% rejected">
  <defs>
    <linearGradient id="donut-valid" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#5eead4"></stop>
      <stop offset="100%" stop-color="#16a34a"></stop>
    </linearGradient>
    <linearGradient id="donut-rejected" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#fb7185"></stop>
      <stop offset="100%" stop-color="#e11d48"></stop>
    </linearGradient>
  </defs>
  <circle cx="21" cy="21" r="15.9155" fill="none" stroke="var(--line)" stroke-width="4.4"></circle>
  <circle class="donut-slice" cx="21" cy="21" r="15.9155" fill="none" stroke="url(#donut-rejected)" stroke-width="4.4"
          stroke-dasharray="{100 - dash if total else 0} {dash if total else 100}"
          stroke-dashoffset="25" stroke-linecap="round" style="animation-delay:.15s"></circle>
  <circle class="donut-slice" cx="21" cy="21" r="15.9155" fill="none" stroke="url(#donut-valid)" stroke-width="4.4"
          stroke-dasharray="{dash} {100 - dash if total else 100}" stroke-dashoffset="25" stroke-linecap="round"></circle>
  <text x="21" y="20.4" text-anchor="middle" class="donut-num">{pct}%</text>
  <text x="21" y="25.4" text-anchor="middle" class="donut-cap">valid</text>
</svg>"""
    return mark_safe(svg)


SPARK_COLUMNS = {"runs": "input_count", "valid": "valid_count", "rejected": "rejected_count"}


@register.simple_tag
def spark_svg(kind):
    """Inline sparkline of the last 12 runs (or loaded snapshots) for a KPI card."""
    from .. import services

    series = []
    try:
        with services.pipeline_conn() as conn:
            if kind == "versions":
                rows = conn.execute(
                    "SELECT row_count FROM reference_versions"
                    " ORDER BY loaded_at ASC, reference_version ASC"
                ).fetchall()
            else:
                col = SPARK_COLUMNS.get(kind, "input_count")
                rows = conn.execute(
                    f"SELECT {col} FROM pipeline_runs ORDER BY started_at ASC, run_id ASC"
                ).fetchall()
            series = [r[0] or 0 for r in rows][-12:]
    except Exception:
        series = []
    if not series:
        series = [0, 0]
    elif len(series) == 1:
        series = [series[0], series[0]]
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1
    pts = [(round(i * 120 / (len(series) - 1), 2),
            round(30 - (v - lo) * 26 / span + 3, 2)) for i, v in enumerate(series)]
    line = " ".join(f"{x},{y}" for x, y in pts)
    area = "M0,36 L" + " L".join(f"{x},{y}" for x, y in pts) + " L120,36 Z"
    return mark_safe(
        f'<svg class="spark" viewBox="0 0 120 36" preserveAspectRatio="none" aria-hidden="true">'
        f'<path d="{area}" fill="currentColor" opacity=".12"></path>'
        f'<polyline points="{line}" fill="none" stroke="currentColor" stroke-width="2"'
        f' stroke-linecap="round" stroke-linejoin="round"></polyline></svg>'
    )