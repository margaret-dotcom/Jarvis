"""Mirror the DOM OS knowledge base into the brain.

Usage:
    python -m brain.domos_sync              pull every active entry, write the mirror, rebuild the index
    python -m brain.domos_sync --dry-run    show what would change, write nothing

DOM OS (Margaret's own app, Supabase Postgres behind it) has a knowledge_entries
table of about 200 SOPs and notes. This module copies every active entry into
brain/library/domos/<business>/<slug>.md as a read-only note with frontmatter,
so brain.search finds DOM OS knowledge next to everything else in the brain.

Rules the mirror keeps:
- It owns brain/library/domos/ and nothing else. It never touches another area.
- It overwrites its own files freely; the copy in DOM OS is the original.
- A mirror file whose entry no longer comes back active is deleted.
- README.md in the mirror folder is left alone.
- Without SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env it prints one
  line and exits 0, so a cron sweep carries on.
- The key is never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

from collectors.config import env

from . import ingest
from .ingest import LIBRARY, dump_frontmatter, make_summary, slugify, split_frontmatter

MIRROR_DIR = LIBRARY / "domos"
SOURCE_NAME = "domos"
PAGE_SIZE = 500
SELECT = "id,business,category,title,content,source,updated_at"
BUSINESSES = ("atwc", "qca", "both")
TIMEOUT = 30


# ---------------------------------------------------------------- fetch

def fetch_entries() -> list[dict]:
    """Every active knowledge entry, newest first, paged 500 at a time.

    Uses Supabase's REST endpoint with Range headers. requests is imported
    here so the module loads (and --help works) without it.
    """
    import requests

    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    endpoint = f"{url}/rest/v1/knowledge_entries"
    params = {"select": SELECT, "active": "eq.true", "order": "updated_at.desc"}
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

    entries: list[dict] = []
    start = 0
    while True:
        page_headers = dict(headers, Range=f"{start}-{start + PAGE_SIZE - 1}")
        resp = requests.get(endpoint, params=params, headers=page_headers, timeout=TIMEOUT)
        if resp.status_code not in (200, 206):
            body = resp.text[:200].replace("\n", " ")
            raise RuntimeError(f"Supabase answered HTTP {resp.status_code}: {body}")
        page = resp.json()
        if not isinstance(page, list):
            raise RuntimeError("Supabase answered with something that is not a list")
        entries.extend(e for e in page if isinstance(e, dict))
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return entries


# ---------------------------------------------------------------- shaping

def business_dir(value) -> str:
    b = str(value or "").strip().lower()
    return b if b in BUSINESSES else "both"


def added_date(updated_at) -> dt.date:
    """The date part of updated_at, today when it is missing or unreadable."""
    text = str(updated_at or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return dt.date.today()


def note_path(entry: dict) -> Path:
    title = str(entry.get("title") or "").strip() or f"entry-{entry.get('id')}"
    slug = slugify(title)
    return MIRROR_DIR / business_dir(entry.get("business")) / f"{slug}.md"


def render_note(entry: dict) -> str:
    title = str(entry.get("title") or "").strip() or f"DOM OS entry {entry.get('id')}"
    category = str(entry.get("category") or "").strip()
    content = str(entry.get("content") or "").strip()
    business = business_dir(entry.get("business"))
    tags = [SOURCE_NAME]
    cat_slug = slugify(category, 40) if category else ""
    if cat_slug and cat_slug != "note":
        tags.append(cat_slug)
    meta = {
        "title": title,
        "added": added_date(entry.get("updated_at")),
        "source": SOURCE_NAME,
        "external_id": str(entry.get("id")),
        "kind": "sop",
        "area": SOURCE_NAME,
        "business": business,
        "category": category,
        "tags": tags,
        "status": "mirrored",
        "summary": make_summary(content),
    }
    body = content + "\n" if content else "(This entry has no content in DOM OS.)\n"
    return dump_frontmatter(meta) + "\n" + body


# ---------------------------------------------------------------- mirror files

def existing_mirror() -> dict[str, Path]:
    """{external_id: path} for every mirror note already on disk."""
    found: dict[str, Path] = {}
    if not MIRROR_DIR.exists():
        return found
    for md in sorted(MIRROR_DIR.rglob("*.md")):
        if md.name == "README.md":
            continue
        meta, _ = split_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        ext_id = str(meta.get("external_id") or "").strip()
        if meta.get("source") == SOURCE_NAME and ext_id:
            found[ext_id] = md
        else:
            # Something that is not ours sits in the mirror folder. Leave it and say so.
            print(f"domos_sync: leaving {rel(md)} alone, it is not a mirror note", file=sys.stderr)
    return found


def rel(path: Path) -> str:
    return ingest.rel(path)


def _inside_mirror(path: Path) -> bool:
    try:
        path.resolve().relative_to(MIRROR_DIR.resolve())
        return True
    except ValueError:
        return False


def sync(entries: list[dict], dry_run: bool = False) -> dict[str, int]:
    """Write one note per entry, drop stale ones. Returns counts."""
    counts = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "skipped": 0}
    before = existing_mirror()
    seen_paths: set[Path] = set()
    keep_ids: set[str] = set()

    for entry in entries:
        ext_id = str(entry.get("id") or "").strip()
        if not ext_id:
            counts["skipped"] += 1
            continue
        target = note_path(entry)
        # Two entries can share a title. Keep both with the id as a tiebreaker.
        if target in seen_paths:
            target = target.with_name(f"{target.stem}-{slugify(ext_id, 12)}{target.suffix}")
        seen_paths.add(target)
        keep_ids.add(ext_id)
        if not _inside_mirror(target):
            counts["skipped"] += 1
            continue

        text = render_note(entry)
        old = before.get(ext_id)
        if old is not None and old != target and old.exists() and old not in seen_paths:
            # Title changed, so the file moves. Drop the old name; the new one is written below.
            if not dry_run:
                old.unlink()
        if target.exists() and target.read_text(encoding="utf-8", errors="replace") == text:
            counts["unchanged"] += 1
            continue
        if old is None and not target.exists():
            counts["added"] += 1
        else:
            counts["updated"] += 1
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    for ext_id, path in before.items():
        if ext_id in keep_ids or not path.exists():
            continue
        if not _inside_mirror(path):
            continue
        counts["removed"] += 1
        if not dry_run:
            path.unlink()

    if not dry_run:
        _drop_empty_dirs()
    return counts


def _drop_empty_dirs() -> None:
    if not MIRROR_DIR.exists():
        return
    for d in sorted((p for p in MIRROR_DIR.rglob("*") if p.is_dir()), reverse=True):
        try:
            d.rmdir()  # only succeeds when empty
        except OSError:
            pass


# ---------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m brain.domos_sync", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not env("SUPABASE_URL") or not env("SUPABASE_SERVICE_ROLE_KEY"):
        print("domos_sync: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set in .env, DOM OS mirror skipped.")
        return 0

    try:
        entries = fetch_entries()
    except Exception as exc:  # network, auth, bad JSON: say so in one line, no key
        print(f"domos_sync: could not read knowledge_entries: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    counts = sync(entries, dry_run=args.dry_run)
    prefix = "would be " if args.dry_run else ""
    print(f"DOM OS mirror: {len(entries)} active entries, "
          f"{counts['added']} {prefix}added, {counts['updated']} {prefix}updated, "
          f"{counts['unchanged']} unchanged, {counts['removed']} {prefix}removed"
          + (f", {counts['skipped']} skipped" if counts["skipped"] else "")
          + f" in {rel(MIRROR_DIR)}.")

    if args.dry_run:
        return 0
    index_path, count = ingest.rebuild_index()
    print(f"Index rebuilt: {rel(index_path)} ({count} notes).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(0)
