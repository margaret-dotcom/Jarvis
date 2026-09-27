# Dashboard

One static page that shows your day. No build step, no framework, no server code.

## How it works

Two files do all the work.

- `index.html` is the page. All the CSS and JavaScript live inside it. When it opens, it fetches `data/today.json` from the folder next to it, draws every section it finds data for, and skips any section that is empty. It fetches again every 15 minutes and every time you come back to the tab. The Refresh link at the bottom does the same thing by hand.
- `data/today.json` is the data. The collectors write it once each morning. `data/schema.json` is the contract for what goes in it. Anything that writes today.json must produce a file that validates against the schema.

The page never talks to Google or DOM OS itself. It only reads the JSON. That keeps the page safe to host anywhere and keeps every credential, including the Supabase service key, on the machine that runs the collectors.

Tasks on the page come from DOM OS (`tasks.source` is `domos`). The "DOM OS inbox" card shows open notifications and the end of week reports waiting on your reply. Each item links into DOM OS when today.json carries a `domos_url`; the collectors fill that from `DOMOS_URL` in `.env`.

Times are shown in the `timezone` named inside today.json (the collectors set this from `JARVIS_TZ`, default America/Chicago).

## View it on your machine

From the `dashboard/` folder:

```
python -m http.server 8765
```

Then open http://localhost:8765/ in a browser. Any static file server works; that one ships with Python.

Opening `index.html` straight from disk (double clicking it) works in Firefox. Chrome and Safari block a page loaded from `file://` from fetching a JSON file next to it, so on those you will see the "Nothing to show yet" line even when today.json is there. Use the one-line server above instead.

## Deploy to Vercel

1. In Vercel, click Add New, then Project, and import the Jarvis repo from GitHub.
2. Set Root Directory to `dashboard`.
3. Set Framework Preset to Other. Leave Build Command and Output Directory blank.
4. Deploy.

`vercel.json` in this folder turns on clean URLs and sends `Cache-Control: no-cache` for `data/today.json`, so the browser always checks for a fresh copy before reusing one.

The page is public at whatever URL Vercel gives you. The JSON holds your calendar, email subjects, task titles, DOM OS notifications, and business numbers, so if you want it private, turn on Vercel's deployment protection for the project or put it behind your own login. The collectors never put patient names or contact details in the file, only counts.

## How the morning job updates it

1. The morning job runs the collectors (`python -m collectors.build_today` from the repo root). They read your accounts and write `dashboard/data/today.json`.
2. The Hermes morning brief job reads the same file, adds the headline and the yesterday summary, and writes it back.
3. The job commits and pushes `dashboard/data/today.json`.
4. Vercel sees the push and redeploys. That takes under a minute for a static site.
5. The page you have open refetches on its own timer or when you switch back to the tab, and shows the new numbers.

If today.json is missing or fails to load, the page shows one line telling you what to run.

## Editing the page

Everything is in `index.html`. Colors are CSS custom properties at the top of the style block, with a dark set that switches on with your system's dark mode. Each section is one `render` function in the script. Every piece of text is inserted with `textContent`, never `innerHTML`, so nothing in the JSON can run as code on the page. Keep it that way if you add a section.
