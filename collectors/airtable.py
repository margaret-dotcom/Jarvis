"""Airtable collector: ATWC waitlist aggregates and the QCA WIP report.

Two public functions, both using the REST API with offset pagination and
AIRTABLE_TOKEN. With an empty token they raise NotConfigured.

atwc_waitlist()
    Aggregate counts only. The request asks Airtable for three fields (Date
    Entered, Date Scheduled or Removed, Status) so no patient field ever
    reaches this process, and nothing per record is returned.

qca_wip()
    Job-level roofing numbers. Field names in that table carry stray spaces
    (for example " JOB # " and " CONTRACT AMOUNT "), so every key is stripped
    before matching and both padded and unpadded names work.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

import requests
from dateutil import parser as dateparser

from . import config

API = "https://api.airtable.com/v0"
PAGE_SIZE = 100

# The ids from .env.example, used when .env leaves them blank.
DEFAULT_ATWC_BASE = "appKWU9ggxyVYvs2g"
DEFAULT_ATWC_WAITLIST_TABLE = "tblnwneQqlChWi8ks"
DEFAULT_QCA_BASE = "appkWXOY6hM0pxT76"
DEFAULT_QCA_WIP_TABLE = "tblX1WhjyrukQulfC"

WAITLIST_FIELDS = ("Date Entered", "Date Scheduled or Removed", "Status")
WAITLIST_STATUSES = {"scheduled", "removed", "waitlist"}
GP_THRESHOLD = 40.0

ACTIVE_STATUSES = {"scheduled/in progress"}
OPEN_STATUSES = {"not started", "scheduled/in progress", "on hold", "invoiced-completed"}


def _token() -> str:
    token = config.env("AIRTABLE_TOKEN")
    if not token:
        raise config.NotConfigured("AIRTABLE_TOKEN is empty in .env")
    return token


def _records(base: str, table: str, fields: tuple[str, ...] | None = None,
             formula: str | None = None) -> list[dict]:
    """Every record's fields dict, keys stripped of surrounding spaces."""
    headers = {"Authorization": f"Bearer {_token()}"}
    if not base or not table:
        raise config.NotConfigured("Airtable base or table id is empty in .env")
    params: list[tuple[str, str]] = [("pageSize", str(PAGE_SIZE))]
    for f in fields or ():
        params.append(("fields[]", f))
    if formula:
        params.append(("filterByFormula", formula))
    out: list[dict] = []
    offset = None
    while True:
        page = list(params)
        if offset:
            page.append(("offset", offset))
        resp = requests.get(f"{API}/{base}/{table}", headers=headers, params=page,
                            timeout=config.HTTP_TIMEOUT)
        if resp.status_code in (401, 403):
            raise RuntimeError(f"Airtable refused the request ({resp.status_code}). "
                               "Check AIRTABLE_TOKEN and its base access.")
        if resp.status_code == 422 and fields:
            # A field name changed in the base. Retry without the field filter so
            # the caller can still match by stripped name.
            return _records(base, table, None, formula)
        resp.raise_for_status()
        body = resp.json()
        for rec in body.get("records") or []:
            raw = rec.get("fields") or {}
            out.append({str(k).strip(): v for k, v in raw.items()})
        offset = body.get("offset")
        if not offset:
            return out


def _as_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return dateparser.isoparse(str(value)).date()
    except (ValueError, TypeError):
        try:
            return dateparser.parse(str(value)).date()
        except (ValueError, TypeError, OverflowError):
            return None


def _as_number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _as_pct(value) -> float | None:
    """Gross profit as a percent. Airtable percent fields arrive as fractions
    (0.42), typed numbers as 42. Anything with magnitude 1.5 or less is read
    as a fraction and scaled."""
    n = _as_number(value)
    if n is None:
        return None
    return round(n * 100, 1) if abs(n) <= 1.5 else round(n, 1)


def _same_month(d: date | None, ref: date) -> bool:
    return bool(d) and d.year == ref.year and d.month == ref.month


# --------------------------------------------------------------------------
# ATWC waitlist
# --------------------------------------------------------------------------


def waitlist_rows() -> list[dict]:
    """Stripped-key rows with only the three non-identifying fields."""
    base = config.env("AIRTABLE_ATWC_BASE", DEFAULT_ATWC_BASE)
    table = config.env("AIRTABLE_ATWC_WAITLIST_TABLE", DEFAULT_ATWC_WAITLIST_TABLE)
    rows = _records(base, table, WAITLIST_FIELDS)
    # Belt and braces: if the field filter was dropped on retry, keep only the
    # three fields we are allowed to look at.
    return [{k: v for k, v in r.items() if k in WAITLIST_FIELDS} for r in rows]


def _status(row: dict) -> str:
    return str(row.get("Status") or "").strip().lower()


