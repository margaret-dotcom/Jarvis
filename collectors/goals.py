"""Goals collector: reads goals/goals.yaml and reports status where it can.

Every goal starts as status unknown with a note that the evening summary
fills it in. Two Airtable computes are supported here because they are
cheap and exact. Matching is by keyword on the goal's source.compute string:

  waitlist conversion   compute mentions both "scheduled" and "removed"
                        (for example "scheduled / (scheduled + removed) ...").
                        current = scheduled / (scheduled + removed) in percent
                        for records entered this calendar quarter.
  minimum GP            compute mentions "gross profit" or "gp" together with
                        "min" (for example "minimum TOTAL GROSS PROFIT across
                        jobs with status Scheduled/In Progress").
                        current = the lowest TOTAL GROSS PROFIT among
                        Scheduled/In Progress jobs.

Status for a supported compute: on_track when current meets the target,
at_risk when current is within ten percent of the target, behind otherwise.
Anything else stays unknown.
"""

from __future__ import annotations

from datetime import date

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


def compute_kind(compute: str) -> str | None:
    """Which supported compute a goal asks for, by keyword. None if unsupported."""
    c = (compute or "").lower()
    if "scheduled" in c and "removed" in c:
        return "waitlist_conversion"
    if ("gross profit" in c or "gp" in c) and "min" in c:
        return "min_gp"
    return None


def collect(waitlist_rows: list[dict] | None = None,
            wip_rows: list[dict] | None = None) -> list[dict]:
    """Return the schema's goals list.

    Airtable rows can be passed in to avoid a second fetch; otherwise each
    supported compute fetches its own table. An Airtable failure leaves that
    goal at unknown with the reason in its note. Never raises for one goal.
    """
    from . import airtable

    today = config.today_local()
    out: list[dict] = []
    for g in _load_yaml():
        entry = _base_entry(g)
        if str(g.get("metric") or "").lower() != "airtable":
            out.append(entry)
            continue
        source = g.get("source") or {}
        kind = compute_kind(str(source.get("compute") or ""))
        if kind is None:
            entry["note"] = ("This compute is not one of the two the collector "
                             "knows. " + EVENING_NOTE)
            out.append(entry)
            continue
        try:
            if kind == "waitlist_conversion":
                rows = waitlist_rows if waitlist_rows is not None else airtable.waitlist_rows()
                current = airtable.waitlist_conversion_pct(rows, config.quarter_start(today))
                entry["note"] = "Scheduled over scheduled plus removed, entered this quarter."
            else:
                rows = wip_rows if wip_rows is not None else airtable.wip_rows()
                gps = airtable.active_gp_pcts(rows)
                current = min(gps) if gps else None
                entry["note"] = "Lowest gross profit among Scheduled/In Progress jobs."
            entry["current"] = current
            entry["status"] = _status_for(current, entry["target"])
            if current is None:
                entry["note"] = "No matching records yet. " + EVENING_NOTE
        except config.NotConfigured as exc:
            entry["note"] = f"Airtable not configured ({exc}). {EVENING_NOTE}"
        except Exception as exc:  # noqa: BLE001, one goal must not sink the rest
            entry["note"] = f"Airtable read failed ({exc.__class__.__name__}). {EVENING_NOTE}"
        out.append(entry)
    return out
