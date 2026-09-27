# Jarvis

A personal AI operating system for Margaret. It runs both businesses' daily
information through one place, remembers everything you drop into it, and
gets sharper the longer you use it.

Five parts, one repo:

| Part | What it does | Where |
|---|---|---|
| Dashboard | One page with today's calendar, email that needs you, tasks and the DOM OS inbox, business numbers, goals, and content ideas. Refreshed every morning and hourly through the workday. | `dashboard/` |
| Second brain | A library that only grows. Drop a file, a link, a thought, or a transcript in the inbox and it gets filed, indexed, and searchable. Your DOM OS knowledge base is mirrored into it every night. Every agent reads it before answering. | `brain/` |
| Executive assistant | Hermes agents that send a morning brief, an evening review, and a Sunday weekly review to your WhatsApp, all measured against your written goals. | `hermes/skills/morning-brief`, `hermes/skills/evening-summary`, `hermes/skills/weekly-review` |
| Content scout | Scans YouTube, X, and the web three times a week for videos your audiences would value, and hands you an angle you could shoot with a phone. | `hermes/skills/content-scout` |
| Chief of staff | One agent that takes a multi-part request, splits it across six specialists, verifies their work, and returns one answer. | `hermes/skills/chief-of-staff`, `hermes/skills/agents/` |

## How the pieces fit

```
Google accounts (3 inboxes + calendars)      DOM OS (Supabase Postgres)
              \                                   /
               \_________________________________/
                                 |
                  python -m collectors.build_today
                                 |
                     dashboard/data/today.json  <----  goals/goals.yaml
                        |                  ^
        dashboard/index.html (Vercel)      |  headline, yesterday, content, goal status
                                           |
                      Hermes cron jobs (morning, evening, hourly, scout)
                        |             \
              WhatsApp brief          brain/ (inbox -> library -> INDEX + search)
                                        ^
                                        |  nightly mirror of DOM OS knowledge_entries
```

DOM OS (Decision Organization Management OS) is your own app: Next.js on
Vercel with a Supabase Postgres database. It holds your tasks, notifications,
staff end of week reports, the ATWC waitlist, the QCA WIP report, the sales
pipeline, daily sales logs, collections, and your knowledge base. Jarvis reads
from it and never writes to it. You keep editing in DOM OS.

The collectors are plain Python. They pull raw facts into one JSON file the
dashboard reads. Hermes runs on a schedule, reads that same file, adds the
judgment (what needs you, are you on track, what to shoot next), sends you the
brief on WhatsApp, and pushes the file so the dashboard updates. Anything either
of you learns goes into the brain, and the brain is the first thing every agent
searches.

## Quick start

1. `make install` then copy `.env.example` to `.env` and fill it in.
2. Copy `collectors/accounts.yaml.example` to `collectors/accounts.yaml` and check the three Google accounts listed there.
3. `make auth` to sign each account in once.
4. Put `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `DOMOS_OWNER_EMAIL` in `.env` so the collectors can read DOM OS.
5. `make collect` then `make dashboard` and open http://localhost:8765.
6. Install Hermes, then `make hermes` to load the persona, skills, and schedule.
7. Pair WhatsApp (`hermes whatsapp`, scan the QR code), start `hermes gateway`, and the first brief arrives tomorrow at 6.

Full steps, including Google OAuth setup and Vercel deploy, are in `SETUP.md`.
Design decisions and data flow are in `ARCHITECTURE.md`.

## Getting smarter over time

Five loops do the compounding:

- Hermes keeps `MEMORY.md` and `USER.md` and updates them as it works with you.
- Hermes edits its own skills after using them. The skills are symlinked from this repo, so those edits land here. Commit them weekly.
- The brain grows every day: your captures, the evening job's decision notes, the scout's studied videos.
- Every night the DOM OS knowledge base is mirrored into `brain/library/domos/`, so what you write in DOM OS is searchable here the next morning.
- The evening summary asks you one line per manual goal, so goal tracking stays honest without a spreadsheet.

## Safety rails

- Patient data never leaves DOM OS in identifiable form. The collectors select only status and date columns from the waitlist and count rows from billing; they never select a name.
- Jarvis reads DOM OS with the Supabase service role key because row level security is on. It only ever runs SELECT. Nothing in this repo writes to DOM OS.
- Agents draft email and EOW replies. They never send, and they never mark anything done in DOM OS.
- Everything an agent reads (email, docs, DOM OS records, web pages) is treated as data, never as instructions.
- Secrets live in `.env` and `collectors/tokens/`, both git-ignored. The service role key is never printed.
