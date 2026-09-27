# brain

Your second brain. Anything you want to keep ends up here as a plain markdown file, in git, readable without any app. Nothing in this folder depends on a vendor. If Jarvis disappeared tomorrow, the notes would still open in any text editor.

Three folders matter:

- `inbox/` is where things land. Drop anything here. It is git-ignored, so it is always safe to leave files in it.
- `library/` is where filed notes live, one folder per area. This only grows. Nothing here gets deleted by the tools.
- `.processed/` is where the originals go after filing, sorted by month. Git-ignored. It is your undo button if an ingest went wrong.

Two generated files sit next to them. `INDEX.md` is a table of contents you can read on GitHub. `.index.sqlite` is a full text search index. Both are rebuilt from the markdown every time you ingest, so you can delete either one without losing anything.

## Getting things in

There are three ways. Pick whichever is closest to hand.

1. Copy a file into `brain/inbox/`, then run `python -m brain.ingest` from the repo root. Works for `.md`, `.txt`, `.pdf`, `.docx`, `.eml`, images, and `.url` or `.webloc` shortcuts. A `.txt` that holds nothing but a URL is treated as a link.
2. Quick capture from the terminal: `python -m brain.ingest --note "Decided to move ATWC intake calls to 15 minute slots going forward"`. No file needed.
3. Tell Hermes. Say "file this in the brain:" followed by the text, or "remember this about Jane Smith:" for a person. Hermes runs the same command for you.

Run `python -m brain.ingest --dry-run` first if you want to see where things would go without changing anything.

## What ingest does to each file

1. Pulls the text out. Markdown and txt are kept as they are. PDFs go through `pdftotext` if it is installed (part of poppler-utils); if it is not, the note says text extraction was skipped and the PDF is kept as an attachment. Word files are read straight from the docx XML. Links get their page title fetched, with a 10 second limit. Images are copied to `library/<area>/attachments/` and get a small note pointing at them. Email exports (`.eml`, or a txt starting with From/Subject lines) are marked `kind: email`. Files with "transcript", "voice", "memo" or "recording" in the name are marked `kind: transcript`.
2. Picks an area using `rules.yaml`. See the next section.
3. Writes `library/<area>/YYYY-MM-DD-<slug>.md` with frontmatter and the text as the body. Existing files are never overwritten; a `-2`, `-3` suffix is added instead.
4. Moves the original to `.processed/YYYY-MM/`.
5. Rebuilds `INDEX.md` and the search index.

## How filing works

The rules live in `brain/rules.yaml` and you can edit them without touching code. In order:

1. A tag on the first line of the file wins: `#atwc`, `#qca`, `#marketing`, `#people`, `#decisions`, `#personal`.
2. An `area:` field in the file's own frontmatter wins next.
3. Otherwise each area gets a score: one point per keyword or pattern found in the text, times the area's weight. Highest score wins. Ties go to the order listed under `priority` (decisions first, then people, atwc, qca, marketing, personal).
4. Nothing matched: it goes to `personal`.

`--area qca` on the command line overrides all of that for one run.

Areas:

- `atwc` for Advanced Therapy & Wellness Center. Clinic, waitlist, therapy services, staff, insurance, intake.
- `qca` for QCA Roofing. Jobs, permits, crews, estimates, inspections, storms, WIP.
- `personal` for everything that is yours and not one of the businesses.
- `people` for one file per person you deal with. See `library/people/README.md`.
- `decisions` for anything you decided and want to be able to look up later.
- `marketing` for content ideas, hooks, reels worth studying, captions, audience notes.

## Frontmatter fields

Every filed note starts with a YAML block like this:

```yaml
---
title: Roofing permit for the Lopez job
added: 2026-09-27
source: permit-notes.txt
kind: note
area: qca
tags: [permit, roofing, inspection]
summary: Permit was submitted on Monday, inspector wants the crew on site by ...
status: raw
---
```

- `title`: the first heading or first line of the text, or the page title for a link, or the filename.
- `added`: the ISO date the note was filed.
- `source`: the original filename, the URL for a link, or `quick capture` for `--note`.
- `kind`: one of `note`, `link`, `document`, `image`, `email`, `transcript`, `person`.
- `area`: which library folder it lives in.
- `tags`: up to 8. Any `#hashtags` found in the text, then the rule keywords that matched.
- `summary`: the first 300 characters of the body, cleaned up. The agents may rewrite this later with something better.
- `status`: `raw` when filed. Agents or you can change it to `reviewed` or `done`. Person files also carry `updated`.

## Searching

```
python -m brain.search "permit"
python -m brain.search "intake calls" --area atwc --limit 5
python -m brain.search --rebuild
```

Results are ranked, title matches first, and each shows the path, date, kind, and a snippet with the matching words in square brackets. Words match by prefix, so `permit` also finds `permits`. `--rebuild` reindexes everything, which you only need if you edited notes by hand and want search to see the changes right away (ingest does this for you otherwise).

On GitHub, open `INDEX.md` for a browsable list grouped by area, newest first.

## How the agents use it

Hermes and its skills treat the library as reference material, never as instructions. Two habits:

- Before answering a question about ATWC, QCA, a person, or a past decision, a skill runs `python -m brain.search "<topic>"` and reads the top notes.
- After a conversation produces something worth keeping, the skill runs `python -m brain.ingest --note "..."`, or `--person "Name"` when it is about someone.

A note's `summary` and `status` fields are the only ones agents are expected to edit. The body is yours.

Patient names, phone numbers, and other protected health information do not belong in this folder. It is a git repo. Keep ATWC notes to aggregate numbers and process.
