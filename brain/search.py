"""Search: sqlite FTS5 full text index over the library.

Usage:
    python -m brain.search "roof permit"              ranked results
    python -m brain.search "intake" --area atwc       only one area
    python -m brain.search "reel hook" --limit 5
    python -m brain.search --rebuild                  reindex every note

The index lives at brain/.index.sqlite and is git-ignored. It is a cache.
Delete it any time; the next search rebuilds it from the markdown.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from .ingest import BRAIN_DIR, AREAS, iter_library_notes, rebuild_index

DB_FILE = BRAIN_DIR / ".index.sqlite"

SCHEMA = """
CREATE VIRTUAL TABLE notes USING fts5(
    title, tags, area, body,
    path UNINDEXED, added UNINDEXED, kind UNINDEXED, summary UNINDEXED,
    tokenize = 'porter unicode61'
);
"""


def build_fts(notes: list[dict] | None = None) -> int:
    """Drop and rebuild the index from the library. Returns the note count."""
    notes = iter_library_notes() if notes is None else notes
    tmp = DB_FILE.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        rows = []
        for n in notes:
            m = n["meta"]
            tags = m.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            rows.append((
                str(m.get("title", "")),
                " ".join(str(t) for t in tags),
                str(m.get("area", "")),
                n["body"],
                n["path"],
                str(m.get("updated") or m.get("added") or ""),
                str(m.get("kind", "note")),
                str(m.get("summary", "")),
            ))
        con.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    tmp.replace(DB_FILE)
    return len(notes)


def fts_query(user_query: str) -> str:
    """Quote every word so FTS5 syntax characters cannot break the query.
    Words get a trailing * so 'permit' also finds 'permits'."""
    words = re.findall(r"[\w'-]+", user_query)
    if not words:
        return '""'
    parts = []
    for w in words:
        w = w.replace('"', '""')
        parts.append(f'"{w}"*' if len(w) >= 3 else f'"{w}"')
    return " ".join(parts)


def search(query: str, area: str | None = None, limit: int = 10) -> list[dict]:
    if not DB_FILE.exists():
        build_fts()
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    sql = (
        "SELECT path, title, added, kind, area, "
        "snippet(notes, 3, '[', ']', ' ... ', 18) AS snip "
        "FROM notes WHERE notes MATCH ? "
    )
    params: list = [fts_query(query)]
    if area:
        sql += "AND area = ? "
        params.append(area)
    # title matters most, then tags, then area, then body
    sql += "ORDER BY bm25(notes, 6.0, 4.0, 1.0, 1.0) LIMIT ?"
    params.append(int(limit))
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            con.close()
            build_fts()
            return search(query, area, limit)
        raise
    finally:
        con.close()
    return [dict(r) for r in rows]


def print_results(results: list[dict], query: str) -> None:
    if not results:
        print(f"No notes match '{query}'.")
        return
    for i, r in enumerate(results, 1):
        snip = re.sub(r"\s+", " ", r["snip"] or "").strip()
        print(f"{i}. {r['title']}")
        print(f"   brain/{r['path']}  ({r['added']}, {r['kind']}, {r['area']})")
        if snip:
            print(f"   {snip}")
        print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m brain.search", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="?", help="words to look for")
    p.add_argument("--area", choices=AREAS + ["domos"], help="limit to one area (domos is the DOM OS mirror)")
    p.add_argument("--limit", type=int, default=10, help="how many results (default 10)")
    p.add_argument("--rebuild", action="store_true", help="reindex the whole library first")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rebuild:
        index_path, count = rebuild_index()
        print(f"Reindexed {count} notes. INDEX.md and {Path('brain') / DB_FILE.name} rebuilt.")
    if not args.query:
        if not args.rebuild:
            build_parser().print_help()
            return 1
        return 0
    print_results(search(args.query, args.area, args.limit), args.query)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # output piped into head or similar, not an error
        sys.stderr.close()
        sys.exit(0)
