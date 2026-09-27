# hermes/

The Hermes Agent layer of Jarvis. Hermes Agent (Nous Research, v0.19) is the runtime: it holds the model, the tools, the Telegram gateway, the cron scheduler, and the memory. This folder gives it a persona, a set of skills, and a schedule. Everything else in the repo (collectors, brain, dashboard) is plain Python that the skills call.

## How the pieces fit

| Piece | File | What it does |
|---|---|---|
| Persona | `SOUL.md` | Who Jarvis is. Loaded fresh on every message. Under 40 lines. |
| Shared context | `skills/jarvis-core/SKILL.md` | Repo layout, commands, the two businesses, HIPAA rule, writing rules, how to read and write today.json. Every job loads it first. Ships `scripts/write_today.py`, the only sanctioned way to write agent fields into today.json. |
| Jobs | `skills/morning-brief`, `evening-summary`, `dashboard-refresh`, `content-scout`, `brain-ingest`, `weekly-review` | One skill per scheduled job. Each is a checklist the agent follows top to bottom. |
| Recall | `skills/brain-recall` | Search the brain before answering a business question, cite the path. |
| Delegation | `skills/chief-of-staff` plus `skills/agents/*` | The chief of staff splits a multi-part request and fans it out with `delegate_task` to six specialists: ea-inbox-calendar, atwc-ops, qca-ops, marketing-content, finance-metrics, research-brain. Each specialist is a full playbook that gets pasted into the subagent's context, because subagents start with no memory. |
| Schedule | `cron/jobs.yaml` | The jobs, their cron expressions, skills, prompts, delivery. install.sh registers them. |
| Config | `config.snippet.yaml` | Timezone, delegation limits, cron delivery settings, a commented MCP example. Merged into Hermes config.yaml by install.sh. |
| Installer | `install.sh` | Symlinks, persona, config merge, cron registration. Idempotent. |

Data flow on a weekday: at 6:00 the morning brief runs `python -m collectors.build_today`, reads `dashboard/data/today.json`, writes `headline` and `day_shape` back through `write_today.py`, commits and pushes so the dashboard updates, and sends the brief to Telegram. Hourly from 8 to 18 the refresh job re-pulls and pushes silently. At 12:00 Monday, Wednesday, Friday the scout fills `content.recommendations`. At 19:00 the evening summary writes `yesterday`, asks for manual goal numbers and the four review prompts, files the brain inbox, and pushes. At 21:00 the sweep files anything still in `brain/inbox`. Sunday at 17:00 the weekly review reads the week and writes a note into `brain/library/decisions`.

## Install

Prerequisites: Hermes Agent installed (`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`), Python 3.10 or newer with `pip install -r requirements.txt` done at the repo root, and a filled `.env` (copy `.env.example`; at minimum `JARVIS_HOME` and `JARVIS_TZ`).

```bash
bash hermes/install.sh
```

What it does, in order:

1. Symlinks every skill directory under `hermes/skills/` and `hermes/skills/agents/` into `~/.hermes/skills/<name>`. Symlinks, not copies, so an edit in the repo is live immediately. An existing real directory with the same name is left alone and reported.
2. Copies `SOUL.md` to `~/.hermes/SOUL.md` if none exists or the existing one is the stock Hermes text. Otherwise it prints a diff and asks. `--yes` answers yes.
3. Deep-merges `config.snippet.yaml` into `~/.hermes/config.yaml`, with `timezone` set from `JARVIS_TZ`. Backs the file up first. Never deletes a key.
4. Registers each job in `cron/jobs.yaml` with `hermes cron create`, skipping names that already exist in `hermes cron list`.
5. Prints the next steps: `hermes model`, `hermes gateway` for Telegram pairing, `python -m collectors.google_auth`.

Flags: `--skip-cron` does steps 1 to 3 without needing `hermes` on PATH. `--profile NAME` targets a Hermes profile (see below). `--uninstall` removes the symlinks it made and the cron jobs by name, and leaves SOUL.md and config.yaml alone.

Rerunning is safe. It reports `ok` or `exists` for anything already done.

Hermes logs a warning when it loads a skill whose real path is outside `~/.hermes/skills`, which is what a symlink into the repo looks like. The skill still loads. The Hermes source treats symlinked skill directories as a supported layout.

### A separate profile named jarvis

If Hermes is already in use for other things, keep Jarvis apart:

```bash
hermes profile create jarvis --clone
bash hermes/install.sh --profile jarvis
```

