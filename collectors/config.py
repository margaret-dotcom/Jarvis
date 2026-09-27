"""Shared settings for every collector.

Reads .env from the repo root (a small parser, no extra dependency), reads
collectors/accounts.yaml, and answers "what time is it for Margaret" through
now_local(). Nothing in here talks to the network.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECTORS_DIR = REPO_ROOT / "collectors"
TOKENS_DIR = COLLECTORS_DIR / "tokens"
CREDENTIALS_DIR = COLLECTORS_DIR / "credentials"
DATA_DIR = REPO_ROOT / "dashboard" / "data"
SCHEMA_PATH = DATA_DIR / "schema.json"
TODAY_PATH = DATA_DIR / "today.json"
HISTORY_DIR = DATA_DIR / "history"
GOALS_PATH = REPO_ROOT / "goals" / "goals.yaml"
BRAIN_DIR = REPO_ROOT / "brain"

DEFAULT_TZ = "America/Chicago"
OWNER_NAME = "Margaret"

# One timeout for every HTTP call the collectors make, in seconds.
HTTP_TIMEOUT = 30

VALID_BUSINESSES = ("atwc", "qca", "personal", "other")


class NotConfigured(Exception):
    """Raised when a collector has no token or account to work with.

    build_today records this as a source with status not_configured instead of
    an error, so the dashboard can say "not set up yet" rather than "broken".
    """


def warn(message: str) -> None:
    """Print a warning to stderr. Never pass secrets to this."""
    print(f"[collectors] {message}", file=sys.stderr)


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

_ENV_LOADED = False


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ.

    Values already set in the real environment win. Lines starting with # and
    blank lines are skipped. Surrounding single or double quotes are removed.
    Safe to call more than once.
    """
    global _ENV_LOADED
    if _ENV_LOADED and path is None:
        return
    env_path = path or (REPO_ROOT / ".env")
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
    _ENV_LOADED = True


def env(key: str, default: str = "") -> str:
    """Return an environment value with .env loaded first. Empty string if unset."""
    load_env()
    return os.environ.get(key, default) or default


# Older name kept for modules written against it (collectors/youtube.py).
get_env = env


def resolve_path(value: str) -> Path:
    """Turn a path from .env into an absolute path, relative to the repo root."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


def tz_name() -> str:
    """The IANA timezone name in use (JARVIS_TZ, else America/Chicago)."""
    return env("JARVIS_TZ", DEFAULT_TZ)


def tzinfo() -> ZoneInfo:
    """The ZoneInfo for tz_name(). Falls back to America/Chicago with a warning."""
    name = tz_name()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        warn(f"JARVIS_TZ={name!r} is not a known timezone, using {DEFAULT_TZ}")
        return ZoneInfo(DEFAULT_TZ)


def now_local() -> datetime:
    """Current time as an aware datetime in Margaret's timezone."""
    return datetime.now(tz=tzinfo())


def today_local() -> date:
    """Today's date in Margaret's timezone."""
    return now_local().date()


def start_of_day(d: date) -> datetime:
    """Midnight at the start of the given date, aware, local timezone."""
    return datetime(d.year, d.month, d.day, tzinfo=tzinfo())


def quarter_start(d: date) -> date:
    """First day of the calendar quarter that contains d."""
    first_month = 3 * ((d.month - 1) // 3) + 1
    return date(d.year, first_month, 1)


def yesterday_local() -> date:
    return today_local() - timedelta(days=1)


# --------------------------------------------------------------------------
# accounts.yaml
# --------------------------------------------------------------------------


def accounts_path() -> Path:
    return COLLECTORS_DIR / "accounts.yaml"


def load_accounts(strict: bool = False) -> list[dict]:
    """Return the Google accounts to collect for.

    Reads collectors/accounts.yaml. When that file is missing, falls back to
    accounts.yaml.example and prints a warning, because the example lists the
    one known address and lets a fresh checkout run end to end. Pass
    strict=True to skip the fallback and get an empty list instead.

    Each account is a dict with email, business, label and calendars. Missing
    fields are filled with sensible defaults so the collectors never crash on a
    half-written entry.
    """
    path = accounts_path()
    if not path.exists():
        example = COLLECTORS_DIR / "accounts.yaml.example"
        if strict or not example.exists():
            return []
        warn("collectors/accounts.yaml not found, using accounts.yaml.example. "
             "Copy it to accounts.yaml and edit it.")
        path = example
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        warn(f"could not parse {path.name}: {exc}")
        return []
    raw_accounts = data.get("accounts") or []
    accounts: list[dict] = []
    for entry in raw_accounts:
        if not isinstance(entry, dict) or not entry.get("email"):
            continue
        email = str(entry["email"]).strip().lower()
        business = str(entry.get("business") or "other").strip().lower()
        if business not in VALID_BUSINESSES:
            warn(f"account {email}: business {business!r} is not one of "
                 f"{', '.join(VALID_BUSINESSES)}, using 'other'")
            business = "other"
        calendars = entry.get("calendars") or ["primary"]
        if isinstance(calendars, str):
            calendars = [calendars]
        accounts.append({
            "email": email,
            "business": business,
            "label": str(entry.get("label") or email),
            "calendars": [str(c) for c in calendars],
        })
    return accounts


def account_summary(account: dict) -> dict:
    """The shape the schema wants under calendar.accounts and email.accounts."""
    return {
        "email": account["email"],
        "business": account["business"],
        "label": account["label"],
    }


def token_path(email: str) -> Path:
    """Where the OAuth token for one Google account lives (git-ignored)."""
    safe = email.strip().lower().replace("/", "_")
    return TOKENS_DIR / f"{safe}.token.json"


def business_from_text(text: str, default: str = "other") -> str:
    """Guess a business tag from free text such as a project name.

    Used for Asana projects, which carry no business field of their own. The
    keyword list is short on purpose. Anything unmatched is tagged default.
    """
    lowered = (text or "").lower()
    if any(k in lowered for k in ("atwc", "therapy", "wellness", "clinic")):
        return "atwc"
    if any(k in lowered for k in ("qca", "roof")):
        return "qca"
    if "personal" in lowered:
        return "personal"
    return default
