# Stock Tracker — Cloud Deployment Guide

## Files in this folder
- `app.py` — the full web app (Flask)
- `requirements.txt` — Python packages needed
- `render.yaml` — Render deployment config
- `swing/module.py` — web module for a future **Swing tab** (not yet wired into
  app.py). Reads the desktop swing platform's signal log
  (`~/.michael_swing_signals.json`), so it only shows data when the app runs
  locally; on Render it returns nothing.
- `journal/module.py` — web module for a future **Journal tab** (not yet wired
  into app.py). Depends on `journal/fidelity.py` (the Fidelity CSV parser,
  to be lifted from the desktop `trade_journal.py`) which is **not in this repo
  yet** — until it lands, the module safely returns no rows. Like the swing
  module, it reads local files, so it's local-run only.

These two modules were recovered on 2026-07-02 from the trading project's
download triage (they had been misfiled there by a sync script; see
`trading-src`'s `PROJECT_MAP.md`). Neither is imported by `app.py`, so they
have zero effect on the deployed Render app.

## How to deploy on Render (free)

### Step 1 — Create a GitHub account
Go to https://github.com and sign up (free).

### Step 2 — Create a new repository
1. Click the + icon → "New repository"
2. Name it: `stock-tracker`
3. Set to **Public**
4. Click "Create repository"

### Step 3 — Upload these 3 files
On the new repo page click "uploading an existing file"
Upload: `app.py`, `requirements.txt`, `render.yaml`
Click "Commit changes"

### Step 4 — Deploy on Render
1. Go to https://render.com and sign up (free)
2. Click "New" → "Web Service"
3. Connect your GitHub account
4. Select your `stock-tracker` repo
5. Render will auto-detect the settings from render.yaml
6. Click "Create Web Service"

### Step 5 — Get your URL
After ~3 minutes Render gives you a URL like:
`https://stock-tracker-xxxx.onrender.com`

Open that URL on your phone or any browser — done!

## Notes
- Free tier sleeps after 15 min inactivity — wakes in ~30 seconds
- Data refreshes every 30 minutes automatically
- All 16 tickers included
- Both tabs: Stock Tracker + LEAP Scanner
