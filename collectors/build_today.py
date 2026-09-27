"""Build dashboard/data/today.json from every collector.

    python -m collectors.build_today                 collect and write
    python -m collectors.build_today --only gmail,gcal
    python -m collectors.build_today --dry-run       print JSON, write nothing
    python -m collectors.build_today --sample        fictional data, no APIs

Each collector runs inside its own try/except and lands in the `sources`
list as ok, error (with a short reason) or not_configured. A failing source
never stops the others. The Hermes jobs write `content`, `yesterday`,
`headline` and `day_shape` into today.json later in the day, so those blocks
are carried forward rather than erased: content is dropped once scanned_at is
older than 48 hours, and headline and day_shape are kept only while the
file's date is still today.

Exit codes: 0 when anything was collected (even if other sources failed),
2 when no source produced data.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timedelta

from dateutil import parser as dateparser

from . import config

ALL_COLLECTORS = ["gmail", "gcal", "asana", "airtable", "goals", "brain"]
CONTENT_MAX_AGE_HOURS = 48


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _short_error(exc: BaseException) -> str:
    """One line about an exception, with anything token-shaped left out."""
    text = f"{exc.__class__.__name__}: {exc}".strip()
    text = " ".join(text.split())
    for key in ("ASANA_TOKEN", "AIRTABLE_TOKEN", "YOUTUBE_API_KEY"):
        value = config.env(key)
        if value:
            text = text.replace(value, "[redacted]")
    return text[:200]


class Sources:
    """Collects the `sources` entries and knows whether anything worked."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, name: str, status: str, detail: str = "") -> None:
        entry = {"name": name, "status": status,
                 "fetched_at": config.now_local().isoformat(timespec="seconds")}
        if detail:
            entry["detail"] = detail
        self.entries.append(entry)

    def run(self, name: str, fn, *args):
        """Call fn(*args), record the outcome, return the result or None."""
        try:
            result = fn(*args)
        except config.NotConfigured as exc:
            self.record(name, "not_configured", str(exc)[:200])
            return None
        except ImportError as exc:
            self.record(name, "not_configured",
                        f"missing library ({exc.name}). Run pip install -r requirements.txt")
            return None
        except Exception as exc:  # noqa: BLE001, one source must not sink the build
            self.record(name, "error", _short_error(exc))
            if os.environ.get("JARVIS_DEBUG"):
                traceback.print_exc()
            return None
        self.record(name, "ok")
        return result

    @property
    def any_ok(self) -> bool:
        return any(e["status"] == "ok" for e in self.entries)


def read_existing() -> dict:
    if not config.TODAY_PATH.exists():
        return {}
    try:
        return json.loads(config.TODAY_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError) as exc:
        config.warn(f"existing today.json unreadable, starting fresh: {exc.__class__.__name__}")
        return {}


def carry_forward(existing: dict, today_str: str, now: datetime) -> dict:
    """The blocks written by Hermes jobs that this run must not erase."""
    kept: dict = {}
    content = existing.get("content")
    if isinstance(content, dict):
        scanned = content.get("scanned_at")
        keep = True
        if scanned:
            try:
                when = dateparser.isoparse(scanned)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=config.tzinfo())
                keep = (now - when) <= timedelta(hours=CONTENT_MAX_AGE_HOURS)
            except (ValueError, TypeError):
                keep = True
        if keep:
            kept["content"] = content
    if isinstance(existing.get("yesterday"), dict):
        kept["yesterday"] = existing["yesterday"]
    same_day = existing.get("date") == today_str
    kept["headline"] = existing.get("headline", "") if same_day else ""
    kept["day_shape"] = existing.get("day_shape", "") if same_day else ""
    if kept["day_shape"] not in ("heavy", "normal", "open", ""):
        kept["day_shape"] = ""
    return kept


def skeleton(now: datetime) -> dict:
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "timezone": config.tz_name(),
        "owner": config.OWNER_NAME,
        "headline": "",
        "day_shape": "",
        "goals": [],
        "calendar": {"accounts": [], "events": []},
        "email": {"accounts": [],
                  "counts": {"unread": 0, "needs_reply": 0, "waiting_on": 0},
                  "needs_reply": [], "waiting_on": []},
        "tasks": {"source": "", "due_today": [], "overdue": [], "completed_yesterday": []},
        "businesses": {},
        "brain": {"total_items": 0, "ingested_yesterday": 0, "inbox_pending": 0, "recent": []},
        "sources": [],
    }


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------


