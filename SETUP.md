# Setup

Plan on about an hour the first time. Everything after that is a yaml edit.

## 1. The machine

Jarvis needs one always-on machine for Hermes and the collectors: a small
cloud VM, a Mac mini in the office, or a Raspberry Pi. Hermes also runs on
Modal or Daytona if you would rather not manage a box. The dashboard itself
runs on Vercel and needs nothing from you once deployed.

On that machine:

```
git clone https://github.com/margaret-dotcom/Jarvis.git
cd Jarvis
python3 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env
```

Edit `.env`. Set `JARVIS_HOME` to the full path of this folder and `JARVIS_TZ`
to your timezone.

## 2. Google accounts (email and calendar)

One OAuth client covers every account.

1. Go to console.cloud.google.com, create a project called Jarvis.
2. APIs and Services, Library: enable Gmail API, Google Calendar API, and
   YouTube Data API v3.
3. APIs and Services, OAuth consent screen: External, add yourself and every
   other email you will connect as test users. Scopes: gmail.readonly,
   calendar.readonly.
4. Credentials, Create credentials, OAuth client ID, type Desktop app. Download
   the JSON and save it as `collectors/credentials/client_secret.json`.
5. Credentials, Create credentials, API key. Restrict it to YouTube Data API
   and put it in `.env` as `YOUTUBE_API_KEY`.

Then list your accounts:

```
cp collectors/accounts.yaml.example collectors/accounts.yaml
```

Add one entry per email address with its business tag. Run `make auth`. A
browser window opens once per account. Sign in with that account. Tokens land
in `collectors/tokens/` and refresh themselves.

Adding an account later: add it to `accounts.yaml`, run `make auth` again.

## 3. Asana and Airtable

- Asana: app.asana.com, My Settings, Apps, Developer apps, Create personal
  access token. Paste into `.env` as `ASANA_TOKEN`.
- Airtable: airtable.com/create/tokens. Scopes `data.records:read` and
  `schema.bases:read`. Access: ATWC Ops and Permit Tracker. Paste as
  `AIRTABLE_TOKEN`. The base and table ids are already in `.env.example`.

## 4. First collection

```
make collect
make dashboard
```

Open http://localhost:8765. The footer shows every source as ok, error, or
not configured, with the reason. Fix anything red before moving on.

## 5. Goals

Open `goals/goals.yaml` and write three to six real goals with real numbers.
The briefs measure you against this file every morning and evening. Examples
of the shape are in the file.

## 6. Hermes

```
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc
hermes model          # pick your provider and model
make hermes           # installs persona, skills, config, and cron jobs
hermes gateway        # pair Telegram; follow the prompts
```

Test a job by hand before waiting for the schedule:

```
hermes cron list
hermes cron run <job id from the list>
```

Output is saved under `~/.hermes/cron/output/` and delivered to Telegram.

## 7. Dashboard on Vercel

1. vercel.com, Add New Project, import `margaret-dotcom/Jarvis`.
2. Root Directory: `dashboard`. Framework: Other. No build command.
3. Deploy. Under Settings, Deployment Protection, turn on password or
   Vercel Authentication so the page is yours alone.

Every push to `dashboard/data/today.json` redeploys the page. The morning
job does that push. For that to work, the Hermes machine needs push access to
the repo: `gh auth login` or an SSH key added to your GitHub account.

## 8. Daily use

- Morning brief arrives at 6:00 on weekdays, 7:30 on weekends.
- Evening summary at 7:00 PM. Answer its goal questions in one line each.
- Drop anything into `brain/inbox/` or tell Jarvis on Telegram "remember this".
- Ask Jarvis anything about either business. It searches the brain first.
- Weekly: `git status` in the repo, look at what Hermes changed in
  `hermes/skills/`, commit what you like.

## Troubleshooting

- A source shows error: run `python -m collectors.build_today --only gmail`
  (or whichever) and read the message.
- Token expired: delete `collectors/tokens/<email>.token.json`, run `make auth`.
- Brief did not arrive: `hermes cron status`, then `hermes cron runs`.
- Dashboard stale: check the last commit time on `dashboard/data/today.json`
  and the Vercel deployment log.
