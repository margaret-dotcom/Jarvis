# collectors

Python scripts that gather the day's facts and write `dashboard/data/today.json`.
The dashboard reads that file. The Hermes morning brief reads it too, then adds
its own lines (headline, day shape, yesterday, content picks) to the same file.
The file must validate against `dashboard/data/schema.json`.

## Run it

From the repo root:

    pip install -r requirements.txt
    cp .env.example .env                                   # fill in tokens
    cp collectors/accounts.yaml.example collectors/accounts.yaml
    python -m collectors.google_auth                       # once per account
    python -m collectors.build_today

Useful flags:

    python -m collectors.build_today --dry-run             # print, do not write
    python -m collectors.build_today --only gmail,gcal     # a subset
    python -m collectors.build_today --sample              # fictional data, no APIs

`--sample` writes a full, made-up day so you can design the dashboard without
any credentials. Every name and number in it is fictional.

The build never stops because one source failed. Each source lands in the
`sources` list as `ok`, `error` (with a short reason) or `not_configured`.
Exit code is 0 when anything was collected and 2 when nothing was. Set
`JARVIS_DEBUG=1` to see full tracebacks for errors.

Every run also copies the result to `dashboard/data/history/<date>.json`.

## Modules

| Module | What it does |
| --- | --- |
| `config.py` | Reads `.env` and `accounts.yaml`, knows the timezone (`JARVIS_TZ`), and gives every collector `now_local()`. No network. |
| `google_auth.py` | Signs in each Google account with one shared OAuth client. Tokens live in `collectors/tokens/<email>.token.json` (git-ignored). Read-only Gmail and Calendar scopes. |
| `gmail.py` | Threads that need a reply (last 3 days, latest message not from you, not automated) and threads you are waiting on (your message is the latest, 2 or more days old, last 10 days, max 10). Metadata only, no bodies. |
| `gcal.py` | Today and tomorrow across every calendar id in `accounts.yaml`. Skips declined events. `mark_conflicts()` flags overlapping timed events today, across accounts. |
| `domos.py` | DOM OS, your system of record, read through Supabase with the service role key. `tasks()` gives your open tasks due today, overdue, and done yesterday. `inbox()` gives open DOM notifications, EOW reports waiting on your response, and your DOM events today. `atwc()` returns waitlist counts only (pending, added, scheduled, removed, conversion, median days) and never selects a patient column. `qca()` returns the WIP numbers, jobs in motion under 40 percent gross profit, the pipeline, this month's sales log totals, and recent profit alerts. It also exposes named read-only queries for the agents: `python -m collectors.domos --list`. |
| `goals.py` | Reads `goals/goals.yaml`. Goals with `metric: domos` are computed live (monthly ATWC revenue, weekly sessions, waitlist conversion, QCA sold and collected this month, lowest active gross profit). A `target` can be a DOM OS path such as `atwc_targets.revenue.monthly` so the number follows what DOM OS holds. Everything else is `unknown` until the evening summary fills it in. |
| `brain_stats.py` | Counts items in `brain/library`, items added yesterday (frontmatter `added`), files waiting in `brain/inbox`, and the five most recent items. |
| `build_today.py` | Runs everything, validates against the schema (warnings, never a crash), writes the file atomically. |

## Authorize Google accounts

1. In Google Cloud Console create an OAuth client of type Desktop app. Enable
   the Gmail API and the Google Calendar API on the same project.
2. Download the client JSON to `collectors/credentials/client_secret.json`
   (or anywhere, and set `GOOGLE_CLIENT_SECRET_FILE` in `.env`).
3. List your accounts in `collectors/accounts.yaml`.
4. Run `python -m collectors.google_auth` on a machine with a browser. It opens
   one consent screen per account. Sign in with the exact address it names.
   If a local port cannot be opened, it prints a URL instead; approve it, then
   paste the final `localhost` address from the browser back into the terminal.
5. `python -m collectors.google_auth --check` reports which tokens work.

Tokens refresh on their own. If Google revokes one, the source shows as
`not_configured` with the command to run again.

## Add another Google account

Add a block to `collectors/accounts.yaml`:

    - email: margaret@qcaroofing.com
      business: qca
      label: QCA inbox
      calendars: [primary]

Then run `python -m collectors.google_auth` again. Accounts that already have a
working token are skipped, only the new one opens a browser. The next
`build_today` run tags that inbox and calendar with `business: qca`.

`calendars` takes calendar ids. `primary` is the main one. Shared calendars
use the id shown under the calendar's settings in Google Calendar.

## What is kept between runs

Hermes jobs write `content`, `yesterday`, `headline` and `day_shape` into
`today.json`. The collector carries `content` and `yesterday` forward
(`content` is dropped once its `scanned_at` is older than 48 hours) and keeps
`headline` and `day_shape` only while the file's date is still today.

## Privacy

The waitlist read is aggregate only and requests no patient field. Gmail is
read with metadata headers and Gmail's own snippet. Tokens and `.env` are
git-ignored. Error messages that reach `sources` are one short line and have
token values removed.
