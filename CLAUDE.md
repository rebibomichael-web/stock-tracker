# CLAUDE.md

Standing instructions for every session working in `stock-tracker`.

## The four-repo system

This repo is one fragment of a single trading platform spread across four
repos:

- `trading-src` (private) — canonical trading code + planning docs; an
  hourly-synced **mirror** of the owner's Dell PC. Code edits made on GitHub
  get overwritten by the next sync.
- `trading-data` (private) — machine-generated data backups (nightly scans,
  swing state, Fidelity journal CSVs). Never hand-edit; contains sensitive
  brokerage data.
- `trading-suite` (public) — the consolidated Flask app on Render and the
  migration target. GitHub-native: edit and push there freely.
- `stock-tracker` (public, this repo) — the legacy Render app + journal
  dashboard; slated for retirement into trading-suite.

Canonical map, data flow, duplication list, and traps:
`trading-suite/docs/SYSTEM_MAP.md`.

## What this repo is

The **legacy** deployed dashboard. Before adding features here, check whether
they belong in `trading-suite` instead — retiring this repo is an open
roadmap item there. Two loosely-coupled subprojects live here:

- **`app.py`** — the deployed Flask app ("Stock Tracker + LEAP Scanner"):
  16-ticker tracker, S3..R3 pivots via yfinance, Barchart opinion scraping.
  Deployed on Render per `render.yaml` (`gunicorn app:app`, Python 3.11).
  Single file, HTML inlined via `render_template_string`. `README.md`
  documents only this three-file deployment and predates everything below.
- **`journal_dashboard/`** — swing-trading journal dashboard:
  `journal_dashboard.html` (static UI), `build_journal_data.py` (the data
  adapter), `journal_dash_server.py` (LAN server; the live instance is served
  from the Dell over Tailscale). The adapter reads scan artifacts from the
  owner's home directory (`~/Desktop/swing_project`, `~/.michael_*`,
  `~/trading-src/journal`), so it **cannot run fully from a clone** — in
  sessions use `--selftest` (pure-function tests) or `--offline`.
  `journal_data.js` is generated output, not source.
- **`docs/SWING_AUDIT_2026-07-06.md`** — cross-repo audit spanning
  trading-src, trading-data, and this repo; good orientation for swing work.
- **`mockups/`** — six frozen historical design mockups; never edit.

## Run / test

- `pip install -r requirements.txt` (Python 3.11)
- `python app.py` — port from `PORT` (default 10000); needs outbound network
  (yfinance + barchart.com), so it is fragile in restricted sessions.
- `python3 journal_dashboard/build_journal_data.py --selftest` — the only
  self-contained test in the repo. There is no other test suite, CI, or lint.

## Traps

- The 16-ticker watchlist is hardcoded twice here (`app.py` `TICKERS`,
  `build_journal_data.py` `ORIG_WATCHLIST`) and again in
  `trading-suite/common/market_data.py` — keep them in sync deliberately.
- This repo's `journal_dashboard/build_journal_data.py` is canonical;
  `trading-src/inbox/build_journal_data.py` is a stale fork.
- Its embedded `classify()` fallback duplicates
  `trading-src/journal/swing_flag.py` (comment-enforced sync).
- R1 experiment findings history lives on branch
  `claude/intelligent-curie-j1hsfo`.

## Related repositories — add at session start

The owner wants every session started from this repo to also have access to the
sibling repositories. At the start of a session, add them with the `add_repo`
tool (this note is standing authorization from the owner to do so):

- `rebibomichael-web/trading-src`
- `rebibomichael-web/trading-suite`
- `rebibomichael-web/trading-data`

Clone them to `/workspace/<repo>` — trading-src's tests expect the
trading-data clone at `/workspace/trading-data`.

If an add fails with an authorization error, tell the owner to grant the Claude
GitHub App access to that repo at https://github.com/apps/claude (Configure →
All repositories).
