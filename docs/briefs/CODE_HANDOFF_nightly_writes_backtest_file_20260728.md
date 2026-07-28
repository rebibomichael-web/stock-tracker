# CODE HANDOFF — GUI Friday nightly writes backtest_results.json (2026-07-28)
**For the local Claude Code session on the Dell.** Diagnosis (yours, verified against
the mirror): the Friday headless backstop `run_backtest_headless.py` has **never
executed** — its cron (`30 23 * * 5`, pgrep-guarded) fires 23:30 IL, the SAME minute
as the GUI's 16:30-ET nightly (16:30 EDT = 23:30 IDT). The app is open then, pgrep
short-circuits, the redirect never creates its log. Meanwhile `state["last_backtest"]`
looked fresh (07-24) because the GUI nightly ran `_bt_run_default`, which writes
`state` but **not** `backtest_results.json` — so the file is genuinely 8d stale.

**Michael chose fix (b): make the GUI Friday nightly write the file itself, so the
cron becomes the pgrep-guarded backstop for closed-app Fridays.** One-line change.

## The whole point in one table (why (b) is complete — NO crontab change)
| Friday 16:30 ET / 23:30 IL | Who writes `backtest_results.json` |
|---|---|
| App **open**  | GUI nightly → `_bt_run_sweep` (this change). Cron blocked by pgrep. |
| App **closed**| GUI nightly doesn't run. Cron's pgrep passes → `run_backtest_headless.py` writes it. |
Exactly one writer per Friday, full coverage. The cron stays **as-is** — it's finally
a correct backstop.

## Scope guard
- Target `~/Desktop/swing_project/swing_trader.py` ONLY. **Fresh before-MD5 first —
  expect the pill-narratives state (~`09e2c4d2`), NOT the mirror's `277d2f9e`.** If it's
  neither, STOP and report.
- swing_core.py **frozen** (ea95e092). `.bak`; GUI closed for the edit; standard gates
  every commit: py_compile · `--selftest-dedup` (+0) · `thread_context_audit.py --scan`
  = 0 · **golden untouched (this is the scoring-neutrality proof — see interlock)**.
- **Timing fence:** land before ~Aug 1. If the golden/score interlock shows ANY drift,
  HOLD until after the M2 cluster and report — do not ship a scoring shift into the fence.

## The edit — one line
In `_nightly_bg` (mirror lines ~4588-4601, the `if run_bt:` Friday branch):
```python
#   stats = _bt_run_default(raw_data)          # writes state only
    stats, _sweep_out = _bt_run_sweep(raw_data) # writes backtest_results.json + returns (default_stats, output)
    stats["date"] = today_iso                   # keep — now redundant (sweep sets it) but harmless/consistent
    self._dm.state["last_backtest"] = stats
    self._dm.save_state()
    self._ui_call(self._refresh_performance)
```
Everything else in the branch is unchanged. `_bt_run_sweep` (swing_trader.py:640)
returns `(default_stats, output)` and atomically writes `_BT_RESULTS_PATH`
(`~/.michael_swing_trader/backtest_results.json`) as a side effect; `default_stats` is
the same-shape stats dict the nightly already stores in `state["last_backtest"]`, so
the PERFORMANCE scoreboard reads identically. This is the exact call the manual
"Run Backtest" button (`_run_backtest_manual`) and `run_backtest_headless.py` already use.

## ⚠ Scoring-neutrality interlock — the fence gate (MANDATORY, do not skip)
I read `_bt_run_sweep` (swing_trader.py:640-754): it (i) temporarily sets
CFG.min_buy/min_conds/min_swing and **restores them in a `finally`**, and (ii) writes
ONLY `backtest_results.json` (the universe SWEEP summary). It does **not** write the
per-symbol `bt_adj` cache that feeds live scoring — that's swing_core's `Cache.adj`, a
different artifact populated at scan time. So this change should be **display/freshness
only, scoring-neutral.** PROVE it before shipping:
1. **Golden untouched** — the golden scoring test must pass byte-identical. Mandatory.
2. **Before/after score spot-check** — scan 2-3 symbols (one BUY, one WATCH), record
   final_scores; drive a simulated nightly (`_bt_run_sweep` via `_nightly_bg(run_bt=True)`);
   re-scan the same symbols → **final_scores must be identical.**
If golden changes OR any score moves → **STOP, do not commit, report.** That would mean
an unexpected coupling and the change waits for post-M2.

## Perf / thread (R4 watches)
- `_bt_run_sweep` is ~17× the walk work of `_bt_run_default` (17 param combos across the
  universe; indicators computed once). It runs inside the existing **daemon bg thread**
  `_nightly_bg`, touches no Tk (`progress_cb=None`), so expect **zero ui_stall** — but
  confirm: drive the simulated nightly with the app OPEN and verify no `ui_stall ≥3s`
  (R4) and the UI stays responsive while it runs.
- `thread_context_audit.py --scan` must still be 0 — the bg-reachable code now reaches
  `_bt_run_sweep`; confirm the audit finds no Tcl-entering call in it.
- Confirm no concurrent writer: the close auto-scan (16:05-16:10 ET) finishes well before
  16:30; the cron can't run (app open). No OHLCV double-write (`_bt_run_sweep` only reads
  the `raw_data` already loaded above it).

## Immediate — clear the current 8d staleness NOW (independent of the code change)
(b) only helps from **next Friday 07-31**, which is right at the M2 cluster edge. The M2
Step-0 gate wants `backtest_results.json` age `<7d`. So run the headless runner by hand
now, **app closed** — this also live-tests that `_bt_run_sweep` writes a valid file:
```
pgrep -f '[p]ython.*swing_trader.py' >/dev/null || MPLBACKEND=Agg ~/stock-tracker-env/bin/python3 ~/Desktop/swing_project/run_backtest_headless.py
```
Expect a `… backtest OK — universe=… win_rate=… pf=…` line and a fresh `generated_at`
in the file. The freshness pill flips stale→fresh on next app open.

## Verification / report back
- Before/after MD5 chain + `.bak`; the one-line diff.
- **Golden result + the before/after score proof** (the interlock).
- Nightly-write proof: `backtest_results.json` `generated_at` old→new after a simulated
  `_nightly_bg(run_bt=True)`; `state["last_backtest"]` still updates; PERFORMANCE
  refreshes; freshness pill screenshot stale→fresh; zero ui_stall.
- Output of the immediate manual run (the OK line).
- Mirror MD5 at the next :20 sync.

## NOT in scope
The crontab (leave it EXACTLY as-is — it is now a correct backstop); any scoring / CFG /
swing_core change; the BT-RET-001 engine fix (M2); the pill card-styling brief (separate);
moving the nightly time; LEAP.