def collect_all(only: list[str]) -> tuple[dict, Sources]:
    now = config.now_local()
    doc = skeleton(now)
    sources = Sources()
    accounts = config.load_accounts() if ("gmail" in only or "gcal" in only) else []

    if "gmail" in only:
        from . import gmail
        if not accounts:
            sources.record("gmail", "not_configured", "no accounts in collectors/accounts.yaml")
        for account in accounts:
            result = sources.run(f"gmail:{account['email']}", gmail.collect, account)
            doc["email"]["accounts"].append(config.account_summary(account))
            if result:
                doc["email"]["needs_reply"].extend(result["needs_reply"])
                doc["email"]["waiting_on"].extend(result["waiting_on"])
                for k, v in result["counts"].items():
                    doc["email"]["counts"][k] = doc["email"]["counts"].get(k, 0) + v
        doc["email"]["needs_reply"].sort(key=lambda t: t.get("age_hours", 0))
        doc["email"]["waiting_on"].sort(key=lambda t: -t.get("age_hours", 0))

    if "gcal" in only:
        from . import gcal
        if not accounts:
            sources.record("gcal", "not_configured", "no accounts in collectors/accounts.yaml")
        for account in accounts:
            result = sources.run(f"gcal:{account['email']}", gcal.collect, account)
            doc["calendar"]["accounts"].append(config.account_summary(account))
            if result:
                doc["calendar"]["events"].extend(result)
        gcal.mark_conflicts(doc["calendar"]["events"])
        doc["calendar"]["events"].sort(
            key=lambda e: (e["day"] != "today", not e.get("all_day"), e["start"]))

    if "asana" in only:
        from . import asana
        result = sources.run("asana", asana.collect)
        if result:
            doc["tasks"] = result

    if "airtable" in only:
        from . import airtable
        waitlist = sources.run("airtable_atwc", airtable.atwc_waitlist)
        if waitlist is not None:
            doc["businesses"].setdefault("atwc", {})["waitlist"] = waitlist
        wip = sources.run("airtable_qca", airtable.qca_wip)
        if wip is not None:
            doc["businesses"].setdefault("qca", {})["wip"] = wip

    if "goals" in only:
        from . import goals
        result = sources.run("goals", goals.collect)
        if result is not None:
            doc["goals"] = result

    if "brain" in only:
        from . import brain_stats
        result = sources.run("brain", brain_stats.collect)
        if result is not None:
            doc["brain"] = result

    doc["sources"] = sources.entries
    return doc, sources


# --------------------------------------------------------------------------
# Sample data (fictional, for design and testing)
# --------------------------------------------------------------------------


