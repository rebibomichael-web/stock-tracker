# CODE HANDOFF — daily-resolution signal outcomes (join the existing bars cache, don't poll live)
**For the local Claude Code session on the Dell. Michael's directive 2026-08-05: "we are always looking at set day amounts instead of the data determining valid date parameters — if not logging for daily basis that should be." Traced from the cloud; this brief is the fix.**

## The finding (verified from source, not guessed)
Two systems exist today and have never been connected:
1. **`SignalTracker.check_outcomes()`** (`swing_core.py:664`) — for every signal, polls ONE live `yf.Ticker(...).fast_info.last_price` quote at each of exactly three fixed elapsed-day thresholds: `[("7d",7),("14d",14),("21d",21)]`. That is the entire resolution of every outcome ever logged — three dots per signal, no path between them.
2. **`ohlcv_cache.py`** — a persistent, already-populated per-ticker **daily** OHLCV parquet cache at `~/.michael_swing_ohlcv_cache/<TICKER>.parquet`, holding up to ~400 calendar days of daily bars, refreshed for backtesting/scanning. This already has the full daily price path for every signal ever fired — for free, no new network calls, it's paid for already.

Every "needs daily data, needs the Dell" caveat threaded through this week's cloud analysis (velocity/speed-of-arrival, repeat-fire timing, Swing% accuracy) exists solely because the grader never reads #2. Michael's critique is exactly right and the fix is a **join, not new infrastructure.**

## The fix — additive, not a replacement of the 7/14/21 system
Do NOT remove the existing 7d/14d/21d checkpoint fields — the whole grading pipeline, `signal_scoreboard()`, PERFORMANCE tab, and everything M1/M2 computed key off `outcomes["7d"]["checked"]`. This is purely additive.

1. New field on each signal record: `outcomes["daily_path"]` — a list of `{"date": "...", "change_pct": ...}` computed once, when the 21d checkpoint is graded (i.e., piggyback on the existing `check_outcomes()` pass, same trigger, same cron).
2. Source the path from `ohlcv_cache` (via `swing/ohlcv_cache.py`'s existing read helper — do not add a second cache), walking daily closes from the signal's `date` forward through however many trading days the cache retains (up to ~21-28 trading days is enough to cover the existing grading horizon plus headroom for a real M3 velocity study).
3. If the ticker's cached bars don't reach back far enough for a given signal's fire date (cache is ~400-day rolling, older signals may be at the edge), store what's available and mark `"path_coverage": "partial"` — never silently truncate without saying so, per the project's own "absence must be a signal" rule (see the silent-scheduler lessons already in MASTER_LOG).
4. No new live network calls — this reads a cache that already exists on disk. If a ticker's parquet is missing/stale for some signals, log it exactly like `check_outcomes()`'s existing failed-attempt handling (attempts/last_error), don't fail silently.

## Gates
py_compile · existing outcome-checker tests (if any) still pass · confirm the 7/14/21 fields are byte-for-byte unchanged for signals graded before this ships (regression, not enrichment, for old records) · spot check 5-10 signals: `daily_path`'s day-7 value should equal (within rounding) the existing `outcomes["7d"]["change_pct"]` — same underlying price series, different sampling.

## Why this matters beyond convenience
It resolves, in one pass, the daily-resolution gap in three separate analyses already run this week (see docs/briefs/ANALYSIS_RUN_REGISTRY_SPEC_20260805.md Parts 5-7): the true "+2% in 1 day vs 5 days" velocity question, a real day-by-day repeat-fire recency curve (not just 30/60/90/180-day buckets), and a genuine intraday-path check for Swing%'s MFE/MAE accuracy. None of those need to wait for new instrumentation once this lands — they need only a re-run against the enriched signal log.

## NOT in scope
No change to scoring, CFG, the 21-bar time-stop, or any live decision logic — this is a read of an existing cache into a new logged field. Zero effect on the swing_core scoring path itself.
