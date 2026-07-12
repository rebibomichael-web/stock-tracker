# Journal Dashboard — live-data setup

The dashboard is two files that talk through one JS global (`window.SWING_JOURNALS`):

| File | What it is |
|---|---|
| `journal_dashboard.html` | The dashboard page. Static — open it in any browser, no server needed. |
| `build_journal_data.py` | The adapter. Reads your real scan outputs + live quotes and writes `journal_data.js`, which the page loads. |

## Install (one time)

1. Copy both files into your project folder:

   ```bash
   cp build_journal_data.py journal_dashboard.html ~/Desktop/swing_project/
   ```

   The adapter imports `swing_flag.py` from the same folder (for the exact
   WATCH/ROT/HOLD classification rules), so keeping it in
   `~/Desktop/swing_project/` next to your other scripts is the right home.
   If `swing_flag.py` can't be found it falls back to an embedded copy of the
   same rules and tells you: `classify source: embedded fallback`.

2. Nothing to install. The adapter is stdlib-only; it only uses `yfinance`
   (which you already have in stock-tracker-env) for live quotes, and it
   degrades gracefully if that's missing.

## Run (after every scan)

```bash
cd ~/Desktop/swing_project
python3 build_journal_data.py
```

Then open (or refresh) `~/Desktop/swing_project/journal_dashboard.html` in a
browser. The header badge flips from yellow "MOCKUP · SAMPLE DATA" to green
"LIVE JOURNAL DATA" once real data is loaded.

Cron example — run the swing scan at 16:40 ET on weekdays and rebuild the
journal right after it (the `&&` chains them; the journal only rebuilds if the
scan succeeded):

```cron
40 16 * * 1-5  cd ~/Desktop/swing_project && python3 swing_headless_scan.py && python3 build_journal_data.py >> ~/journal_build.log 2>&1
```

Each run adds/replaces that day's entry and keeps the last 30 days in
`journal_history.json`, so the dashboard's date picker fills up over time.

## Refresh / Scan Now (the phone button)

The dashboard has a **🔄 Refresh** button (top-right of the header on desktop,
in the sticky top bar on the phone). Tapping it re-runs the **full swing scan
and rebuild on demand** — exactly what the cron line does, but right now —
so a phone showing a stale morning snapshot can be brought up to date without
waiting for the desktop app. One tap runs, in order and from the served
directory:

1. `swing_headless_scan.py`  (the real swing scan; **~1–2 min** live)
2. `build_journal_data.py`   (regenerates `journal_data.js`)

then the page reloads itself and shows the fresh data. Progress
(`Scanning… 2/34 · 41s` → `Rebuilding…` → `Updated ✓`) comes straight from the
scan's `[n/total]` log lines.

### It REQUIRES `journal_dash_server.py`

The button only works when the page is served by **`journal_dash_server.py`**
(this folder), **not** `python3 -m http.server` and **not** opening the file
directly. That server is a drop-in replacement for the old no-cache static
server: it still sends every response `Cache-Control: no-store` (so the phone
never caches a stale page), and it adds the two endpoints the button calls.
Opened any other way, the button stays visible but, on tap, explains itself
(*"Refresh needs the LAN server — open the phone URL (…:8090)"*) instead of
hanging.

```bash
cp journal_dash_server.py ~/Desktop/swing_project/
cd ~/Desktop/swing_project
python3 journal_dash_server.py         # stdlib-only, runs on system python3
```

It prints a `http://<lan-ip>:8090/journal_dashboard.html` URL — open that on
the phone (same wifi).

### Config knobs (env)

| Env var | Default | Meaning |
|---|---|---|
| `JOURNAL_DASH_DIR` | `~/Desktop/swing_project` | Directory served **and** the rebuild's working dir (where the two scripts + `journal_data.js` live) |
| `JOURNAL_DASH_PORT` | `8090` | LAN port |
| `JOURNAL_DASH_PY` | `~/stock-tracker-env/bin/python` | Python that runs the scripts — the **venv** one, because `build_journal_data.py` needs `yfinance` (the server itself is stdlib-only) |
| `JOURNAL_DASH_LOG` | `/tmp/journal_rebuild.log` | Combined stdout+stderr of each rebuild (truncated per run) |

