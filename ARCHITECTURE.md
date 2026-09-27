# Architecture

## The decisions and why

**One JSON file is the spine.** `dashboard/data/today.json` is written by the
collectors, refined by Hermes, and read by the dashboard. Its shape is fixed in
`dashboard/data/schema.json`. Anything that wants to show up on the page or in a
brief has to land in that file. That keeps every part replaceable: swap Asana
for another task tool and only one collector changes.

**Collectors are dumb, agents are smart.** The Python collectors fetch facts and
never interpret them. Hermes reads the facts and decides what matters. That
split means a collector failure is visible (a grey dot in the sources footer)
and a bad judgment is fixable by editing a skill, not code.

**Files over databases for the brain.** Markdown with frontmatter in git. You
can read it, grep it, move it, back it up, and no vendor holds it. A sqlite
full-text index sits beside it for speed and is rebuilt from the files at any
time.

**Hermes for the agents.** Hermes gives us scheduled jobs with Telegram
delivery, persistent memory, skills that edit themselves after use, and
`delegate_task` for parallel specialists. We keep every Hermes-specific file in
`hermes/` and symlink into `~/.hermes`, so this repo stays the source of truth.

**Static dashboard on Vercel.** No server, no auth to maintain. The morning job
commits the new JSON and pushes; Vercel redeploys in under a minute. If you want
it private, turn on Vercel password protection for the project.

## Data flow, step by step

1. 6:00 AM. Hermes cron fires `morning-brief` in `$JARVIS_HOME`.
2. The skill runs `python -m collectors.build_today`. Each collector runs in
   isolation. Gmail and Calendar iterate every account in `accounts.yaml`.
   Airtable computes ATWC waitlist and QCA WIP aggregates. Asana pulls tasks.
   `goals.py` scores anything it can compute. `brain_stats.py` counts the library.
3. The file is validated and written. Yesterday's `content` and `yesterday`
   blocks carry forward so nothing the evening job wrote is lost.
4. Hermes reads the file, searches the brain for context on today's meetings,
   writes `headline` and `day_shape`, composes the brief, sends it to Telegram,
   commits and pushes.
5. Hourly, `dashboard-refresh` reruns the collectors so the page stays current.
6. 7:00 PM. `evening-summary` reruns collectors, compares with the morning
   list, writes the `yesterday` block, asks about manual goals, sweeps the
   brain inbox, files decision notes, pushes.
7. Mon, Wed, Fri at noon, `content-scout` searches, scores, writes up to five
   recommendations into `content`, files the top two into the brain, and sends
   the top three.

## Multi-account email and calendar

One Google Cloud OAuth client. Each account signs in once and gets its own
token file. `accounts.yaml` tags each account with a business, and that tag
follows every event and thread into the JSON and onto the page. Adding an
account is one yaml entry and one sign-in.

Non-Google accounts (Outlook, iCloud) are not wired yet. The collector
interface is the same (`collect(account) -> dict`), so an Outlook collector via
Microsoft Graph would slot in next to `gmail.py`.

## The agent team

The chief of staff is a Hermes skill, not a separate process. It reads a
request, picks specialists, and calls `delegate_task` in batch mode with one
task per specialist. Each specialist is a playbook file in
`hermes/skills/agents/`. Because Hermes subagents start with no memory, the
chief of staff pastes the playbook and the relevant data into each task's
context. Results come back as self-reports, so the chief of staff verifies any
side effect before telling you it happened.

Specialists: inbox and calendar EA, ATWC ops, QCA ops, marketing and content,
finance and metrics, research and brain.

## What is not built yet

- Outlook or iCloud collectors.
- Instagram and TikTok scanning. Both block automated access; the scout uses
  YouTube, X search, and the web, and you can drop links you find into the
  brain inbox for it to study.
- A write path from the dashboard back to Asana or Airtable. The page is
  read-only on purpose for now.
