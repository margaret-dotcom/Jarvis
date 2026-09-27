# Setup

Plan on about an hour the first time. Everything after that is a yaml edit.

## 1. The machine

Jarvis needs one always-on machine for Hermes and the collectors: a small
cloud VM, a Mac mini in the office, or a Raspberry Pi. Hermes also runs on
Modal or Daytona if you would rather not manage a box. The dashboard itself
runs on Vercel and needs nothing from you once deployed. DOM OS keeps running
on Vercel and Supabase as it does today; Jarvis only reads from it.

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

Node.js also has to be on the machine, because the WhatsApp bridge in step 6
is a small Node program. `node --version` should print a version.

## 2. Google accounts (email and calendar)

One OAuth client covers every account. The three accounts are:

- stochmt@gmail.com, personal
- margaret@mytherapywellness.com, ATWC
- margarets@qcaroofing.com, QCA

1. Go to console.cloud.google.com, create a project called Jarvis.
2. APIs and Services, Library: enable Gmail API, Google Calendar API, and
   YouTube Data API v3.
3. APIs and Services, OAuth consent screen: External, add all three addresses
   as test users. Scopes: gmail.readonly, calendar.readonly.
4. Credentials, Create credentials, OAuth client ID, type Desktop app. Download
   the JSON and save it as `collectors/credentials/client_secret.json`.
5. Credentials, Create credentials, API key. Restrict it to YouTube Data API
   and put it in `.env` as `YOUTUBE_API_KEY`.

Then list your accounts:

```
cp collectors/accounts.yaml.example collectors/accounts.yaml
```

The example already lists the three addresses with their business tags. Run
`make auth`. A browser window opens once per account. Sign in with that
account. Tokens land in `collectors/tokens/` and refresh themselves.

Adding an account later: add it to `accounts.yaml`, run `make auth` again.

## 3. DOM OS access

The collectors read DOM OS straight from its Supabase database. Row level
security is on, so they need the service role key, and they use it for
SELECT only.

1. Open the Supabase dashboard for the DOM OS project, then Project Settings,
   API.
2. Copy the Project URL into `.env` as `SUPABASE_URL`. It is
   https://kgeynjpcqwgfnaudycht.supabase.co.
3. Copy the service_role key (under Project API keys, click Reveal) into
   `.env` as `SUPABASE_SERVICE_ROLE_KEY`. Treat it like a password. It never
   leaves this machine and nothing in Jarvis prints it.
4. Set `DOMOS_OWNER_EMAIL=margaret@mytherapywellness.com`, the identity your
   tasks, notifications and events are filed under in DOM OS.
5. Set `DOMOS_URL` to the address of your DOM OS app, so links in the brief
   and on the dashboard open the right page.

Check it: `python -m collectors.domos --query open_tasks` prints your open
tasks as JSON. `python -m brain.domos_sync` mirrors the knowledge base into
`brain/library/domos/` and prints one line with the counts.

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
of the shape are in the file. When you build your goals in DOM OS later, this
file goes away and the briefs read `growth_goals` instead.

## 6. Hermes and WhatsApp

```
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc
hermes model          # pick your provider and model
make hermes           # installs persona, skills, config, and cron jobs
hermes whatsapp       # shows a QR code; scan it from WhatsApp, Linked devices
hermes gateway        # starts the bridge and the scheduler
```

`hermes whatsapp` writes `WHATSAPP_ENABLED=true` into `~/.hermes/.env`. The
default mode is self-chat: Jarvis talks to you in your own "message yourself"
chat in WhatsApp, and nobody else can reach it. If you ever want another number
allowed, add it to `WHATSAPP_ALLOWED_USERS` in the same file.

If you would rather not link your personal phone, `hermes whatsapp-cloud`
uses the WhatsApp Business Cloud API instead. That needs a Meta developer app
and a business number.

Test a job by hand before waiting for the schedule:

```
hermes cron list
hermes cron run <job id from the list>
```

Output is saved under `~/.hermes/cron/output/` and delivered to WhatsApp.

## 7. Dashboard on Vercel

1. vercel.com, Add New Project, import `margaret-dotcom/Jarvis`.
2. Root Directory: `dashboard`. Framework: Other. No build command.
3. Deploy. Under Settings, Deployment Protection, turn on password or
   Vercel Authentication so the page is yours alone.

Every push to `dashboard/data/today.json` redeploys the page. The morning
job does that push. For that to work, the Hermes machine needs push access to
the repo: `gh auth login` or an SSH key added to your GitHub account.

## 8. Daily use

- Morning brief arrives on WhatsApp at 6:00 on weekdays, 7:30 on weekends.
- Evening summary at 7:00 PM. Answer its four review questions (went well, did not, one lesson, one thanks) and any goal questions in one line each, right there in the chat.
- Weekly review arrives Sunday at 5:00 PM.
- Tasks, notifications, EOW reports and the numbers stay in DOM OS. Jarvis shows them to you; you act on them in DOM OS.
- Drop anything into `brain/inbox/` or tell Jarvis on WhatsApp "remember this". What you write in the DOM OS knowledge base is mirrored into the brain every night at 9.
- Ask Jarvis anything about either business. It searches the brain first, DOM OS second.
- Weekly: `git status` in the repo, look at what Hermes changed in
  `hermes/skills/`, commit what you like.

## Troubleshooting

- A source shows error: run `python -m collectors.build_today --only gmail`
  (or whichever) and read the message.
- A `domos_*` source shows error: check that `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY` are in `.env` and that the key is the
  service_role one, not anon. Then `python -m collectors.domos --query
  open_tasks` shows the raw response.
- Token expired: delete `collectors/tokens/<email>.token.json`, run `make auth`.
- Brief did not arrive: `hermes cron status`, then `hermes cron runs`. If the
  gateway log says the WhatsApp bridge is not connected, run `hermes whatsapp`
  again and rescan the QR code; a linked device drops after long offline
  stretches.
- Dashboard stale: check the last commit time on `dashboard/data/today.json`
  and the Vercel deployment log.
