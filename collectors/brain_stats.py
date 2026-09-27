"""Brain collector: counts for the second brain folder.

collect() returns the schema's brain block: how many items live in
brain/library, how many were added yesterday (frontmatter `added`), how many
files wait in brain/inbox, and the five most recent library items.

Frontmatter is YAML between --- lines at the top of each markdown file, read
with python-frontmatter. A file with no frontmatter still counts as an item;
its title is the filename and its added date is the file's modified time.
README.md and INDEX.md files are folder notes and are not counted.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import frontmatter
from dateutil import parser as dateparser

from . import config

RECENT_COUNT = 5

# Folder notes, not items. INDEX.md is written by the ingest step.
SKIP_NAMES = {"readme.md", "index.md"}


def _added_date(meta: dict, path: Path) -> date | None:
    value = meta.get("added") or meta.get("date") or meta.get("created")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return dateparser.parse(str(value)).date()
        except (ValueError, TypeError, OverflowError):
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=config.tzinfo()).date()
    except OSError:
        return None


def _business_for(path: Path, meta: dict) -> str:
    value = str(meta.get("business") or "").lower()
    if value in config.VALID_BUSINESSES:
        return value
    try:
        area = path.relative_to(config.BRAIN_DIR / "library").parts[0]
    except (ValueError, IndexError):
        return "other"
    return area if area in config.VALID_BUSINESSES else "other"


def _read(path: Path) -> dict:
    try:
        post = frontmatter.load(str(path))
        meta = dict(post.metadata or {})
    except Exception:  # noqa: BLE001, a bad file is still an item
        meta = {}
    added = _added_date(meta, path)
    title = str(meta.get("title") or path.stem.replace("-", " ").replace("_", " "))
    return {
        "title": title,
        "path": str(path.relative_to(config.REPO_ROOT)),
        "business": _business_for(path, meta),
        "added": added.isoformat() if added else "",
    }


def collect(today: date | None = None) -> dict:
    today = today or config.today_local()
    yesterday = (today - timedelta(days=1)).isoformat()
    library = config.BRAIN_DIR / "library"
    inbox = config.BRAIN_DIR / "inbox"

    items = [_read(p) for p in sorted(library.rglob("*.md"))] if library.exists() else []
    items = [i for i in items
             if not Path(i["path"]).name.startswith(".")
             and Path(i["path"]).name.lower() not in SKIP_NAMES]
    pending = 0
    if inbox.exists():
        pending = sum(1 for p in inbox.iterdir()
                      if p.is_file() and not p.name.startswith("."))

    recent = sorted(items, key=lambda i: i["added"], reverse=True)[:RECENT_COUNT]
    return {
        "total_items": len(items),
        "ingested_yesterday": sum(1 for i in items if i["added"] == yesterday),
        "inbox_pending": pending,
        "recent": recent,
    }
