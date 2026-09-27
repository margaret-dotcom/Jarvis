"""Google Calendar collector: today and tomorrow, across every account.

collect(account) returns a list of events for the window from today 00:00
through the end of tomorrow in JARVIS_TZ, for each calendar id the account
lists in accounts.yaml. Declined events are skipped.

mark_conflicts(events) is separate because a conflict can be between two
accounts. build_today calls it once on the merged list.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from dateutil import parser as dateparser

from . import config
from . import google_auth

MAX_EVENTS_PER_CALENDAR = 250


def window(today: date | None = None) -> tuple[datetime, datetime]:
    """Start of today through start of the day after tomorrow, local, aware."""
    today = today or config.today_local()
    start = config.start_of_day(today)
    end = config.start_of_day(today + timedelta(days=2))
    return start, end


def _parse_when(value: dict) -> tuple[datetime | date, bool]:
    """Return (datetime or date, all_day) from a Calendar start/end object."""
    if "date" in value:
        return date.fromisoformat(value["date"]), True
    dt = dateparser.isoparse(value["dateTime"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=config.tzinfo())
    return dt.astimezone(config.tzinfo()), False


def _my_attendance(event: dict, my_email: str) -> dict | None:
    for att in event.get("attendees") or []:
        if att.get("self") or (att.get("email") or "").lower() == my_email:
            return att
    return None


def normalize_event(raw: dict, account: dict, today: date) -> dict | None:
    """Turn one API event into the schema's event shape. None means skip it."""
    if raw.get("status") == "cancelled":
        return None
    my_email = account["email"]
    me = _my_attendance(raw, my_email)
    if me and me.get("responseStatus") == "declined":
        return None

    start, all_day = _parse_when(raw.get("start") or {})
    end, _ = _parse_when(raw.get("end") or raw.get("start") or {})
    tomorrow = today + timedelta(days=1)

    if all_day:
        # Calendar all-day ends are exclusive: a one-day event ends the next date.
        start_date, end_date = start, end
        if start_date <= today < end_date:
            day = "today"
        elif start_date <= tomorrow < end_date:
            day = "tomorrow"
        else:
            return None
        start_str, end_str = start_date.isoformat(), end_date.isoformat()
    else:
        start_date = start.date()
        if start_date <= today and end.date() >= today and end > config.start_of_day(today):
            day = "today"
        elif start_date == tomorrow:
            day = "tomorrow"
        elif start_date < today:
            return None
        else:
            day = "tomorrow"
        start_str = start.isoformat(timespec="minutes")
        end_str = end.isoformat(timespec="minutes")

    organizer = raw.get("organizer") or {}
    organizer_is_me = bool(organizer.get("self")) or \
        (organizer.get("email") or "").lower() == my_email
    if me and me.get("responseStatus"):
        response = me["responseStatus"]
    elif organizer_is_me:
        response = "owner"
    else:
        response = "accepted"

    return {
        "id": raw.get("id", ""),
        "account": my_email,
        "business": account["business"],
        "title": (raw.get("summary") or "(no title)").strip(),
        "start": start_str,
        "end": end_str,
        "all_day": all_day,
        "location": (raw.get("location") or "").strip(),
        "attendees_count": len(raw.get("attendees") or []),
        "organizer_is_me": organizer_is_me,
        "response": response,
        "link": raw.get("htmlLink") or "",
        "conflict": False,
        "day": day,
    }


def collect(account: dict) -> list[dict]:
    """Collect today's and tomorrow's events for one account. Raises NotConfigured."""
    creds = google_auth.get_credentials(account["email"], interactive=False)
    service = google_auth.build_service("calendar", "v3", creds)
    today = config.today_local()
    start, end = window(today)

    events: list[dict] = []
    seen_ids: set[str] = set()
    for calendar_id in account.get("calendars") or ["primary"]:
        page_token = None
        while True:
            resp = service.events().list(
                calendarId=calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=MAX_EVENTS_PER_CALENDAR,
                pageToken=page_token,
            ).execute()
            for raw in resp.get("items") or []:
                ev = normalize_event(raw, account, today)
                if ev and ev["id"] not in seen_ids:
                    seen_ids.add(ev["id"])
                    events.append(ev)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    events.sort(key=lambda e: (e["day"] != "today", e["all_day"] is False, e["start"]))
    return events


def _interval(ev: dict) -> tuple[datetime, datetime] | None:
    if ev.get("all_day"):
        return None
    try:
        return dateparser.isoparse(ev["start"]), dateparser.isoparse(ev["end"])
    except (ValueError, KeyError, TypeError):
        return None


def mark_conflicts(events: list[dict]) -> list[dict]:
    """Set conflict=True on today's timed events that overlap any other one.

    Works across accounts. Two entries with the same title, start and end are
    treated as the same meeting seen from two inboxes, not as a conflict.
    """
    todays = [(ev, _interval(ev)) for ev in events if ev.get("day") == "today"]
    todays = [(ev, iv) for ev, iv in todays if iv]
    for ev, _ in todays:
        ev["conflict"] = False
    for i, (a, (a_start, a_end)) in enumerate(todays):
        for b, (b_start, b_end) in todays[i + 1:]:
            same_meeting = (a["title"] == b["title"] and a["start"] == b["start"]
                            and a["end"] == b["end"])
            if same_meeting:
                continue
            if a_start < b_end and b_start < a_end:
                a["conflict"] = True
                b["conflict"] = True
    return events
