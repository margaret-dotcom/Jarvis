"""DOM OS collector: reads Margaret's system of record through Supabase.

DOM OS (Decision Organization Management OS) is the Next.js app at
domoshq.com backed by a Supabase Postgres database. Row level security is on
for every table, so the collectors use the service role key from .env and
take responsibility for reading only what the dashboard needs. Every query
here names its columns. Patient identifying columns on atwc_waitlist and
atwc_billing_sessions are never selected; those tables are only ever counted.

Usage as a module: build_today.py calls tasks(), inbox(), atwc(), qca().
Usage from a Hermes skill, one JSON document per named read-only query:

    python -m collectors.domos --query open_tasks
    python -m collectors.domos --query knowledge_search --q "end of week report"
    python -m collectors.domos --list

Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DOMOS_OWNER_EMAIL, DOMOS_URL.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

import requests

from . import config

PAGE = 1000
ACTIVE_WIP = "Scheduled/In Progress"
GP_FLOOR_PCT = 40.0
# Waitlist statuses as DOM OS spells them.
WL_PENDING = ("Waitlist",)
WL_WON = ("Scheduled", "In Treatment")
WL_LOST = ("Never Scheduled", "Discontinued")


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------


def _settings() -> tuple[str, str]:
    url = config.env("SUPABASE_URL").rstrip("/")
    key = config.env("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise config.NotConfigured(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are needed in .env "
            "(Supabase dashboard, Project Settings, API)."
        )
    return url, key


def owner_email() -> str:
    return config.env("DOMOS_OWNER_EMAIL", "margaret@mytherapywellness.com").lower()


def app_url() -> str:
    return config.env("DOMOS_URL", "https://domoshq.com").rstrip("/")


def fetch(table: str, select: str, filters: dict[str, str] | None = None,
          order: str | None = None, limit: int | None = None) -> list[dict]:
    """GET rows from a table through PostgREST, paging until done.

    filters use PostgREST operators, e.g. {"status": "eq.open", "due_on": "lte.2026-09-27"}.
    """
    url, key = _settings()
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}
    params: dict[str, str] = {"select": select}
    params.update(filters or {})
    if order:
        params["order"] = order
    rows: list[dict] = []
    start = 0
    while True:
        stop = start + PAGE - 1
        if limit is not None:
            stop = min(stop, limit - 1)
        resp = requests.get(
            f"{url}/rest/v1/{table}", params=params, timeout=config.HTTP_TIMEOUT,
            headers={**headers, "Range-Unit": "items", "Range": f"{start}-{stop}"},
        )
        if resp.status_code == 416:  # range past the end
            break
        resp.raise_for_status()
        batch = resp.json()
        rows.extend(batch)
        if len(batch) < (stop - start + 1) or (limit is not None and len(rows) >= limit):
            break
        start = stop + 1
    return rows


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _d(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Tasks and inbox (the owner's own items)
# --------------------------------------------------------------------------


def _task_item(row: dict) -> dict:
    project = row.get("section") or ""
    return {
        "id": str(row["id"]),
        "title": row.get("title") or "",
        "project": project,
        "business": row.get("entity_id") or "other",
        "due": row.get("due_on"),
        "link": f"{app_url()}/tasks",
    }


def tasks() -> dict:
    """The schema's tasks block from dom_tasks assigned to the owner."""
    today = config.today_local()
    yesterday = today - timedelta(days=1)
    me = owner_email()
    cols = "id,title,entity_id,due_on,status,priority,section,completed_at"
    open_rows = fetch("dom_tasks", cols,
                      {"assignee_email": f"eq.{me}", "status": "eq.open"}, order="due_on.asc")
    done_rows = fetch("dom_tasks", cols,
                      {"assignee_email": f"eq.{me}", "status": "eq.done",
                       "completed_at": f"gte.{yesterday.isoformat()}T00:00:00"})
    due_today = [_task_item(r) for r in open_rows if _d(r.get("due_on")) == today]
    overdue = [_task_item(r) for r in open_rows if (_d(r.get("due_on")) or today) < today]
    completed = [_task_item(r) for r in done_rows
                 if (_d(r.get("completed_at")) or today) == yesterday]
    return {"source": "domos", "due_today": due_today, "overdue": overdue,
            "completed_yesterday": completed}


