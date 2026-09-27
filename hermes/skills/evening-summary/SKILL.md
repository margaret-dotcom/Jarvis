---
name: evening-summary
description: The 7:00 PM Jarvis job. Re-runs the collectors, compares the day against the morning brief's "Needs you" list, writes the yesterday block into today.json for tomorrow, asks Margaret for any manual goal number in one line, files the brain inbox, then delivers what got done, what slipped, goal status, and tomorrow's first thing. Use when a cron job or Margaret asks for the evening summary or end of day wrap.
---

# Evening summary

Load jarvis-core first and follow its rules. Your final message is the summary itself.

## 1. Refresh

```bash
python -m collectors.build_today
```

This picks up tasks completed today and mail sent today. If it fails, continue with the existing today.json and open the summary with one line saying the refresh failed and why.

## 2. Find this morning's list

The morning brief is saved as cron output. Locate it:

```bash
python - <<'PY'
import json, os, glob
home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
jobs = json.load(open(os.path.join(home, "cron", "jobs.json")))
jobs = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
ids = [j["id"] for j in jobs if str(j.get("name", "")).startswith("morning-brief")]
files = sorted(f for i in ids for f in glob.glob(os.path.join(home, "cron", "output", i, "*.md")))
print(files[-1] if files else "NONE")
PY
```

Read the newest file if its date is today. Extract the "Needs you" items and the "On track" lines. If there is no file from today, say so in one line and compare against `tasks.due_today` and `email.needs_reply` from the morning instead (whatever today.json shows now).

## 3. Compare

For each morning "Needs you" item decide, from data only:

- done: the task is in `tasks.completed_yesterday` or no longer in due or overdue; the thread is no longer in `email.needs_reply`; the EOW report is no longer in `domos.eow_pending`; the notification is gone from `domos.notifications`; the meeting happened.
- slipped: still in `needs_reply`, `overdue`, `eow_pending`, or `notifications`, or the task is still due.
- unknown: the data cannot tell. Say so plainly instead of guessing.

Also list anything that got done today that was not on the morning list (from `tasks.completed_yesterday` after the refresh, which now means today).

## 4. Goals

For each goal in today.json:

- DOM OS goals (`metric: domos`): the collectors set `current` and `status`. Report them.
- Manual goals (check `metric: manual` in `goals/goals.yaml`): look in memory for a line `goal <id>: current <number> on <today>`. If found, use it. If not, ask Margaret in the delivery message, one goal per line, one line each, answerable with a number: "QCA Q4 revenue: what is the number today?" Ask about at most 3 goals per evening; pick the ones with the nearest due date.

When Margaret answers (in WhatsApp, in whatever session receives it), record it as jarvis-core section 9 says: write `current`, `status`, and a `note` through `write_today.py`, then save the memory line. Decide `status` by simple arithmetic: on pace for the due date is on_track, within 15 percent of pace is at_risk, further behind is behind, at or past target is done.

## 4b. The daily review, four prompts

Every evening the delivery ends with four prompts Margaret answers in one line each:

1. What went well today?
2. What did not go well?
3. One lesson.
4. One thing you are grateful for.

Write them exactly like that, numbered, after the goal questions. Do not answer them for her and do not skip them on a quiet day.

When she replies (in WhatsApp, in whatever session receives it), that session saves her answers to `brain/library/personal/YYYY-MM-DD-daily-review.md` with today's date. Write the file directly, not through `brain.ingest`, so the four headings stay fixed:

```
---
title: Daily review YYYY-MM-DD
area: personal
kind: note
date: YYYY-MM-DD
tags: [daily-review]
source: Margaret, WhatsApp
---

## Went well
her words

## Did not go well
her words

## Lesson
her words

## Grateful for
her words
```

Her words go in verbatim, identifiers removed. A partial answer (two of four) still gets saved with the other headings left empty. If the file for today already exists, add her new lines under the right headings instead of overwriting. Then run `python -m brain.search --rebuild "daily review"` once so the index has it, and commit it with the next publish step (`git add brain/library`).

## 5. Write the yesterday block

This block is what tomorrow morning's brief and dashboard show as "yesterday". Build it now:

```json
{"yesterday": {
  "summary": "One or two sentences on the day in plain words.",
  "accomplished": ["one line each, outcome first"],
  "slipped": ["one line each, with what it was waiting on if known"],
  "carry_forward": ["the items that should appear in tomorrow's Needs you"]
}}
```

Apply it with `python hermes/skills/jarvis-core/scripts/write_today.py -`. Also apply any goal updates you have in the same patch. The collectors are expected to carry `yesterday` into tomorrow's file; if tomorrow's morning brief finds it empty, it reads this job's saved output instead, so make sure the same four lists appear in your delivered message.

## 6. File the brain inbox

```bash
python -m brain.ingest
```

Then follow the brain-ingest skill's "grow the brain from the day" section: one short note per real decision or new fact from today. Only what the data or Margaret's own messages show. Nothing inferred.

## 7. Publish

```bash
git add dashboard/data/today.json brain/library brain/INDEX.md
git diff --cached --quiet || git commit -m "evening: $(date +%F)"
git push
```

If push fails twice, add one line to the summary saying the dashboard is stale and why.

## 8. Deliver

Structure, plain text with *bold* section names only, under 3000 characters for WhatsApp:

- Date line, then one sentence on the day.
- **Done**: each accomplished item, one line.
- **Slipped**: each slipped item, one line, with what it is waiting on if the data shows it. None: "Nothing slipped."
- **Goals**: one line per goal, status word and the number. Manual goals without today's number get the question here, one line each.
- **Your review**: the four prompts from 4b, numbered, one line each.
- **Tomorrow, first thing**: one item, the one from carry_forward or tomorrow's calendar with the earliest cost of being late. State what it is and why it is first. If tomorrow's first event starts before 9, name the time.
- Close with one observing line. No cheer, no command.

Voice: observe and hand over. Never apologize for a slow day. Never reproach ("you did not get to"). Say what happened: "The vendor reply is still open, 2 days now."

HIPAA: no patient identifiers anywhere in the summary or the brain notes.

## After the run

If a command or field in this file was wrong, patch this SKILL.md with `skill_manage(action="patch")` right away. Keep the writing rules.
