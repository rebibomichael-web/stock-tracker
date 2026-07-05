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

## What each section shows, and where it comes from

| Section | Source |
|---|---|
| **Market strip** (VIX/regime/mult/breadth + index tiles) | Header fields of `~/Desktop/swing_headless_results.json`; index/crypto tiles are live yfinance quotes (`^DJI ^IXIC ^GSPC ^VIX BTC-USD ETH-USD KAS-USD`). |
| **ACT NOW cards** (entry / max-chase / stop / T1 / T2 / R:R / hard stop) | Swing results with an ACT NOW signal and score ≥ 85. Trade levels are derived exactly like swing_trader's fire card: stop = price − 2×ATR, T1 = price + 2×ATR, T2 = price + 3×ATR, hard stop = scan date + 21 days. |
| **Over-75 table** | All swing results with final score > 75 (tech = raw_buy, bt = backtest adj, arb = arb z-score). "Opinion" is "—" because Barchart isn't scraped headless. |
| **Positions** (WATCH/ROT/HOLD flags + the 4 checks) | `open_positions_export.csv` ("Swing Trader" rows, lots aggregated per ticker with cost-weighted entry) + live prices; flags come from `swing_flag.classify` — same −8% worst-since-entry cliff and 22-day ROT rules as `swing_flag.py`. |
| **Heatmap** | ALL tickers in the CSV. Green/red = live daily change. "Excluded strategy" tickers get their own block; a ticker with both strategies counts as active. Tiles show the day's LEAP score when one exists. |
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
| `ACT_NOW_MIN` | 85 | Min score for an ACT NOW card |
| `TABLE_MIN` | 75 | Min score (exclusive) for the over-75 table |
| `HISTORY_KEEP` | 30 | Days kept in history / the date picker |

Every path also has a CLI flag (`--swing --csv --leap --out --history`), plus
`--date YYYY-MM-DD` (override the journal date; default is the swing scan
timestamp's date), `--no-history` (write a single date only), `--offline`
with `--quotes fixture.json` (no network — for testing), and `--selftest`
(runs the built-in assertions on the mapping math, touches nothing).

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