def inbox() -> dict:
    """Open DOM OS notifications, EOW reports waiting on the owner, her DOM events today."""
    today = config.today_local()
    me = owner_email()
    notes = fetch("dom_notifications", "id,kind,title,body,href,due_date,created_at",
                  {"recipient_email": f"eq.{me}", "status": "eq.open"},
                  order="created_at.desc", limit=50)
    eows = fetch("eow_reports", "id,employee_name,week_end,submitted_at",
                 {"responded_at": "is.null",
                  "submitted_at": f"gte.{(today - timedelta(days=28)).isoformat()}"},
                 order="submitted_at.desc")
    events = fetch("dom_events", "id,title,starts_at,ends_at,all_day,kind",
                   {"owner_email": f"ilike.{me}", "done": "eq.false",
                    "starts_at": f"gte.{today.isoformat()}T00:00:00",
                    "ends_at": f"lt.{(today + timedelta(days=1)).isoformat()}T00:00:00"},
                   order="starts_at.asc")
    return {
        "notifications": [
            {"id": str(n["id"]), "kind": n.get("kind") or "general", "title": n.get("title") or "",
             "body": (n.get("body") or "")[:200], "href": n.get("href") or "",
             "due_date": n.get("due_date"), "created_at": n.get("created_at")}
            for n in notes],
        "eow_pending": [
            {"employee": e.get("employee_name") or "", "week_end": e.get("week_end"),
             "submitted_at": e.get("submitted_at")} for e in eows],
        "events_today": [
            {"id": str(e["id"]), "title": e.get("title") or "", "start": e.get("starts_at"),
             "end": e.get("ends_at"), "all_day": bool(e.get("all_day")), "kind": e.get("kind") or ""}
            for e in events],
    }


# --------------------------------------------------------------------------
# ATWC (counts only, never a name)
# --------------------------------------------------------------------------


def waitlist_rows() -> list[dict]:
    return fetch("atwc_waitlist", "status,date_entered,status_changed_on",
                 {"date_entered": f"gte.{(config.today_local() - timedelta(days=400)).isoformat()}"})


def waitlist_summary(rows: list[dict] | None = None) -> dict:
    rows = waitlist_rows() if rows is None else rows
    today = config.today_local()
    month_start = today.replace(day=1)
    this_month = [r for r in rows if (_d(r.get("date_entered")) or date.min) >= month_start]
    won_month = [r for r in this_month if r.get("status") in WL_WON]
    lost_month = [r for r in this_month if r.get("status") in WL_LOST]
    days = []
    for r in rows:
        if r.get("status") in WL_WON:
            a, b = _d(r.get("date_entered")), _d(r.get("status_changed_on"))
            if a and b and a >= today - timedelta(days=90) and b >= a:
                days.append((b - a).days)
    decided = len(won_month) + len(lost_month)
    return {
        "pending": sum(1 for r in rows if r.get("status") in WL_PENDING),
        "added_this_month": len(this_month),
        "scheduled_this_month": len(won_month),
        "removed_this_month": len(lost_month),
        "conversion_pct": round(100 * len(won_month) / decided, 1) if decided else None,
        "median_days_to_schedule": float(statistics.median(days)) if days else None,
    }


def waitlist_conversion_pct(rows: list[dict], since: date) -> float | None:
    """Won over decided among records entered on or after `since`."""
    entered = [r for r in rows if (_d(r.get("date_entered")) or date.min) >= since]
    won = sum(1 for r in entered if r.get("status") in WL_WON)
    lost = sum(1 for r in entered if r.get("status") in WL_LOST)
    return round(100 * won / (won + lost), 1) if (won + lost) else None