def sample_document() -> tuple[dict, Sources]:
    """A fully populated day with made-up but plausible data. No API calls.

    Every name, number and link here is fictional. The example domains
    (example.com, example.org) make that visible in the dashboard.
    """
    now = config.now_local()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    tz = config.tzinfo()

    def at(d, h, m=0):
        return datetime(d.year, d.month, d.day, h, m, tzinfo=tz).isoformat(timespec="minutes")

    def ago(hours):
        return (now - timedelta(hours=hours)).isoformat(timespec="minutes")

    atwc = "margaret@mytherapywellness.com"
    qca = "margaret@qca.example.com"
    accounts = [
        {"email": atwc, "business": "atwc", "label": "ATWC inbox"},
        {"email": qca, "business": "qca", "label": "QCA inbox"},
    ]
    doc = skeleton(now)
    doc["headline"] = ("Heavy morning at the clinic, two roofing calls after lunch, "
                       "and the Hartwell estimate still needs a reply.")
    doc["day_shape"] = "heavy"
    doc["goals"] = [
        {"id": "atwc-q4-waitlist-conversion",
         "title": "Convert 70 percent of the ATWC waitlist to scheduled evaluations",
         "business": "atwc", "target": 70, "current": 63.2, "unit": "pct",
         "due": f"{today.year}-12-31", "status": "at_risk",
         "note": "Scheduled over scheduled plus removed, entered this quarter."},
        {"id": "qca-q4-gp",
         "title": "Keep every active QCA job above 40 percent gross profit",
         "business": "qca", "target": 40, "current": 34.5, "unit": "pct",
         "due": f"{today.year}-12-31", "status": "behind",
         "note": "Lowest gross profit among Scheduled/In Progress jobs."},
        {"id": "personal-strength",
         "title": "Three strength sessions a week", "business": "personal",
         "target": 3, "current": None, "unit": "sessions", "due": None,
         "status": "unknown", "note": "Status is filled in by the evening summary."},
    ]
    doc["calendar"] = {
        "accounts": accounts,
        "events": [
            {"id": "ev-sample-1", "account": atwc, "business": "atwc",
             "title": "Team huddle", "start": at(today, 8, 30), "end": at(today, 8, 45),
             "all_day": False, "location": "Clinic front desk", "attendees_count": 6,
             "organizer_is_me": True, "response": "owner",
             "link": "https://calendar.google.com/calendar/u/0/r/day", "conflict": False,
             "day": "today"},
            {"id": "ev-sample-2", "account": atwc, "business": "atwc",
             "title": "Feeding evaluation (new family)", "start": at(today, 9, 0),
             "end": at(today, 10, 30), "all_day": False, "location": "Room 2",
             "attendees_count": 2, "organizer_is_me": False, "response": "accepted",
             "link": "https://calendar.google.com/calendar/u/0/r/day", "conflict": True,
             "day": "today"},
            {"id": "ev-sample-3", "account": qca, "business": "qca",
             "title": "Call: Redbird Insurance adjuster, Pine St job",
             "start": at(today, 10, 0), "end": at(today, 10, 30), "all_day": False,
             "location": "Phone", "attendees_count": 2, "organizer_is_me": False,
             "response": "tentative", "link": "https://calendar.google.com/calendar/u/0/r/day",
             "conflict": True, "day": "today"},
            {"id": "ev-sample-4", "account": qca, "business": "qca",
             "title": "Site visit: 41 Pine St re-roof", "start": at(today, 13, 30),
             "end": at(today, 15, 0), "all_day": False, "location": "41 Pine St",
             "attendees_count": 3, "organizer_is_me": True, "response": "owner",
             "link": "https://calendar.google.com/calendar/u/0/r/day", "conflict": False,
             "day": "today"},
            {"id": "ev-sample-5", "account": atwc, "business": "atwc",
             "title": "Payroll due", "start": tomorrow.isoformat(),
             "end": (tomorrow + timedelta(days=1)).isoformat(), "all_day": True,
             "location": "", "attendees_count": 0, "organizer_is_me": True,
             "response": "owner", "link": "https://calendar.google.com/calendar/u/0/r/day",
             "conflict": False, "day": "tomorrow"},
            {"id": "ev-sample-6", "account": atwc, "business": "atwc",
             "title": "Podcast recording with Dr. Okafor", "start": at(tomorrow, 11, 0),
             "end": at(tomorrow, 12, 0), "all_day": False, "location": "Zoom",
             "attendees_count": 2, "organizer_is_me": False, "response": "needsAction",
             "link": "https://calendar.google.com/calendar/u/0/r/day", "conflict": False,
             "day": "tomorrow"},
        ],
    }
    doc["email"] = {
        "accounts": accounts,
        "counts": {"unread": 27, "needs_reply": 3, "waiting_on": 2},
        "needs_reply": [
            {"id": "thr-sample-a1", "account": atwc, "business": "atwc",
             "from": "Priya Nandakumar", "subject": "Re: OT coverage for Thursday afternoons",
             "received": ago(5), "age_hours": 5.0,
             "snippet": "I can cover 1 to 4 but not the 4:30 slot. Does that work for the schedule?",
             "link": f"https://mail.google.com/mail/u/?authuser={atwc}#all/thr-sample-a1"},
            {"id": "thr-sample-a2", "account": qca, "business": "qca",
             "from": "Dale Hartwell", "subject": "Estimate for 118 Cedar Ln, questions",
             "received": ago(19), "age_hours": 19.0,
             "snippet": "Thanks for coming out. Two questions before we sign: does the price include the skylight flashing, and",
             "link": f"https://mail.google.com/mail/u/?authuser={qca}#all/thr-sample-a2"},
            {"id": "thr-sample-a3", "account": atwc, "business": "atwc",
             "from": "Sam Reyes (Lakeside Pediatrics)", "subject": "Referral pathway for tongue tie",
             "received": ago(41), "age_hours": 41.0,
             "snippet": "Our front desk asked how they should send families over. Is there a form or should they call?",
             "link": f"https://mail.google.com/mail/u/?authuser={atwc}#all/thr-sample-a3"},
        ],
        "waiting_on": [
            {"id": "thr-sample-w1", "account": qca, "business": "qca",
             "from": "Margaret Stoch", "subject": "Permit status for 9 Orchard Way",
             "received": ago(70), "age_hours": 70.0,
             "snippet": "Checking in on the permit. The crew is ready for the 3rd if we have it.",
             "link": f"https://mail.google.com/mail/u/?authuser={qca}#all/thr-sample-w1"},
            {"id": "thr-sample-w2", "account": atwc, "business": "atwc",
             "from": "Margaret Stoch", "subject": "Speaker slot at the parent night",
             "received": ago(120), "age_hours": 120.0,
             "snippet": "Happy to speak for 20 minutes on feeding red flags. Which date works?",
             "link": f"https://mail.google.com/mail/u/?authuser={atwc}#all/thr-sample-w2"},
        ],
    }
    doc["tasks"] = {
        "source": "asana",
        "due_today": [
            {"id": "task-sample-1", "title": "Approve September payroll", "project": "ATWC Admin",
             "business": "atwc", "due": today.isoformat(),
             "link": "https://app.asana.com/0/0/task-sample-1"},
            {"id": "task-sample-2", "title": "Send Hartwell revised estimate", "project": "QCA Sales",
             "business": "qca", "due": today.isoformat(),
             "link": "https://app.asana.com/0/0/task-sample-2"},
        ],
        "overdue": [
            {"id": "task-sample-3", "title": "Renew clinic liability policy", "project": "ATWC Admin",
             "business": "atwc", "due": (today - timedelta(days=3)).isoformat(),
             "link": "https://app.asana.com/0/0/task-sample-3"},
        ],
        "completed_yesterday": [
            {"id": "task-sample-4", "title": "Post the October class schedule", "project": "ATWC Marketing",
             "business": "atwc", "due": yesterday.isoformat(),
             "link": "https://app.asana.com/0/0/task-sample-4"},
            {"id": "task-sample-5", "title": "Order shingles for Pine St", "project": "QCA Jobs",
             "business": "qca", "due": yesterday.isoformat(),
             "link": "https://app.asana.com/0/0/task-sample-5"},
        ],
    }
    doc["businesses"] = {
        "atwc": {"waitlist": {
            "pending": 18, "added_this_month": 11, "scheduled_this_month": 7,
            "removed_this_month": 4, "conversion_pct": 63.2, "median_days_to_schedule": 12.0}},
        "qca": {"wip": {
            "active_jobs": 6, "not_started": 3, "on_hold": 1,
            "total_contract_value": 412750.0, "uncollected": 58900.0, "avg_gp_pct": 43.8,
            "jobs_below_40_gp": [
                {"job": "2026-041", "customer": "Hartwell", "gp_pct": 34.5,
                 "status": "Scheduled/In Progress"},
                {"job": "2026-037", "customer": "Okonkwo", "gp_pct": 38.1,
                 "status": "On Hold"},
            ]}},
    }
    doc["brain"] = {
        "total_items": 142, "ingested_yesterday": 3, "inbox_pending": 2,
        "recent": [
            {"title": "Notes: adjuster call scripts", "path": "brain/library/qca/adjuster-call-scripts.md",
             "business": "qca", "added": yesterday.isoformat()},
            {"title": "Feeding therapy intake checklist", "path": "brain/library/atwc/feeding-intake-checklist.md",
             "business": "atwc", "added": yesterday.isoformat()},
            {"title": "Decision: no weekend crews in Q4", "path": "brain/library/decisions/no-weekend-crews-q4.md",
             "business": "qca", "added": yesterday.isoformat()},
            {"title": "Parent night talk outline", "path": "brain/library/marketing/parent-night-outline.md",
             "business": "atwc", "added": (today - timedelta(days=2)).isoformat()},
            {"title": "Dr. Okafor, pediatric dentist", "path": "brain/library/people/dr-okafor.md",
             "business": "atwc", "added": (today - timedelta(days=4)).isoformat()},
        ],
    }
    doc["content"] = {
        "scanned_at": (now - timedelta(hours=8)).isoformat(timespec="minutes"),
        "recommendations": [
            {"title": "How a pediatric OT explains sensory diets to parents in 90 seconds",
             "platform": "youtube", "url": "https://www.youtube.com/watch?v=sample0001",
             "creator": "Example Therapy Channel",
             "why": "Parents ask this every week and a short clear answer builds trust fast.",
             "angle": "Record your own 90 second version at the clinic with a real prop.",
             "audience": "atwc", "job": "attract", "score": 8.4},
            {"title": "Roof inspection walkthrough, what the adjuster looks for",
             "platform": "youtube", "url": "https://www.youtube.com/watch?v=sample0002",
             "creator": "Example Roofing Co",
             "why": "Homeowners fear the adjuster visit and this removes the mystery.",
             "angle": "Film a Pine St style walkthrough and name the three things you check first.",
             "audience": "qca", "job": "activate", "score": 7.9},
        ],
    }
    doc["yesterday"] = {
        "summary": "Two evaluations, the class schedule went out, and the Pine St materials are ordered.",
        "accomplished": ["Posted the October class schedule", "Ordered shingles for Pine St"],
        "slipped": ["Renew clinic liability policy"],
        "carry_forward": ["Reply to Hartwell on the Cedar Ln estimate"],
    }
    sources = Sources()
    for name in (f"gmail:{atwc}", f"gmail:{qca}", f"gcal:{atwc}", f"gcal:{qca}",
                 "asana", "airtable_atwc", "airtable_qca", "goals", "brain"):
        sources.record(name, "ok", "sample data, nothing was fetched")
    doc["sources"] = sources.entries
    return doc, sources


