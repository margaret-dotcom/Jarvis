#!/usr/bin/env python3
"""Merge a small patch into dashboard/data/today.json and validate it.

Only these fields may change through this script:
  headline, day_shape, yesterday, content, and per goal: status, current, note.
Everything else in today.json is left exactly as the collectors wrote it.

Usage (run from JARVIS_HOME):
  python hermes/skills/jarvis-core/scripts/write_today.py patch.json
  echo '{"headline": "..."}' | python hermes/skills/jarvis-core/scripts/write_today.py -
  python hermes/skills/jarvis-core/scripts/write_today.py --check

Patch shape (every key optional):
  {
    "headline": "one sentence",
    "day_shape": "heavy" | "normal" | "open" | "",
    "yesterday": {"summary": "", "accomplished": [], "slipped": [], "carry_forward": []},
    "content": {"scanned_at": "...", "recommendations": [ ... ]},
    "goals": [{"id": "goal-id", "status": "on_track", "current": 12, "note": ""}]
  }
Goals are matched by id. A goal id that is not already in today.json is reported
and skipped, because goals are defined in goals/goals.yaml, not here.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

TOP_LEVEL_ALLOWED = {"headline", "day_shape", "yesterday", "content"}
GOAL_ALLOWED = {"status", "current", "note"}


def repo_root() -> Path:
    env_home = os.environ.get("JARVIS_HOME", "").strip()
    if env_home and (Path(env_home) / "dashboard" / "data" / "schema.json").exists():
        return Path(env_home)
    here = Path(__file__).resolve()
    for parent in [Path.cwd(), *here.parents]:
        if (parent / "dashboard" / "data" / "schema.json").exists():
            return parent
    sys.exit("write_today: cannot find the repo. Set JARVIS_HOME or run from the repo directory.")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def validate(doc: dict, schema: dict) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is not installed. Run: pip install -r requirements.txt"]
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return errors


def apply_patch(today: dict, patch: dict) -> list[str]:
    notes: list[str] = []
    for key, value in patch.items():
        if key == "goals":
            continue
        if key not in TOP_LEVEL_ALLOWED:
            notes.append(f"skipped '{key}': not a field the agent may write")
            continue
        today[key] = value
        notes.append(f"set {key}")

    goal_patches = patch.get("goals") or []
    if goal_patches:
        existing = {g.get("id"): g for g in today.get("goals", []) if isinstance(g, dict)}
        for gp in goal_patches:
            gid = gp.get("id")
            if gid not in existing:
                notes.append(f"skipped goal '{gid}': not in today.json (define it in goals/goals.yaml)")
                continue
            for field, value in gp.items():
                if field == "id":
                    continue
                if field not in GOAL_ALLOWED:
                    notes.append(f"skipped goal '{gid}'.{field}: not writable")
                    continue
                existing[gid][field] = value
            notes.append(f"updated goal {gid}")
    return notes


def main(argv: list[str]) -> int:
    root = repo_root()
    today_path = root / "dashboard" / "data" / "today.json"
    schema_path = root / "dashboard" / "data" / "schema.json"
    schema = load_json(schema_path)

    if not today_path.exists():
        print(f"write_today: {today_path} does not exist. Run: python -m collectors.build_today", file=sys.stderr)
        return 2
    today = load_json(today_path)

    if len(argv) == 2 and argv[1] == "--check":
        errors = validate(today, schema)
        if errors:
            print("today.json is INVALID:")
            for e in errors:
                print("  " + e)
            return 1
        print("today.json is valid")
        return 0

    if len(argv) != 2:
        print(__doc__)
        return 2

    raw = sys.stdin.read() if argv[1] == "-" else Path(argv[1]).read_text(encoding="utf-8")
    try:
        patch = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"write_today: patch is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(patch, dict):
        print("write_today: patch must be a JSON object", file=sys.stderr)
        return 2

    notes = apply_patch(today, patch)
    errors = validate(today, schema)
    if errors:
        print("write_today: refused, the result would not validate against schema.json:")
        for e in errors:
            print("  " + e)
        return 1

    fd, tmp = tempfile.mkstemp(dir=str(today_path.parent), prefix=".today.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(today, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, today_path)
    for n in notes:
        print(n)
    print(f"wrote {today_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