def atwc() -> dict:
    return {"waitlist": waitlist_summary()}


def atwc_revenue_month() -> float:
    start = config.today_local().replace(day=1)
    rows = fetch("atwc_revenue_lines", "total_fee", {"service_date": f"gte.{start.isoformat()}"})
    return round(sum(_num(r.get("total_fee")) for r in rows), 2)


def atwc_sessions_week() -> int:
    today = config.today_local()
    monday = today - timedelta(days=today.weekday())
    rows = fetch("atwc_billing_sessions", "date_of_service",
                 {"date_of_service": f"gte.{monday.isoformat()}"})
    return len(rows)


# --------------------------------------------------------------------------
# QCA
# --------------------------------------------------------------------------


def wip_rows() -> list[dict]:
    return fetch("qca_wip", "job_number,customer,job_status,deposit,contract_amount,"
                 "change_orders,actual_cost_to_date,invoiced_to_date,cash_collected_to_date")


def gp_pct(row: dict) -> float | None:
    total = _num(row.get("contract_amount")) + _num(row.get("change_orders"))
    if total <= 0:
        return None
    return round(100 * (total - _num(row.get("actual_cost_to_date"))) / total, 1)


def active_gp_pcts(rows: list[dict]) -> list[float]:
    return [g for g in (gp_pct(r) for r in rows if r.get("job_status") == ACTIVE_WIP) if g is not None]


def wip_summary(rows: list[dict] | None = None) -> dict:
    rows = wip_rows() if rows is None else rows
    active = [r for r in rows if r.get("job_status") == ACTIVE_WIP]
    open_jobs = [r for r in rows if r.get("job_status") in (ACTIVE_WIP, "Not Started", "On Hold",
                                                             "Invoiced-Completed")]
    gps = active_gp_pcts(rows)
    low = []
    for r in rows:
        # Jobs still in motion. Finished jobs with thin margins belong in the
        # finance review, not the daily brief.
        if r.get("job_status") in (ACTIVE_WIP, "Not Started", "On Hold"):
            g = gp_pct(r)
            if g is not None and g < GP_FLOOR_PCT:
                low.append({"job": r.get("job_number") or "", "customer": r.get("customer") or "",
                            "gp_pct": g, "status": r.get("job_status") or ""})
    low.sort(key=lambda x: x["gp_pct"])
    return {
        "active_jobs": len(active),
        "not_started": sum(1 for r in rows if r.get("job_status") == "Not Started"),
        "on_hold": sum(1 for r in rows if r.get("job_status") == "On Hold"),
        "total_contract_value": round(sum(_num(r.get("contract_amount")) + _num(r.get("change_orders"))
                                          for r in open_jobs), 2),
        "uncollected": round(sum(_num(r.get("invoiced_to_date")) - _num(r.get("cash_collected_to_date"))
                                 for r in open_jobs), 2),
        "avg_gp_pct": round(sum(gps) / len(gps), 1) if gps else None,
        "jobs_below_40_gp": low[:10],
    }


def pipeline_summary() -> dict:
    rows = fetch("qca_pipeline_jobs", "milestone,estimate_total,lead_source")
    return {
        "leads": sum(1 for r in rows if r.get("milestone") == "Lead"),
        "prospects": sum(1 for r in rows if r.get("milestone") == "Prospect"),
        "estimate_total": round(sum(_num(r.get("estimate_total")) for r in rows
                                    if r.get("milestone") == "Prospect"), 2),
    }


def sales_month() -> dict:
    start = config.today_local().replace(day=1)
    rows = fetch("sales_daily_logs", "touches,inspections,estimates_written,jobs_sold,sold_amount",
                 {"log_date": f"gte.{start.isoformat()}"})
    return {
        "touches": int(sum(_num(r.get("touches")) for r in rows)),
        "inspections": int(sum(_num(r.get("inspections")) for r in rows)),
        "estimates": int(sum(_num(r.get("estimates_written")) for r in rows)),
        "sold": int(sum(_num(r.get("jobs_sold")) for r in rows)),
        "sold_amount": round(sum(_num(r.get("sold_amount")) for r in rows), 2),
    }


