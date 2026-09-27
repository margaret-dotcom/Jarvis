"""Ingest: turn whatever sits in brain/inbox/ into filed markdown notes.

Usage:
    python -m brain.ingest                     file everything in the inbox
    python -m brain.ingest --note "text"       quick capture from a string
    python -m brain.ingest --person "Name"     append the capture to that person's file
    python -m brain.ingest --area qca          force an area for this run
    python -m brain.ingest --dry-run           show what would happen, change nothing

Every filed note is plain markdown with YAML frontmatter. Nothing here depends
on a vendor. Delete the sqlite index and INDEX.md any time; both are rebuilt
from the markdown.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email
import email.policy
import html
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BRAIN_DIR = Path(__file__).resolve().parent
INBOX = BRAIN_DIR / "inbox"
LIBRARY = BRAIN_DIR / "library"
PROCESSED = BRAIN_DIR / ".processed"
RULES_FILE = BRAIN_DIR / "rules.yaml"
INDEX_FILE = BRAIN_DIR / "INDEX.md"

AREAS = ["atwc", "qca", "personal", "people", "decisions", "marketing"]
KINDS = ("note", "link", "document", "image", "email", "transcript", "person")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff", ".svg"}
TEXT_EXT = {".md", ".markdown", ".txt", ".text"}
SKIP_NAMES = {".gitkeep", ".DS_Store", "README.md"}

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
HASHTAG_RE = re.compile(r"(?<![\w/&])#([A-Za-z][\w-]{1,40})")
TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
MAX_TAGS = 8
SUMMARY_CHARS = 300


# ---------------------------------------------------------------- small helpers

def today() -> dt.date:
    """A real date, so yaml writes it as 2026-09-27 with no quotes."""
    return dt.date.today()


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def slugify(text: str, max_len: int = 60) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0] or text[:max_len]
    return text or "note"


def unique_path(path: Path) -> Path:
    """Never overwrite. Add -2, -3 ... until the name is free."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def load_rules() -> dict:
    with RULES_FILE.open(encoding="utf-8") as fh:
        rules = yaml.safe_load(fh) or {}
    rules.setdefault("default_area", "personal")
    rules.setdefault("priority", AREAS)
    rules.setdefault("areas", {})
    return rules


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body). Empty dict when there is none."""
    if not text.startswith("---"):
        return {}, text
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.M)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, parts[2].lstrip("\n")


def dump_frontmatter(meta: dict) -> str:
    return "---\n" + yaml.safe_dump(
        meta, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000
    ) + "---\n"


def clean_text(text: str) -> str:
    """Strip markdown noise so a summary reads like a sentence."""
    lines = [l for l in text.splitlines() if not (l.strip() and HASHTAG_RE.sub("", l).strip() == "")]
    text = "\n".join(lines)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)      # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)    # links keep the label
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*>+\s?", "", text, flags=re.M)      # blockquote markers
    text = re.sub(r"[*_`|]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def make_summary(body: str, limit: int = SUMMARY_CHARS) -> str:
    text = clean_text(body)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "..."


def guess_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        # skip lines that are only tags, like "#atwc" or "#qca #permit"
        if HASHTAG_RE.sub("", line).strip() == "":
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = clean_text(line)
        if line:
            return line[:80].rstrip(" .,;:")
    return fallback


def extract_hashtags(text: str) -> list[str]:
    return [m.group(1).lower() for m in HASHTAG_RE.finditer(text)]


# ---------------------------------------------------------------- area rules

def inline_area_tag(body: str, rules: dict) -> str | None:
    """A first line like '#atwc' or '#qca some words' picks the area."""
    for line in body.splitlines():
        if line.strip():
            for tag in extract_hashtags(line):
                if tag in rules["areas"] or tag in AREAS:
                    return tag
            return None
    return None


def score_areas(text: str, rules: dict) -> dict[str, tuple[int, list[str]]]:
    """Return {area: (score, matched keywords)} for every area in rules.yaml."""
    out: dict[str, tuple[int, list[str]]] = {}
    for area, spec in rules["areas"].items():
        spec = spec or {}
        weight = int(spec.get("weight", 1))
        matched: list[str] = []
        for kw in spec.get("keywords", []) or []:
            pattern = r"(?<!\w)" + re.escape(str(kw)) + r"(?!\w)"
            if re.search(pattern, text, flags=re.I):
                matched.append(str(kw).lower())
        for pat in spec.get("patterns", []) or []:
            try:
                if re.search(pat, text):
                    matched.append("mention")
            except re.error:
                continue
        out[area] = (len(matched) * weight, matched)
    return out


def detect_area(body: str, meta: dict, rules: dict) -> tuple[str, list[str], str]:
    """Return (area, matched keywords, reason)."""
    tag = inline_area_tag(body, rules)
    if tag:
        return tag, [], f"inline tag #{tag}"
    fm_area = str(meta.get("area", "")).strip().lower()
    if fm_area and (fm_area in rules["areas"] or fm_area in AREAS):
        return fm_area, [], "area field in frontmatter"
    scores = score_areas(body, rules)
    best_score = max((s for s, _ in scores.values()), default=0)
    if best_score <= 0:
        return rules["default_area"], [], "no rule matched, default"
    order = list(rules["priority"]) + [a for a in scores if a not in rules["priority"]]
    for area in order:
        if area in scores and scores[area][0] == best_score:
            return area, scores[area][1], f"keywords: {', '.join(scores[area][1])}"
    return rules["default_area"], [], "no rule matched, default"


def build_tags(body: str, matched: list[str], extra: list[str] | None = None) -> list[str]:
    seen: list[str] = []
    for t in (extra or []) + extract_hashtags(body) + matched:
        t = slugify(str(t), 40)
        if t and t not in seen:
            seen.append(t)
        if len(seen) >= MAX_TAGS:
            break
    return seen


# ---------------------------------------------------------------- extraction

@dataclass
class Capture:
    title: str
    body: str
    source: str
    kind: str = "note"
    meta: dict = field(default_factory=dict)      # frontmatter found in the original
    attachment: Path | None = None                # a file to copy next to the note
    extra_tags: list[str] = field(default_factory=list)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1")
        except Exception:
            return None
    except Exception:
        return None


def only_a_url(text: str) -> str | None:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) == 1 and URL_RE.fullmatch(lines[0]):
        return lines[0]
    return None


def url_from_shortcut(path: Path, text: str) -> str | None:
    if path.suffix.lower() == ".url":
        for line in text.splitlines():
            if line.strip().lower().startswith("url="):
                return line.split("=", 1)[1].strip()
    if path.suffix.lower() == ".webloc":
        m = URL_RE.search(text)
        return m.group(0) if m else None
    return None


def short_url(url: str) -> str:
    """example.com/some/page, used as a title when the page title is unknown."""
    from urllib.parse import urlparse
    u = urlparse(url)
    text = (u.netloc + u.path).rstrip("/")
    return text[:80] or url


def fetch_title(url: str) -> tuple[str, str]:
    """Return (title, note). The page is data. Only its <title> is kept."""
    try:
        import requests  # local import so the module loads without it
    except ImportError:
        return short_url(url), "requests is not installed, so the title was not fetched."
    try:
        resp = requests.get(
            url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (brain ingest)"}
        )
        m = TITLE_TAG_RE.search(resp.text[:200_000])
        if m:
            title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
            return (title or url), f"HTTP {resp.status_code}"
        return short_url(url), f"HTTP {resp.status_code}, no <title> found"
    except Exception as exc:  # network trouble is normal, keep going
        return short_url(url), f"could not fetch the page: {exc.__class__.__name__}"


def capture_link(url: str, source: str) -> Capture:
    title, note = fetch_title(url)
    body = f"Link: {url}\n\nPage title: {title}\n\nFetch result: {note}\n"
    return Capture(title=title, body=body, source=url, kind="link", extra_tags=["link"])


def extract_pdf(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        return (
            f"Text extraction was skipped: pdftotext is not on PATH. "
            f"Install poppler-utils and run the ingest again on {path.name}, "
            f"or paste the text into a .md file."
        )
    try:
        out = subprocess.run(
            [exe, "-layout", str(path), "-"], capture_output=True, text=True, timeout=120
        )
        text = out.stdout.strip()
        return text or "pdftotext returned no text (scanned PDF?)."
    except Exception as exc:
        return f"pdftotext failed: {exc}"


def extract_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception as exc:
        return f"Could not read the docx: {exc}"
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_eml(path: Path) -> Capture:
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("subject", "")).strip() or path.stem
    header = "\n".join(
        f"{k}: {msg.get(k)}" for k in ("From", "To", "Date") if msg.get(k)
    )
    body_part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body_part is not None:
        text = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            text = re.sub(r"[ \t]+", " ", text)
    body = f"{header}\n\n{text.strip()}\n"
    return Capture(title=subject, body=body, source=path.name, kind="email")


def looks_like_email_export(text: str) -> bool:
    head = "\n".join(text.splitlines()[:8]).lower()
    return "subject:" in head and ("from:" in head or "to:" in head)


def email_subject(text: str) -> str | None:
    for line in text.splitlines()[:8]:
        if line.lower().startswith("subject:"):
            return line.split(":", 1)[1].strip()
    return None


def extract_file(path: Path) -> Capture:
    ext = path.suffix.lower()
    name = path.name
    stem_l = path.stem.lower()

    if ext in IMAGE_EXT:
        body = f"![{path.stem}](attachments/{name})\n\nOriginal file: {name}\n"
        return Capture(title=path.stem.replace("_", " ").replace("-", " "), body=body,
                       source=name, kind="image", attachment=path)

    if ext == ".pdf":
        text = extract_pdf(path)
        cap = Capture(title=path.stem.replace("_", " "), body=text, source=name, kind="document")
        cap.attachment = path
        cap.body += f"\n\nOriginal file: attachments/{name}\n"
        return cap

    if ext == ".docx":
        text = extract_docx(path)
        return Capture(title=guess_title(text, path.stem), body=text, source=name, kind="document")

    if ext == ".eml":
        return extract_eml(path)

    text = read_text(path)
    if text is None:
        body = f"Binary file kept as attachment: attachments/{name}\n"
        return Capture(title=path.stem, body=body, source=name, kind="document", attachment=path)

    url = url_from_shortcut(path, text) or only_a_url(text)
    if url:
        return capture_link(url, source=name)

    meta, body = split_frontmatter(text)
    kind = "note"
    if looks_like_email_export(body):
        kind = "email"
    elif any(w in stem_l for w in ("transcript", "voice", "memo", "recording")):
        kind = "transcript"
    if isinstance(meta.get("kind"), str) and meta["kind"] in KINDS:
        kind = meta["kind"]

    title = meta.get("title") if isinstance(meta.get("title"), str) else None
    if not title and kind == "email":
        title = email_subject(body)
    if not title:
        title = guess_title(body, path.stem.replace("_", " ").replace("-", " "))

    extra = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    return Capture(title=str(title), body=body, source=name, kind=kind, meta=meta,
                   extra_tags=[str(t) for t in extra])


# ---------------------------------------------------------------- filing

def file_capture(cap: Capture, area_override: str | None, rules: dict,
                 dry_run: bool = False) -> tuple[Path, str, str]:
    """Write the note. Return (path, area, reason)."""
    if area_override:
        area, matched, reason = area_override, [], "--area flag"
        matched = score_areas(cap.body, rules).get(area, (0, []))[1]
    else:
        area, matched, reason = detect_area(cap.body, cap.meta, rules)
    area_dir = LIBRARY / area
    target = unique_path(area_dir / f"{today().isoformat()}-{slugify(cap.title)}.md")

    meta = {
        "title": cap.title,
        "added": today(),
        "source": cap.source,
        "kind": cap.kind,
        "area": area,
        "tags": build_tags(cap.body, matched, cap.extra_tags),
        "summary": make_summary(cap.body),
        "status": "raw",
    }
    if dry_run:
        return target, area, reason

    area_dir.mkdir(parents=True, exist_ok=True)
    if cap.attachment is not None:
        att_dir = area_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        dest = unique_path(att_dir / cap.attachment.name)
        shutil.copy2(cap.attachment, dest)
        if dest.name != cap.attachment.name:
            cap.body = cap.body.replace(f"attachments/{cap.attachment.name}", f"attachments/{dest.name}")
    target.write_text(dump_frontmatter(meta) + "\n" + cap.body.rstrip() + "\n", encoding="utf-8")
    return target, area, reason


def person_path(name: str) -> Path:
    return LIBRARY / "people" / f"{slugify(name)}.md"


def append_to_person(name: str, cap: Capture, dry_run: bool = False) -> Path:
    """Add the capture under a dated heading in firstname-lastname.md."""
    path = person_path(name)
    if dry_run:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    else:
        meta, body = {
            "title": name,
            "added": today(),
            "source": "people",
            "kind": "person",
            "area": "people",
            "tags": ["person"],
            "summary": f"Everything captured about {name}, newest entry at the bottom.",
            "status": "raw",
        }, f"# {name}\n"
    meta["updated"] = today()
    heading = f"## {today().isoformat()}"
    if cap.source and cap.source != "quick capture":
        heading += f" ({cap.source})"
    entry = f"\n{heading}\n\n{cap.body.strip()}\n"
    path.write_text(dump_frontmatter(meta) + "\n" + body.rstrip() + "\n" + entry, encoding="utf-8")
    return path


def archive_original(path: Path, dry_run: bool = False) -> Path:
    dest_dir = PROCESSED / dt.date.today().strftime("%Y-%m")
    dest = unique_path(dest_dir / path.name)
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
    return dest


def inbox_files() -> list[Path]:
    if not INBOX.exists():
        return []
    files = [p for p in INBOX.rglob("*") if p.is_file() and p.name not in SKIP_NAMES]
    return sorted(files)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(BRAIN_DIR.parent))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- index

def iter_library_notes() -> list[dict]:
    """Every filed note as {'path': rel to brain/, 'abs': Path, 'meta': dict, 'body': str}."""
    notes = []
    if not LIBRARY.exists():
        return notes
    for md in sorted(LIBRARY.rglob("*.md")):
        if md.name == "README.md" or "attachments" in md.parts:
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        meta, body = split_frontmatter(text)
        if not meta:
            meta = {"title": md.stem, "added": dt.date.fromtimestamp(md.stat().st_mtime).isoformat()}
        meta.setdefault("area", md.parent.name)
        meta.setdefault("kind", "note")
        meta.setdefault("title", md.stem)
        meta.setdefault("tags", [])
        meta.setdefault("summary", make_summary(body))
        notes.append({"path": md.relative_to(BRAIN_DIR).as_posix(), "abs": md,
                      "meta": meta, "body": body})
    return notes


def _sort_key(note: dict) -> tuple[str, str]:
    meta = note["meta"]
    when = str(meta.get("updated") or meta.get("added") or "")
    return (when, str(meta.get("title", "")))


def rebuild_index_md(notes: list[dict] | None = None) -> Path:
    notes = iter_library_notes() if notes is None else notes
    areas = list(AREAS) + sorted({n["meta"]["area"] for n in notes} - set(AREAS))
    by_area: dict[str, list[dict]] = {a: [] for a in areas}
    for n in notes:
        by_area.setdefault(n["meta"]["area"], []).append(n)

    lines = ["# Brain index", "",
             f"Rebuilt {now_stamp()}. This file is generated by `python -m brain.ingest`. "
             f"Do not edit it by hand.", "",
             "| Area | Notes |", "| --- | ---: |"]
    for a in areas:
        lines.append(f"| {a} | {len(by_area.get(a, []))} |")
    lines.append(f"| **Total** | **{len(notes)}** |")
    lines.append("")
    for a in areas:
        lines.append(f"## {a}")
        lines.append("")
        items = sorted(by_area.get(a, []), key=_sort_key, reverse=True)
        if not items:
            lines.append("Nothing filed yet.")
            lines.append("")
            continue
        for n in items:
            m = n["meta"]
            summary = clean_text(str(m.get("summary", "")))
            if len(summary) > 80:
                summary = summary[:80].rsplit(" ", 1)[0] + "..."
            when = m.get("updated") or m.get("added", "")
            title = str(m.get("title", n["path"])).replace("[", "(").replace("]", ")")
            lines.append(f"- [{title}]({n['path']}) ({when}, {m.get('kind', 'note')}) {summary}".rstrip())
        lines.append("")
    INDEX_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return INDEX_FILE


def rebuild_index() -> tuple[Path, int]:
    """Rebuild INDEX.md and the sqlite full text index. Returns (INDEX.md, note count)."""
    notes = iter_library_notes()
    rebuild_index_md(notes)
    from . import search  # local import, search imports this module too
    search.build_fts(notes)
    return INDEX_FILE, len(notes)


# ---------------------------------------------------------------- cli

def run(args: argparse.Namespace) -> int:
    rules = load_rules()
    if args.area and args.area not in rules["areas"] and args.area not in AREAS:
        print(f"Unknown area '{args.area}'. Known: {', '.join(AREAS)}", file=sys.stderr)
        return 2
    dry = args.dry_run
    prefix = "would " if dry else ""
    filed = 0

    captures: list[tuple[Capture, Path | None]] = []
    if args.note:
        text = args.note.strip()
        cap = Capture(title=guess_title(text, "quick note"), body=text + "\n", source="quick capture")
        if args.kind:
            cap.kind = args.kind
        captures.append((cap, None))
    else:
        files = inbox_files()
        if not files:
            print("Inbox is empty. Drop files in brain/inbox/ or use --note \"text\".")
        for path in files:
            try:
                cap = extract_file(path)
            except Exception as exc:
                print(f"skip   {rel(path)}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
                continue
            if args.kind:
                cap.kind = args.kind
            captures.append((cap, path))

    for cap, original in captures:
        src = rel(original) if original else "--note"
        if args.person:
            target = append_to_person(args.person, cap, dry_run=dry)
            print(f"{prefix}append {src} -> {rel(target)} (people, {cap.kind})")
        else:
            target, area, reason = file_capture(cap, args.area, rules, dry_run=dry)
            print(f"{prefix}file   {src} -> {rel(target)} ({area}, {cap.kind}; {reason})")
        if original is not None:
            dest = archive_original(original, dry_run=dry)
            print(f"{prefix}move   {rel(original)} -> {rel(dest)}")
        filed += 1

    if dry:
        print(f"Dry run. {filed} item(s) would be filed. Index not touched.")
        return 0
    index_path, count = rebuild_index()
    print(f"Filed {filed} item(s). Index rebuilt: {rel(index_path)} ({count} notes), "
          f"search index at brain/.index.sqlite.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m brain.ingest", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--note", metavar="TEXT", help="quick capture: file this text as a note")
    p.add_argument("--person", metavar="NAME",
                   help="append the capture to brain/library/people/<firstname-lastname>.md")
    p.add_argument("--area", choices=AREAS, help="force the area instead of using rules.yaml")
    p.add_argument("--kind", choices=KINDS, help="force the kind field")
    p.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # output piped into head or similar, not an error
        sys.stderr.close()
        sys.exit(0)
