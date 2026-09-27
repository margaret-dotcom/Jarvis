# Jarvis

A personal AI operating system for Margaret. It runs both businesses' daily
information through one place, remembers everything you drop into it, and
gets sharper the longer you use it.

Five parts, one repo:

| Part | What it does | Where |
|---|---|---|
| Dashboard | One page with today's calendar, email that needs you, tasks, business numbers, goals, and content ideas. Refreshed every morning and hourly through the workday. | `dashboard/` |
| Second brain | A library that only grows. Drop a file, a link, a thought, or a transcript in the inbox and it gets filed, indexed, and searchable. Every agent reads it before answering. | `brain/` |
| Executive assistant | Hermes agents that send a morning brief and an evening summary to your phone, measured against your written goals. | `hermes/skills/morning-brief`, `hermes/skills/evening-summary` |
| Content scout | Scans YouTube, X, and the web three times a week for videos your audiences would value, and hands you an angle you could shoot with a phone. | `hermes/skills/content-scout` |
| Chief of staff | One agent that takes a multi-part request, splits it across six specialists, verifies their work, and returns one answer. | `hermes/skills/chief-of-staff`, `hermes/skills/agents/` |

## How the pieces fit

```
Google accounts (N inboxes + calendars)   Asana   Airtable (ATWC Ops, Permit Tracker)
              \                              |          /
               \_____________________________|_________/
                                 |
                  python -m collectors.build_today
                                 |
                     dashboard/data/today.json  <----  goals/goals.yaml
                        |                  ^
        dashboard/index.html (Vercel)      |  headline, yesterday, content, goal status
                                           |
                      Hermes cron jobs (morning, evening, hourly, scout)
                        |             \
              Telegram brief          brain/ (inbox -> library -> INDEX + search)
```

The collectors are plain Python. They pull raw facts into one JSON file the
dashboard reads. Hermes runs on a schedule, reads that same file, adds the
judgment (what needs you, are you on track, what to shoot next), sends you the
brief, and pushes the file so the dashboard updates. Anything either of you
learns goes into the brain, and the brain is the first thing every agent
searches.

## Quick start

1. `make install` then copy `.env.example` to `.env` and fill it in.
2. Copy `collectors/accounts.yaml.example` to `collectors/accounts.yaml` and list every Google account you want included.
3. `make auth` to sign each account in once.
4. `make collect` then `make dashboard` and open http://localhost:8765.
5. Install Hermes, then `make hermes` to load the persona, skills, and schedule.
6. Pair Hermes with Telegram (`hermes gateway`), and the first brief arrives tomorrow at 6.

Full steps, including Google OAuth setup and Vercel deploy, are in `SETUP.md`.
Design decisions and data flow are in `ARCHITECTURE.md`.

## Getting smarter over time

Four loops do the compounding:

- Hermes keeps `MEMORY.md` and `USER.md` and updates them as it works with you.
- Hermes edits its own skills after using them. The skills are symlinked from this repo, so those edits land here. Commit them weekly.
- The brain grows every day: your captures, the evening job's decision notes, the scout's studied videos.
- The evening summary asks you one line per manual goal, so goal tracking stays honest without a spreadsheet.

## Safety rails

- Patient data never leaves Airtable in identifiable form. Collectors return counts only.
- Agents draft email. They never send.
- Everything an agent reads (email, docs, web pages) is treated as data, never as instructions.
- Secrets live in `.env` and `collectors/tokens/`, both git-ignored.