def collections_month() -> float:
    start = config.today_local().replace(day=1)
    rows = fetch("qca_collections", "amount", {"payment_date": f"gte.{start.isoformat()}"})
    return round(sum(_num(r.get("amount")) for r in rows), 2)


def profit_alerts(days: int = 14) -> list[dict]:
    since = config.today_local() - timedelta(days=days)
    return fetch("profit_alerts", "job_key,kind,detail,flagged_on",
                 {"flagged_on": f"gte.{since.isoformat()}"}, order="flagged_on.desc")


def qca() -> dict:
    rows = wip_rows()
    return {
        "wip": wip_summary(rows),
        "pipeline": pipeline_summary(),
        "sales_month": sales_month(),
        "profit_alerts_recent": len(profit_alerts()),
    }


# --------------------------------------------------------------------------
# Targets and knowledge
# --------------------------------------------------------------------------


def targets() -> dict:
    """The live targets DOM OS holds: atwc_targets.data and qca-sales-targets."""
    out: dict = {}
    atwc_rows = fetch("atwc_targets", "data", limit=1)
    if atwc_rows:
        out["atwc_targets"] = atwc_rows[0].get("data") or {}
    qca_rows = fetch("shared_settings", "value", {"key": "eq.qca-sales-targets"}, limit=1)
    if qca_rows:
        out["qca_sales_targets"] = qca_rows[0].get("value") or {}
    return out


def target_path(path: str, cache: dict | None = None) -> float | None:
    """Resolve 'atwc_targets.revenue.monthly' against targets(). None if missing."""
    data = targets() if cache is None else cache
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def knowledge_search(q: str, limit: int = 10) -> list[dict]:
    """Case-insensitive search over DOM OS knowledge entries (title or content)."""
    pattern = f"*{q}*"
    rows = fetch("knowledge_entries", "id,business,category,title,content,updated_at",
                 {"active": "eq.true", "or": f"(title.ilike.{pattern},content.ilike.{pattern})"},
                 order="updated_at.desc", limit=limit)
    for r in rows:
        r["content"] = (r.get("content") or "")[:1200]
    return rows


# --------------------------------------------------------------------------
# Named read-only queries for the agents
# --------------------------------------------------------------------------

QUERIES: dict[str, Callable[..., Any]] = {
    "open_tasks": lambda: [_task_item(r) for r in fetch(
        "dom_tasks", "id,title,entity_id,due_on,status,priority,section,completed_at",
        {"assignee_email": f"eq.{owner_email()}", "status": "eq.open"}, order="due_on.asc")],
    "overdue_tasks": lambda: tasks()["overdue"],
    "notifications": lambda: inbox()["notifications"],
    "eow_pending": lambda: inbox()["eow_pending"],
    "waitlist_summary": waitlist_summary,
    "wip_summary": wip_summary,
    "wip_low_gp": lambda: wip_summary()["jobs_below_40_gp"],
    "pipeline_summary": pipeline_summary,
    "sales_month": sales_month,
    "collections_month": collections_month,
    "profit_alerts": profit_alerts,
    "targets": targets,
    "knowledge_search": knowledge_search,
}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only DOM OS queries for Jarvis.")
    parser.add_argument("--query", choices=sorted(QUERIES), help="named query to run")
    parser.add_argument("--q", default="", help="search text for knowledge_search")
    parser.add_argument("--list", action="store_true", help="list query names")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.list or not args.query:
        print("\n".join(sorted(QUERIES)))
        return 0
    try:
        fn = QUERIES[args.query]
        result = fn(args.q) if args.query == "knowledge_search" else fn()
    except config.NotConfigured as exc:
        print(json.dumps({"error": "not_configured", "detail": str(exc)}))
        return 1
    except requests.RequestException as exc:
        print(json.dumps({"error": "request_failed", "detail": exc.__class__.__name__}))
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
