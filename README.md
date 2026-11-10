# 🇮🇸 Chicago ⇄ Iceland Fare Tracker

A free bot that checks round-trip fares from O'Hare (ORD) to Reykjavik (KEF) on
**United and Icelandair** for **every weekend from now through May 2027**, and
builds a website showing how every trip's price moves. Every price is labeled
with its airline.

**Trips tracked each week:**
- Wed→Sun, Wed→Mon, Thu→Sun, Thu→Mon (any time of day)
- Fri→Mon, only if the Friday flight leaves **before noon** and the Monday flight
  **lands at ORD at 6 PM or later**

**How often:** the next 10 weekends every hour; weekends further out every
3 hours (keeps the load on Google reasonable; ~50–145 searches per run).

**Search settings:** regular Economy (no Basic, matching Chase Travel) · nonstop
or 1 stop · 1 adult.

> United's ORD–KEF nonstop is seasonal (3×/week in winter: leaves ORD Wed/Fri/Sun,
> returns Mon/Thu/Sat, paused after Jan 3). Icelandair flies the route year-round.
> Nonstops to Iceland leave ORD in the evening, so Friday-morning trips will
> usually show "No flights".

## What you get

- **Website** with:
  - a grid of every weekend × date combo with today's price (tap one to dig in)
  - price-over-time chart (cheapest trip + the one you tapped)
  - United flights for the selected trip
  - a day × hour grid of every check, showing missed hours too
  - best time of day / day of week to buy
  - full check log and CSV download
- **REPORT.md** with the same grid in text form.
- **Email alerts** (GitHub issue → email) when:
  - a trip drops **15%+ below its own typical price**
  - the cheapest trip hits a **new all-time low**
  - a trip drops to your **target price** (set `TARGET_PRICE` in `config.py`)

**Cost: $0.**

---

## Setup (about 10 minutes)

### 1. Create a new **public** repository
**+** → **New repository** → name it `icelandtracker` → **Public** → **Create**.
(Public is needed for the free website.)

### 2. Upload the files
Unzip `iceland-tracker.zip`, open the folder, and drag everything inside into
**Add file → Upload files**: `analyze.py`, `config.py`, `tracker.py`,
`dashboard_template.html`, `requirements.txt`, `README.md`, and the `docs` folder.
**Commit changes**.

### 3. Add the workflow file
**Add file → Create new file**, name it exactly `.github/workflows/track.yml`, and
paste in the contents of that file (it's in the unzipped folder under
`.github/workflows/`). **Commit**.

### 4. Let the bot save data
**Settings → Actions → General → Workflow permissions → Read and write → Save.**

### 5. Run it once
**Actions → Track Iceland fares → Run workflow.** Takes ~2 minutes.

### 6. Turn on the website
**Settings → Pages** → Deploy from a branch → **main** / **/docs** → **Save**.
Your link: `https://YOUR-USERNAME.github.io/icelandtracker/`

### 7. Run it every hour with cron-job.org
This bot has **no GitHub schedule** (GitHub skips too many hourly runs), so
cron-job.org starts it.

1. **Let your existing GitHub key reach this repo:**
   [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens)
   → your token → **Edit** → Repository access → add `icelandtracker` → save.
   (Actions must still be **Read and write**.)
2. **cron-job.org → Create cronjob** (or clone your Dallas job):
   - Title: `IcelandTracker`
   - URL: `https://api.github.com/repos/YOUR-USERNAME/icelandtracker/actions/workflows/track.yml/dispatches`
   - Schedule: Custom, crontab `37 * * * *` (minute 37 of every hour)
   - Advanced: POST, body `{"ref":"main"}`, headers `Content-Type: application/json`,
     `Accept: application/vnd.github+json`, `Authorization: Bearer YOUR-TOKEN`,
     `X-GitHub-Api-Version: 2022-11-28`
   - **Test run** → expect a code starting with 2 → **Create**.

---

## Changing settings (`config.py`)

- `TARGET_PRICE = 600`: also email when any trip is $600 or less
- `TRIPS`: which day combos to check. Remove `leave_before` from the Friday line
  to allow Friday departures at any time.
- `AIRLINES`: delete the Icelandair line to go back to United only
- `TRACK_UNTIL`: last departure date to track
- `HOURLY_WEEKENDS` / `FAR_EVERY_HOURS`: how often near vs. far weekends are checked
- `MAX_STOPS = 0`: nonstop only
- `DEAL_THRESHOLD`: `0.15` = email when 15% below typical

## Good to know

- Prices come from Google Flights, not Chase. United cash fares should match Chase
  Travel closely; always confirm before booking.
- The "Leaves ORD" times on the website are the outbound flight. Google shows
  return-flight options only after an outbound is picked.
- Give it about a week before trusting the time-of-day / day-of-week patterns.

## Files

| File | What it does |
|---|---|
| `tracker.py` | Searches every trip, saves to `data/prices-YYYY-MM-DD.csv` |
| `safe_parse.py` | Skips incomplete Google listings instead of crashing |
| `analyze.py` | Builds `REPORT.md`, the website in `docs/`, and alerts |
| `dashboard_template.html` | Website design (data is filled in each run) |
| `config.py` | Trip settings |
| `.github/workflows/track.yml` | The job cron-job.org starts every hour |