### Endpoints

| Route | Method | Returns |
|---|---|---|
| `/rebuild` | POST | Starts scan→build in a background thread → `{"ok":true,"state":"running"}`. A second POST while one is running is a no-op → `{"ok":true,"state":"running","note":"already running"}`. Missing script → `{"ok":false,"error":…}` (won't start). |
| `/rebuild-status` | GET | `{"ok":true,"state":"idle"\|"running"\|"done"\|"failed","elapsed":<sec>,"phase":"scan"\|"build"\|null,"done_n":n,"done_total":m}`. `failed` also includes a `log_tail`. |

Everything else is served as a normal (no-cache) static file. A failed rebuild
never crashes the server — it just reports `failed`, and the button says
*"Scan failed — check /tmp/journal_rebuild.log"*.

### Cron: point `@reboot` at the server

Replace whatever `@reboot` line currently starts the plain static server with
this one, so the phone URL is live (and refreshable) after every boot:

```cron
@reboot  cd ~/Desktop/swing_project && /usr/bin/python3 journal_dash_server.py >> ~/journal_dash_server.log 2>&1
```

The **scheduled** rebuild (the `40 16 * * 1-5` line above) is unchanged — the
button is an *additional*, on-demand path to the same rebuild.

### Why it's manual, not automatic

The scan uses `yfinance`, the **same** data source as your always-running
desktop app. Hammering it on a timer would double up those requests, so
Refresh is deliberately **on-demand** (you tap it when you want fresh numbers)
rather than a background auto-refresh. It's also single-flight: one rebuild at
a time, shared across every device pointed at the server.

## 🩺 Diagnose (any symbol)

A **🩺 Diagnose** control sits right under the header (a search box + **Go** on
both skins). Type any symbol and get its **current technical state for the exit
decision** — a *state-only* snapshot ("overbought, decelerating into
resistance"), never a probability or forecast. Each call does a **fresh ~1–2s
live fetch** (no disk cache — this is an exit tool, so staleness would be
wrong), is **position-aware** (if you hold it, the snapshot adds your lot's
gain/worst-since-entry/flags), and the result is rendered **verbatim** — the
module's formatted text *is* the product.

Two ways in:

- **Search box** — type a ticker (e.g. `NEM`), press **Enter** or tap **Go**.
- **Tap any ticker** — Act Now cards, the over-75 table, both LEAP tables, and
  position rows all have tappable tickers. On the **heatmap**, a **desktop**
  tile click opens Diagnose directly; on the **phone**, tapping a tile still
  shows its detail line and now appends a **· 🩺 diagnose** link to it.

The result opens as a **centered modal** on desktop and a **bottom sheet** on
the phone (✕ / ESC / backdrop tap to close). A short footer shows
`cached · Ns ago` on a cache hit plus the universe/source label.

### It runs the CANONICAL `diagnose.py`

Diagnose does **not** copy anything into the served folder. The server shells
out to the venv python and runs **`~/trading-src/swing/diagnose.py` in place**
(its canonical home, next to `swing_core`) — so it always tracks the real
engine. Michael chose this over a copy on purpose. Like the rebuild, it needs
the **venv python** (`JOURNAL_DASH_PY`) because `diagnose.py` imports `yfinance`
(and `trade_journal` for the open-position lookup); the server itself stays
stdlib-only.

### It REQUIRES `journal_dash_server.py`

Same as Refresh: the box/tickers only work when the page is served by
`journal_dash_server.py`. Over `file://` or with no server, the panel explains
itself (*"Diagnose needs the LAN server — open the phone URL (…:8090)"*) instead
of hanging.

### Endpoint

| Route | Method | Returns |
|---|---|---|
| `/diagnose?symbol=XYZ` | GET | `{"ok":true,"symbol":"XYZ","cached":false,"result":{…}}` on a fresh run, or `…"cached":true,"age":<sec>…` from cache. The `result` is `diagnose(symbol)`'s dict (its `text` field is the formatted snapshot). A symbol whose data fetch fails still returns `ok:true` with `result.error` set (the module reports its own errors as data). Bad/empty symbol → `{"ok":false,"error":"invalid symbol"}`; timeout → `{"ok":false,"error":"diagnose timed out after Ns"}`; missing `diagnose.py` → `{"ok":false,"error":"diagnose.py not found in <dir> — set JOURNAL_DASH_DIAG_DIR"}`. Always HTTP 200 (no-cache), matching the server's `ok:false` convention. |

The symbol is validated against `^[A-Z0-9.\-]{1,7}$` (uppercased + stripped)
**before** any subprocess runs, and is passed only as a subprocess **argv
element** — never a shell string, never a filesystem path. Results are cached
per symbol for **60s**, and a small semaphore caps concurrent live fetches to 3
so a tap-happy phone can't stampede yfinance.

### Config knobs (env)

| Env var | Default | Meaning |
|---|---|---|
| `JOURNAL_DASH_DIAG_DIR` | `~/trading-src/swing` | Directory containing `diagnose.py` (run in place; also the subprocess cwd) |
| `JOURNAL_DASH_DIAG_TTL` | `60` | Per-symbol result cache, in seconds |
| `JOURNAL_DASH_DIAG_TIMEOUT` | `30` | Kill the diagnose subprocess (and report `timed out`) after this many seconds |

The startup banner prints a `diagnose : <dir>  (found | MISSING …)` line so you
can see at a glance whether the wrapper will work.

## What each section shows, and where it comes from

| Section | Source |
|---|---|
| **Market strip** (VIX/regime/mult/breadth + index tiles) | Header fields of `~/Desktop/swing_headless_results.json`; index/crypto tiles are live yfinance quotes (`^DJI ^IXIC ^GSPC ^VIX BTC-USD ETH-USD KAS-USD`). |
| **ACT NOW cards** (entry / max-chase / stop / T1 / T2 / R:R / hard stop) | Swing results with an ACT NOW signal and score ≥ 85. Trade levels are derived exactly like swing_trader's fire card: stop = price − 2×ATR, T1 = price + 2×ATR, T2 = price + 3×ATR, hard stop = scan date + 21 days. |
| **Over-75 table** | All swing results with final score > 75 (tech = raw_buy, bt = backtest adj, arb = arb z-score). "Opinion" is "—" because Barchart isn't scraped headless. |
| **Positions** (WATCH/ROT/HOLD flags + the 4 checks) | `open_positions_export.csv` ("Swing Trader" rows, lots aggregated per ticker with cost-weighted entry) + live prices; flags come from `swing_flag.classify` — same −8% worst-since-entry cliff and 22-day ROT rules as `swing_flag.py`. |
| **Heatmap** | ALL tickers in the CSV. Green/red = live daily change. "Excluded strategy" tickers get their own block; a ticker with both strategies counts as active. Tiles show the day's LEAP score when one exists. |
| **Daily Holdings** (plain-English verdict per held ticker) | Journal opens (`parse_fidelity_csv` + tags DB) × `holdings.py` (per-account counts/adjusted basis from `Accounts_History*.csv` + `Portfolio_Positions*.csv`) × `diagnose.py` (verdict + LEAP thesis read — imported, never copied). Excluded-tagged lots are dropped; LEAP options appear under their **underlying**; each ticker shows once with per-account/per-lot rows underneath (entry-anchored stop/T1/T2 from the buy-date ATR). Header stamps positions-data freshness and goes loud when stale. |
| **LEAPs** (Original watchlist vs Exceeders) | `~/.michael_leap_recommendations.json`, that day's records. Original = top 3 from your 16-name watchlist; Exceeders = any full-universe name that out-scored the watchlist's best. ⚠ next to a premium = stale-premium guard fired. |

## Config knobs (top of `build_journal_data.py`)

| Knob | Default | Meaning |
|---|---|---|
| `SWING_JSON_PATH` | `~/Desktop/swing_headless_results.json` | Swing scan output |
| `CSV_PATH` | `~/Desktop/swing_project/open_positions_export.csv` | Journal's open-positions export |
| `LEAP_JSON_PATH` | `~/.michael_leap_recommendations.json` | LEAP tracker records |
| `OUT_PATH` | `~/Desktop/swing_project/journal_data.js` | What the dashboard loads |
| `HISTORY_PATH` | `~/Desktop/swing_project/journal_history.json` | Multi-day store |
| `ORIG_WATCHLIST` | your deployed 16 names | Defines the Original/Exceeders split |
| `CARD_MIN` | 75 | Min score (exclusive) for a trade card — applies to actionable signals only (`CARD_SIGNALS`: ACT NOW, ARB BUY, BUY; WATCH/SELL never get cards) |
| `TABLE_MIN` | 75 | Min score (exclusive) for the over-75 table |
| `HISTORY_KEEP` | 30 | Days kept in history / the date picker |

Every path also has a CLI flag (`--swing --csv --leap --out --history`), plus
`--date YYYY-MM-DD` (override the journal date; default is the swing scan
timestamp's date), `--no-history` (write a single date only), `--offline`
with `--quotes fixture.json` (no network — for testing), and `--selftest`
(runs the built-in assertions on the mapping math and the journal encryption
scheme — test vector, round-trip, wrong-password rejection — touches nothing).

## Trade Journal (locked) section

The dashboard has a password-protected "💰 Trade Journal" section: your real
closed-trade P/L, win rates, per-strategy cards, charts and a recent-trades
table, computed from the same Fidelity CSV + tags DB your desktop
`trade_journal.py` app uses. Because `journal_data.js` may sit in synced
folders and is served over plain HTTP on the LAN, this data is **encrypted at
rest** and only decrypted in the browser after you type the password.

### Password setup (one time)

```bash
# pick any passphrase; this file is the default password source
printf 'my-strong-passphrase' > ~/.journal_dashboard_key
chmod 600 ~/.journal_dashboard_key
```

The adapter warns if the file's permissions aren't `600`. Alternative sources,
first match wins: `--journal-password <str>` →
`--journal-password-file <path>` → env `JOURNAL_DASH_PASSWORD` →
`~/.journal_dashboard_key`. Then just re-run `build_journal_data.py` — on the
page, the section shows a 🔒 password prompt; unlocking takes a couple of
seconds (60,000 PBKDF2 iterations, on purpose). "Remember this device" stores
only the *derived* keys in that browser, never the password.

If you change the password later, just edit the key file and re-run the
build; browsers that "remembered" the old password will simply prompt again.

### What is / isn't encrypted

| Encrypted (inside `SWING_JOURNAL_LOCKED`) | Plaintext (as before) |
|---|---|
| All journal trade data: tickers, option symbols, P/L dollars/percents, win rates, deployed capital, turnover, allocation, trade dates, the recent-trades table, CSV filename | The existing per-date dashboard sections (market strip, ACT NOW, over-75 table, positions, heatmap, LEAPs) in `window.SWING_JOURNALS` |

Scheme: PBKDF2-HMAC-SHA256 (60k iterations) → HMAC-SHA256 keystream XOR +
encrypt-then-MAC; the MAC is verified before decryption, so a wrong password
reveals nothing (and is how "wrong password" is detected). The blob is
recomputed fresh each run, lives only at the top level of `journal_data.js`,
and is **never** written to `journal_history.json`. If no password source is
configured the adapter prints a WARN and omits the section entirely — journal
data is never emitted unencrypted.

### Journal flags

| Flag | Default | Meaning |
|---|---|---|
| `--journal-csv` | `last_csv_path` from `~/.trade_journal_config.json` | Fidelity trade-history CSV to analyze |
| `--journal-tags-db` | `~/.trade_journal_tags.db` | Strategy tags SQLite (same one the app writes) |
| `--journal-config` | `~/.trade_journal_config.json` | Config override (mostly for tests) |
| `--journal-password` | — | Password on the command line (prefer the key file — argv is visible in `ps`) |
| `--journal-password-file` | — | Read password from this file |
| `--no-journal` | off | Skip the journal section entirely |
| `--holdings-dir` | journal CSV's folder | Folder holding `Accounts_History*.csv` / `Portfolio_Positions*.csv` for per-account totals |
| `--no-holdings` | off | Skip the Daily Holdings section |

The adapter imports your real `trade_journal.py` (looked up next to the
script, then `~/Downloads`, then `~/trading-src/journal`) and reuses its
parser, FIFO matcher, tagging rules and return math — nothing is
reimplemented. It imports headlessly: on machines without Tk/matplotlib the
GUI imports are stubbed out automatically.

### Cron

**No change needed.** The existing cron line picks the journal section up
automatically on its next run: the password comes from
`~/.journal_dashboard_key`, the CSV path from the journal app's own config
(whatever you last loaded in the app), and every journal problem degrades to
a WARN line in `~/journal_build.log` while the rest of the dashboard still
builds.

### Journal troubleshooting

- **Section shows "Trade Journal not configured"** — no password source was
  available at build time. Create `~/.journal_dashboard_key` (see setup
  above) and re-run the build; the log will have said
  `WARN: journal password not configured …`.
- **"Wrong password" when unlocking** — the password you typed doesn't match
  the one used at build time (MAC verification failed — by design nothing
  decrypts). Check the content of `~/.journal_dashboard_key` (note: the file
  is read *trimmed*, so a trailing newline is fine).
- **`WARN: journal section unavailable: trade_journal.py not importable`** —
  the adapter couldn't find/import your journal app. Keep `trade_journal.py`
  in `~/Downloads` (its canonical home) or copy it next to
  `build_journal_data.py`; the mirror in `~/trading-src/journal` also works.
- **`WARN: journal CSV not found`** — pass `--journal-csv` or open the CSV in
  the journal app once (it records `last_csv_path` in its config).
- **Permissions warning about the key file** — run
  `chmod 600 ~/.journal_dashboard_key`.
- **Sanity check** — `python3 build_journal_data.py --selftest` verifies the
  encryption test vector, a random round-trip, and wrong-password rejection.

## PATCH NOTE — turn the Rev column live (1 line)

The LEAP scanner already computes reversal confirmation (`rev_confirmed`) per
ticker but doesn't persist it into the recommendations JSON, so the
dashboard's **Rev column shows "—" until you add it**. Patch the copy your
cron actually runs — `~/leap_headless_scan.py` (NOT a copy inside
swing_project; the trading-src mirror picks the change up via your one-way
sync). Right next to the existing `premium_stale` attach (inside `main()`,
after `tracker.recs[-1]["premium_stale"] = row["premium_stale"]` and before
`tracker._save()`), add this one line:

```python
                    tracker.recs[-1]["rev_confirmed"] = row["rev_confirmed"]
```

From the next scan on, new records carry `rev_confirmed` and the Rev column
shows ✅ confirmed / ❌ not confirmed. Older records stay "—" (unknown) — that's
correct, not a bug.

## Troubleshooting

- **"WARN: swing results not found …"** — run `swing_headless_scan.py` first,
  or point at the file with `--swing /path/to/swing_headless_results.json`.
  The build still completes; that section is just empty for the day.
- **"WARN: positions CSV not found …"** — do the journal's *Export Open*
  first (it writes `open_positions_export.csv`), or pass `--csv`.
- **"WARN: quote fetch failed …" / no internet** — the script never fails on
  network problems. Affected fields (price, %chg, worst-low) render as "—" /
  neutral gray on the page. Re-run when you're back online. For a fully
  offline rebuild use `--offline --quotes quotes.json` with a fixture in the
  documented format.
- **"classify source: embedded fallback"** — `swing_flag.py` wasn't importable
  from the script's folder. On Michael's machine it lives in
  `~/trading-src/journal/swing_flag.py`, not `~/Desktop/swing_project/` — copy
  it in (`cp ~/trading-src/journal/swing_flag.py ~/Desktop/swing_project/`) and
  re-run; the summary should then print "classify source: swing_flag.py".
  Flags still work on the fallback (it's a copy of the same rules), but only
  the import path follows future threshold tuning automatically.
- **Dashboard still shows the yellow SAMPLE badge** — `journal_data.js`
  isn't next to `journal_dashboard.html`, or the build failed before writing
  it. Check the one-screen summary the adapter prints: the `Output` line tells
  you exactly where it wrote.
- **Wrong date on the page** — the journal date comes from the swing scan's
  timestamp. Rebuilding old data? Use `--date YYYY-MM-DD`.
- **Sanity check anytime**: `python3 build_journal_data.py --selftest`
  (pure math assertions, no files, no network).