# --------------------------------------------------------------------------
# Validate and write
# --------------------------------------------------------------------------


def validate(doc: dict) -> list[str]:
    """Return schema violations as short strings. Empty list means valid."""
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is not installed, validation skipped"]
    try:
        schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read schema: {exc.__class__.__name__}"]
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema, format_checker=validator_cls.FORMAT_CHECKER)
    problems = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        problems.append(f"{where}: {err.message[:160]}")
    return problems


def write_atomic(doc: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    tmp = config.TODAY_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, config.TODAY_PATH)
    shutil.copyfile(config.TODAY_PATH, config.HISTORY_DIR / f"{doc['date']}.json")


def build(only: list[str], sample: bool = False) -> tuple[dict, Sources]:
    if sample:
        doc, sources = sample_document()
    else:
        doc, sources = collect_all(only)
    existing = read_existing()
    kept = carry_forward(existing, doc["date"], config.now_local())
    for key in ("content", "yesterday"):
        if key in kept and key not in doc:
            doc[key] = kept[key]
    if not sample:
        doc["headline"] = kept["headline"]
        doc["day_shape"] = kept["day_shape"]
    return doc, sources


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m collectors.build_today",
        description="Collect the day's facts and write dashboard/data/today.json.")
    parser.add_argument("--only", default=",".join(ALL_COLLECTORS),
                        help="comma list of collectors to run: " + ", ".join(ALL_COLLECTORS))
    parser.add_argument("--dry-run", action="store_true",
                        help="print the JSON to stdout instead of writing it")
    parser.add_argument("--sample", action="store_true",
                        help="write fictional, fully populated data without calling any API")
    args = parser.parse_args(argv)
    args.only = [c.strip().lower() for c in args.only.split(",") if c.strip()]
    unknown = [c for c in args.only if c not in ALL_COLLECTORS]
    if unknown:
        parser.error(f"unknown collector(s): {', '.join(unknown)}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    doc, sources = build(args.only, sample=args.sample)

    problems = validate(doc)
    for p in problems:
        config.warn(f"schema warning: {p}")

    if args.dry_run:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        write_atomic(doc)
        print(f"wrote {config.TODAY_PATH.relative_to(config.REPO_ROOT)} "
              f"and history/{doc['date']}.json")

    for entry in sources.entries:
        detail = f" ({entry['detail']})" if entry.get("detail") else ""
        config.warn(f"{entry['name']}: {entry['status']}{detail}")

    if not sources.any_ok:
        config.warn("nothing was collected")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
