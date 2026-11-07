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
        return "badge-indigo"
    return "badge-red"


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
    """Offline SVG donut: share of valid rows (green) vs rejected (red)."""
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
  <circle cx="21" cy="21" r="15.9155" fill="none" stroke="#e2e8f0" stroke-width="5"></circle>
  <circle cx="21" cy="21" r="15.9155" fill="none" stroke="#ef4444" stroke-width="5"
          stroke-dasharray="{100 - dash if total else 0} {dash if total else 100}"
          stroke-dashoffset="25" stroke-linecap="butt"></circle>
  <circle cx="21" cy="21" r="15.9155" fill="none" stroke="#22c55e" stroke-width="5"
          stroke-dasharray="{dash} {100 - dash}" stroke-dashoffset="25" stroke-linecap="butt"></circle>
  <text x="21" y="20.4" text-anchor="middle" class="donut-num">{pct}%</text>
  <text x="21" y="25.4" text-anchor="middle" class="donut-cap">valid</text>
</svg>"""
    return mark_safe(svg)