`--clone` copies the active profile's config.yaml, .env, SOUL.md, and skills into `~/.hermes/profiles/jarvis/`. From then on everything for Jarvis lives there: its own `config.yaml`, `SOUL.md`, `skills/`, `memories/`, `cron/jobs.json`, and its own gateway. Every command gains `-p jarvis`: `hermes -p jarvis model`, `hermes -p jarvis gateway`, `hermes -p jarvis cron list`. The installer's next-steps output includes the flag when you used `--profile`. Skip the profile if Hermes on this machine exists only for Jarvis.

## Test a job by hand

List the jobs and their ids, then trigger one. It fires on the scheduler's next tick, so the gateway has to be running (`hermes gateway`, or `hermes gateway install` to run it as a service).

```bash
hermes cron list
hermes cron run <job_id>
hermes cron runs <job_id>
```

The output lands in `~/.hermes/cron/output/<job_id>/<timestamp>.md` whether or not delivery worked, so you can read a brief before Telegram is paired.

To run a skill in chat without cron, start `hermes` (or `hermes -p jarvis`) and type `/morning-brief` or "run the morning brief". The same skill loads, the same steps run, and the output shows in the terminal instead of Telegram. That is the fastest way to check a change to a SKILL.md.

To check the write-back path alone:

```bash
python -m collectors.build_today
python hermes/skills/jarvis-core/scripts/write_today.py --check
```

## Talk to Jarvis on Telegram

1. Create a bot with Telegram's BotFather and copy the token.
2. Run `hermes gateway` and follow the setup prompt for Telegram; it stores the token in `~/.hermes/.env` and pairs your chat as the home channel.
3. Message the bot. Jarvis answers as the persona in SOUL.md with every skill available.

Cron deliveries go to that home channel. `config.snippet.yaml` turns on `cron.mirror_delivery`, so when you reply to a brief, Jarvis has the brief in context. Things you can say:

- "Remember this: ..." or "Note for QCA: ..." files a note (brain-ingest).
- "What did we decide about deposits?" searches the brain first and cites the note (brain-recall).
- A number in reply to a goal question, or four lines in reply to the evening review prompts, gets saved (evening-summary explains where).
- "Prep me for tomorrow and pull the WIP flags" runs the chief of staff, which fans out to specialists and comes back with one answer and a "decisions needed from you" list.

## Skills improve themselves

Hermes tells the agent to patch a skill when it finds a step wrong or missing, using its `skill_manage` tool. Every Jarvis SKILL.md ends with a short "After the run" section pointing at that. Because the skills are symlinked, those patches land in `hermes/skills/` inside this repo as uncommitted changes.

Once a week, look at what changed and commit it:

```bash
cd "$JARVIS_HOME"
git status hermes/skills
git diff hermes/skills
git add hermes/skills && git commit -m "skills: weekly patches" && git push
```

Revert anything that reads wrong. The writing rules in jarvis-core apply to patches too; a quick check for stray dashes is `LC_ALL=C.UTF-8 grep -rnP '\x{2014}|\x{2013}' hermes/`.

## Where things live

| What | Where |
|---|---|
| Cron job definitions (live) | `~/.hermes/cron/jobs.json` |
| Cron output, one file per run | `~/.hermes/cron/output/<job_id>/<timestamp>.md` |
| Hermes config, secrets, persona | `~/.hermes/config.yaml`, `~/.hermes/.env`, `~/.hermes/SOUL.md` |
| Memory Jarvis keeps across sessions | `~/.hermes/memories/MEMORY.md`, `USER.md` |
| Installed skills (symlinks) | `~/.hermes/skills/<name>` pointing into `hermes/skills/` here |
| Dashboard data | `dashboard/data/today.json`, daily copies in `dashboard/data/history/` (local only) |
| Brain | `brain/library/<area>/`, index in `brain/INDEX.md`, daily reviews in `brain/library/personal/`, weekly reviews in `brain/library/decisions/` |
| Logs | `hermes logs` |

For a profile, replace `~/.hermes` with `~/.hermes/profiles/<name>`.

## Changing the schedule

Edit `cron/jobs.yaml`. New job names are added on the next `bash hermes/install.sh`. To change an existing job: `hermes cron edit <job_id> --schedule "0 7 * * 1-5"`, or remove it and rerun the installer. Schedules follow the `timezone` in Hermes config.yaml, which the installer sets from `JARVIS_TZ`.
