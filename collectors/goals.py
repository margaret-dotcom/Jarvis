"""Goals collector: reads goals/goals.yaml and reports status where it can.

Every goal starts as status unknown with a note that the evening summary
fills it in. Goals with `metric: domos` are computed live from DOM OS using
one of the named computes below. A goal's `target` can be a number or a
DOM OS target path such as `atwc_targets.revenue.monthly`, in which case
the number DOM OS holds today is used, so editing a target in DOM OS
changes the goal here without touching this file.

Named computes (source.compute):
  atwc_revenue_month          sum of atwc_revenue_lines.total_fee this month
  atwc_sessions_week          count of atwc_billing_sessions this week (Mon start)
  atwc_waitlist_conversion    won / (won + lost) among waitlist entered this quarter
  qca_sold_month              sum of sales_daily_logs.sold_amount this month
  qca_collections_month       sum of qca_collections.amount this month
  qca_min_gp_active           lowest gross profit percent across Scheduled/In Progress jobs

Status: on_track when current meets the target, at_risk within ten percent,
behind otherwise. Anything unsupported stays unknown.
"""
from __future__ import annotations

from datetime import date
from typing import Callable

import yaml

from . import config

EVENING_NOTE = "Status is filled in by the evening summary."


def _load_yaml() -> list[dict]:
    if not config.GOALS_PATH.exists():
        return []
    data = yaml.safe_load(config.GOALS_PATH.read_text(encoding="utf-8")) or {}
    goals = data.get("goals") or []
    return [g for g in goals if isinstance(g, dict) and g.get("id")]


def _base_entry(g: dict) -> dict:
    business = str(g.get("business") or "other").lower()
    if business not in config.VALID_BUSINESSES:
        business = "other"
    due = g.get("due")
    if isinstance(due, date):
        due = due.isoformat()
    return {
        "id": str(g["id"]),
        "title": str(g.get("title") or g["id"]),
        "business": business,
        "target": g.get("target"),
        "current": None,
        "unit": str(g.get("unit") or ""),
        "due": str(due) if due else None,
        "status": "unknown",
        "note": EVENING_NOTE,
    }


def _status_for(current: float | None, target) -> str:
    try:
        t = float(target)
    except (TypeError, ValueError):
        return "unknown"
    if current is None:
        return "unknown"
    if current >= t:
        return "on_track"
    if current >= t * 0.9:
        return "at_risk"
    return "behind"


def _computes() -> dict[str, tuple[Callable[[], float | None], str]]:
    from . import domos

    def conversion() -> float | None:
        return domos.waitlist_conversion_pct(domos.waitlist_rows(),
                                             config.quarter_start(config.today_local()))

    def min_gp() -> float | None:
        gps = domos.active_gp_pcts(domos.wip_rows())
        return min(gps) if gps else None

    return {
        "atwc_revenue_month": (domos.atwc_revenue_month, "Billed this month so far."),
        "atwc_sessions_week": (lambda: float(domos.atwc_sessions_week()), "Sessions this week so far."),
        "atwc_waitlist_conversion": (conversion, "Won over decided, entered this quarter."),
        "qca_sold_month": (lambda: domos.sales_month()["sold_amount"], "Sold this month so far."),
        "qca_collections_month": (domos.collections_month, "Collected this month so far."),
        "qca_min_gp_active": (min_gp, "Lowest gross profit among Scheduled/In Progress jobs."),
    }


def collect() -> list[dict]:
    """Return the schema's goals list. Never raises for one goal."""
    out: list[dict] = []
    goals = _load_yaml()
    if not goals:
        return out
    computes = _computes()
    targets_cache: dict | None = None
    from . import domos

    for g in goals:
        entry = _base_entry(g)
        if str(g.get("metric") or "").lower() != "domos":
            out.append(entry)
            continue
        source = g.get("source") or {}
        name = str(source.get("compute") or "").strip()
        if name not in computes:
            entry["note"] = f"Compute '{name}' is not one the collector knows. {EVENING_NOTE}"
            out.append(entry)
            continue
        fn, note = computes[name]
        try:
            if isinstance(entry["target"], str) and "." in entry["target"]:
                if targets_cache is None:
                    targets_cache = domos.targets()
                resolved = domos.target_path(entry["target"], targets_cache)
                if resolved is None:
                    entry["note"] = f"Target path {entry['target']} not found in DOM OS. {EVENING_NOTE}"
                    out.append(entry)
                    continue
                entry["target"] = resolved
            current = fn()
            entry["current"] = current
            entry["status"] = _status_for(current, entry["target"])
            entry["note"] = note if current is not None else f"No matching records yet. {EVENING_NOTE}"
        except config.NotConfigured as exc:
            entry["note"] = f"DOM OS not configured ({exc}). {EVENING_NOTE}"
        except Exception as exc:  # noqa: BLE001, one goal must not sink the rest
            entry["note"] = f"DOM OS read failed ({exc.__class__.__name__}). {EVENING_NOTE}"
        out.append(entry)
    return out