def waitlist_conversion_pct(rows: list[dict], since: date) -> float | None:
    """scheduled / (scheduled + removed) for records entered on or after since."""
    scheduled = removed = 0
    for r in rows:
        entered = _as_date(r.get("Date Entered"))
        if not entered or entered < since:
            continue
        st = _status(r)
        if st == "scheduled":
            scheduled += 1
        elif st == "removed":
            removed += 1
    resolved = scheduled + removed
    return round(100.0 * scheduled / resolved, 1) if resolved else None


def atwc_waitlist(today: date | None = None) -> dict:
    """The schema's businesses.atwc.waitlist block. Counts only.

    pending                  Status is Waitlist
    added_this_month         Date Entered in the current month
    scheduled_this_month     Status Scheduled, resolved date in the current month
    removed_this_month       Status Removed, resolved date in the current month
    conversion_pct           scheduled / (scheduled + removed) for records
                             entered this calendar quarter
    median_days_to_schedule  median of (scheduled date minus entered date) for
                             records scheduled in the last 90 days
    """
    today = today or config.today_local()
    rows = waitlist_rows()
    pending = added = scheduled = removed = 0
    days_to_schedule: list[int] = []
    cutoff = today - timedelta(days=90)

    for r in rows:
        st = _status(r)
        entered = _as_date(r.get("Date Entered"))
        resolved = _as_date(r.get("Date Scheduled or Removed"))
        if st == "waitlist":
            pending += 1
        if _same_month(entered, today):
            added += 1
        if st == "scheduled" and _same_month(resolved, today):
            scheduled += 1
        if st == "removed" and _same_month(resolved, today):
            removed += 1
        if st == "scheduled" and entered and resolved and resolved >= cutoff:
            delta = (resolved - entered).days
            if delta >= 0:
                days_to_schedule.append(delta)

    return {
        "pending": pending,
        "added_this_month": added,
        "scheduled_this_month": scheduled,
        "removed_this_month": removed,
        "conversion_pct": waitlist_conversion_pct(rows, config.quarter_start(today)),
        "median_days_to_schedule": (
            float(statistics.median(days_to_schedule)) if days_to_schedule else None),
    }


# --------------------------------------------------------------------------
# QCA WIP report
# --------------------------------------------------------------------------


def wip_rows() -> list[dict]:
    """Stripped-key rows from the WIP Report table."""
    base = config.env("AIRTABLE_QCA_BASE", DEFAULT_QCA_BASE)
    table = config.env("AIRTABLE_QCA_WIP_TABLE", DEFAULT_QCA_WIP_TABLE)
    return _records(base, table)


def _job_status(row: dict) -> str:
    return str(row.get("Job Status") or "").strip()


def active_gp_pcts(rows: list[dict]) -> list[float]:
    """TOTAL GROSS PROFIT (percent) for every Scheduled/In Progress job."""
    out: list[float] = []
    for r in rows:
        if _job_status(r).lower() in ACTIVE_STATUSES:
            gp = _as_pct(r.get("TOTAL GROSS PROFIT"))
            if gp is not None:
                out.append(gp)
    return out


def qca_wip() -> dict:
    """The schema's businesses.qca.wip block.

    active_jobs           Job Status is Scheduled/In Progress
    not_started           Job Status is Not Started
    on_hold               Job Status is On Hold
    total_contract_value  sum of CONTRACT AMOUNT over open jobs (not Paid In
                          Full-Closed, not Cancelled)
    uncollected           sum over open jobs of INVOICED TO DATE minus CASH
                          COLLECTED TO DATE, never below zero per job
    avg_gp_pct            mean TOTAL GROSS PROFIT across active jobs
    jobs_below_40_gp      open jobs under the 40 percent threshold
    """
    rows = wip_rows()
    active = not_started = on_hold = 0
    contract_total = 0.0
    uncollected = 0.0
    below: list[dict] = []

    for r in rows:
        status = _job_status(r)
        low = status.lower()
        if low in ACTIVE_STATUSES:
            active += 1
        elif low == "not started":
            not_started += 1
        elif low == "on hold":
            on_hold += 1
        if low not in OPEN_STATUSES:
            continue
        contract_total += _as_number(r.get("CONTRACT AMOUNT")) or 0.0
        invoiced = _as_number(r.get("INVOICED TO DATE")) or 0.0
        collected = _as_number(r.get("CASH COLLECTED TO DATE")) or 0.0
        uncollected += max(0.0, invoiced - collected)
        gp = _as_pct(r.get("TOTAL GROSS PROFIT"))
        if gp is not None and gp < GP_THRESHOLD:
            job = r.get("JOB #")
            below.append({
                "job": job if isinstance(job, int) else str(job or ""),
                "customer": str(r.get("CUSTOMER") or "").strip(),
                "gp_pct": gp,
                "status": status,
            })

    gps = active_gp_pcts(rows)
    below.sort(key=lambda j: j["gp_pct"])
    return {
        "active_jobs": active,
        "not_started": not_started,
        "on_hold": on_hold,
        "total_contract_value": round(contract_total, 2),
        "uncollected": round(uncollected, 2),
        "avg_gp_pct": round(sum(gps) / len(gps), 1) if gps else None,
        "jobs_below_40_gp": below,
    }
