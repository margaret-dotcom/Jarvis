"""Gmail collector: what needs a reply, and what Margaret is waiting on.

collect(account) returns a dict with three keys that slot into the schema's
email block: needs_reply, waiting_on and counts.

Rules
  needs_reply  Threads in INBOX from the last 3 days whose latest message is
               not from her and does not look automated. Automated means a
               List-Unsubscribe header, a sender like noreply or notifications,
               or Gmail's promotions category.
  waiting_on   Threads from the last 10 days where her sent message is the
               latest one and it is at least 2 days old. At most 10.

Only metadata is requested (From, Subject, Date, List-Unsubscribe headers and
Gmail's own snippet). Message bodies are never downloaded.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from . import config
from . import google_auth

NEEDS_REPLY_DAYS = 3
WAITING_ON_DAYS = 10
WAITING_ON_MIN_AGE_HOURS = 48
WAITING_ON_MAX = 10
MAX_THREADS_PER_QUERY = 60

METADATA_HEADERS = ["From", "Subject", "Date", "List-Unsubscribe"]

AUTOMATED_SENDER_RE = re.compile(
    r"(no-?reply|do-?not-?reply|notifications?|mailer-daemon|postmaster|"
    r"newsletter|alerts?|updates|digest|bounce)", re.IGNORECASE)


def thread_link(email: str, thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/?authuser={email}#all/{thread_id}"


def _headers(message: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in (message.get("payload") or {}).get("headers") or []:
        name = (h.get("name") or "").lower()
        if name and name not in out:
            out[name] = h.get("value") or ""
    return out


def _message_time(message: dict, headers: dict[str, str]) -> datetime:
    """Aware UTC datetime of a message, from internalDate, else the Date header."""
    raw = message.get("internalDate")
    if raw:
        try:
            return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
        except (ValueError, OverflowError):
            pass
    try:
        dt = parsedate_to_datetime(headers.get("date", ""))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc)


def _is_from_me(message: dict, headers: dict[str, str], my_email: str) -> bool:
    if "SENT" in (message.get("labelIds") or []):
        return True
    _name, addr = parseaddr(headers.get("from", ""))
    return addr.strip().lower() == my_email


def _looks_automated(message: dict, headers: dict[str, str]) -> bool:
    if headers.get("list-unsubscribe"):
        return True
    _name, addr = parseaddr(headers.get("from", ""))
    local = addr.split("@", 1)[0] if addr else headers.get("from", "")
    if AUTOMATED_SENDER_RE.search(local):
        return True
    labels = message.get("labelIds") or []
    return "CATEGORY_PROMOTIONS" in labels


def _latest_message(thread: dict) -> tuple[dict, dict, datetime] | None:
    best = None
    for msg in thread.get("messages") or []:
        if "DRAFT" in (msg.get("labelIds") or []):
            continue
        headers = _headers(msg)
        when = _message_time(msg, headers)
        if best is None or when > best[2]:
            best = (msg, headers, when)
    return best


def _thread_entry(account: dict, thread: dict, msg: dict, headers: dict,
                  when: datetime, now: datetime) -> dict:
    from_header = headers.get("from", "")
    name, addr = parseaddr(from_header)
    return {
        "id": thread.get("id", ""),
        "account": account["email"],
        "business": account["business"],
        "from": (name or addr or from_header).strip(),
        "subject": (headers.get("subject") or "(no subject)").strip(),
        "received": when.astimezone(config.tzinfo()).isoformat(timespec="minutes"),
        "age_hours": round((now - when).total_seconds() / 3600, 1),
        "snippet": html.unescape(msg.get("snippet") or thread.get("snippet") or "").strip(),
        "link": thread_link(account["email"], thread.get("id", "")),
    }


def _list_threads(service, query: str, limit: int) -> list[dict]:
    threads: list[dict] = []
    page_token = None
    while len(threads) < limit:
        resp = service.users().threads().list(
            userId="me", q=query, maxResults=min(50, limit - len(threads)),
            pageToken=page_token).execute()
        threads.extend(resp.get("threads") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return threads[:limit]


def _get_thread(service, thread_id: str) -> dict:
    return service.users().threads().get(
        userId="me", id=thread_id, format="metadata",
        metadataHeaders=METADATA_HEADERS).execute()


def _unread_count(service) -> int:
    label = service.users().labels().get(userId="me", id="INBOX").execute()
    return int(label.get("threadsUnread") or 0)


def collect(account: dict) -> dict:
    """Collect needs_reply, waiting_on and counts for one Google account.

    Raises config.NotConfigured when the account has no token.
    """
    my_email = account["email"]
    creds = google_auth.get_credentials(my_email, interactive=False)
    service = google_auth.build_service("gmail", "v1", creds)
    now = datetime.now(tz=timezone.utc)

    needs_reply: list[dict] = []
    seen: set[str] = set()
    for stub in _list_threads(service, f"in:inbox newer_than:{NEEDS_REPLY_DAYS}d",
                              MAX_THREADS_PER_QUERY):
        thread = _get_thread(service, stub["id"])
        latest = _latest_message(thread)
        if not latest:
            continue
        msg, headers, when = latest
        if _is_from_me(msg, headers, my_email) or _looks_automated(msg, headers):
            continue
        if now - when > timedelta(days=NEEDS_REPLY_DAYS):
            continue
        needs_reply.append(_thread_entry(account, thread, msg, headers, when, now))
        seen.add(thread["id"])
    needs_reply.sort(key=lambda t: t["age_hours"])

    waiting_on: list[dict] = []
    for stub in _list_threads(service, f"in:sent newer_than:{WAITING_ON_DAYS}d",
                              MAX_THREADS_PER_QUERY):
        if stub["id"] in seen:
            continue
        thread = _get_thread(service, stub["id"])
        latest = _latest_message(thread)
        if not latest:
            continue
        msg, headers, when = latest
        if not _is_from_me(msg, headers, my_email):
            continue
        age_hours = (now - when).total_seconds() / 3600
        if age_hours < WAITING_ON_MIN_AGE_HOURS:
            continue
        waiting_on.append(_thread_entry(account, thread, msg, headers, when, now))
        seen.add(thread["id"])
    waiting_on.sort(key=lambda t: -t["age_hours"])
    waiting_on = waiting_on[:WAITING_ON_MAX]

    try:
        unread = _unread_count(service)
    except Exception:  # noqa: BLE001, the count is nice to have, not required
        unread = 0

    return {
        "needs_reply": needs_reply,
        "waiting_on": waiting_on,
        "counts": {
            "unread": unread,
            "needs_reply": len(needs_reply),
            "waiting_on": len(waiting_on),
        },
    }
