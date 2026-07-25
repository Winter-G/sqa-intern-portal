# sqa-intern-portal

Your personal, always-on QA/SQA internship tracker. It watches job boards for
new remote QA/SQA intern postings, scores each one against your CV, and lets
you track your application status — all for free, with no server to run.

## What it actually does (read this first)

- **Checks for new postings every hour** via a free GitHub Actions job. This
  is the closest thing to "instant" that's realistically free — no job board
  offers a live push feed, so hourly polling is the trade-off. You can change
  the frequency (see below).
- **Scores every job against your CV** using keyword/skill overlap — no API
  key, no cost, runs entirely in your browser. It also tells you exactly
  which skills are missing from your CV for that specific posting.
- **"Apply" opens a cover-letter modal**, then hands you off to the real
  posting to finish submitting — no job board lets outside tools submit
  applications on your behalf, so this gets you as close as possible.
- **Everything (CV text, applied/rejected/etc. status) lives in your
  browser's local storage** — it's a static site with no backend, so nothing
  is uploaded anywhere. It's tied to one browser; it won't sync across
  devices unless you extend it with a small database later.

## One-time setup (15 minutes)

1. **Create a GitHub repo** named `sqa-intern-portal` (public or private —
   private repos work fine with GitHub Pages too as long as you're on a plan
   that supports it, otherwise make it public).
2. **Push everything in this folder** to that repo:
   ```bash
   cd sqa-intern-portal
   git init
   git add .
   git commit -m "init: sqa intern portal"
   git branch -M main
   git remote add origin https://github.com/<your-username>/sqa-intern-portal.git
   git push -u origin main
   ```
3. **Turn on GitHub Pages**: repo → Settings → Pages → Source → "GitHub
   Actions" isn't needed for the site itself, just set Source to
   "Deploy from a branch", branch `main`, folder `/ (root)`. Save.
   Your portal will be live at `https://<your-username>.github.io/sqa-intern-portal/`
   within a minute or two.
4. **Run the scraper once manually** so `jobs.json` has data: repo → Actions
   tab → "Scrape SQA Intern Jobs" → Run workflow. Wait ~30 seconds, then
   refresh your portal URL.
5. That's it — it'll now check hourly on its own.

## Optional: instant Discord/Telegram alerts

The scraper will ping you the moment a new match is found, if you set either
of these as **repo secrets** (Settings → Secrets and variables → Actions):

- `DISCORD_WEBHOOK_URL` — a Discord channel webhook URL
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — a bot token from @BotFather
  and your chat id

Leave them unset if you don't want alerts — the scraper skips this silently.

## Changing the check frequency

Edit `.github/workflows/scraper.yml`, the `cron` line. Examples:
- Every 15 min: `"*/15 * * * *"` (uses more of your free Actions minutes)
- Every 6 hours (lightest): `"0 */6 * * *"`

GitHub's free tier gives public repos unlimited Action minutes for scheduled
jobs like this; private repos get 2,000 free minutes/month, and an hourly
1-minute job comfortably fits inside that.

## If a source stops returning jobs

`ITPro.lk` (RSS), `RemoteOK` and `Arbeitnow` (public JSON APIs) are stable —
they're not scraped HTML, so they shouldn't break.

`DevJobs.lk` and `TopJobs.lk` don't publish an API or RSS feed, so
`scraper.py` scrapes their HTML with best-effort selectors. If either of
those two ever returns 0 jobs, that's the first thing to check — the site
likely changed its layout. Open `scraper.py`, find `scrape_devjobs()` /
`scrape_topjobs()`, and adjust the regex/selectors to match the new HTML
(view page source in your browser to see what changed).

## Tuning the "is this a QA intern role" filter

`scraper.py` has two regexes near the top: `ROLE_LEVEL_PATTERN` (intern,
trainee, junior, etc.) and `QA_DOMAIN_PATTERN` (QA, testing, SQA, etc.). If
you're getting too many irrelevant results, tighten these; if you're missing
roles you know exist, loosen them.

## Tuning the CV match score

`index.html` has a `QA_SKILLS` array near the top of the `<script>` block —
add or remove skills/tools/keywords there to change what the matcher looks
for.

## Local development

You don't need Node or a build step — `index.html` loads React/Tailwind from
a CDN. To preview locally:
```bash
python3 -m http.server 8000
```
then open `http://localhost:8000`. (`jobs.json` needs to exist — run
`python scraper.py` once locally, or copy one from a real run.)
