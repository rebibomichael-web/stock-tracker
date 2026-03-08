# Stock Tracker — Cloud Deployment Guide

## Files in this folder
- `app.py` — the full web app (Flask)
- `requirements.txt` — Python packages needed
- `render.yaml` — Render deployment config

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
