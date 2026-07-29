# DELL RUNSHEET — backtest freshness: manual clear + fix (b) + Item-46 logging (2026-07-29)
**For the local Claude Code session on the Dell. SUPERSEDES `CODE_HANDOFF_nightly_writes_backtest_file_20260728` — same fix, re-baselined after the two 07-28 UI builds (pill cards, gold+drag) moved the file.**

**Fresh baselines (mirror-verified from the cloud, 07-29):**
- `swing_trader.py` before-MD5: **`a10c31d0`** (`a10c31d0dd3ac401ab2342100f010952`). The old brief said expect `09e2c4d2` — that was two builds ago; if you see anything other than `a10c31d0`, STOP and report.
- `swing_core.py` baseline: **`55ffd7f2`** (the `ea95e092` frozen-hash in the old brief was stale — you caught this yourself on the pill run). Still: do not touch it.
- Fresh line anchors: `_bt_run_sweep` def at **640** (unchanged), `_run_backtest_manual` at **2234**, `_nightly_bg` def at **4686**, the target line `stats = _bt_run_default(raw_data)` at **4697**. Anchor by string regardless.
- `.bak` name for this run: `swing_trader.py.bak_20260729_fixb`.

**Why now, with evidence:** `backtest_results.json` is `generated_at 2026-07-20T09:52:40` — **9 days stale** as of 07-29, confirmed from the trading-data backup (last backup commit touching the file: 07-20 17:15; every backup since has left it unchanged). The M2 Step-0 gate wants `<7d`; the M2 fence is ~Aug 1. Fix (b) only helps from Friday 07-31 — hence Step 0 by hand, today.

---

## STEP 0 — clear the staleness NOW (no code; app CLOSED)

Pre-check (in case it was cleared since the cloud looked):
```
stat -c '%y' ~/.michael_swing_trader/backtest_results.json
```
If the mtime is already <7d old, say so and skip to Step 1. Otherwise, with the GUI closed:
```
pgrep -f '[p]ython.*swing_trader.py' >/dev/null || MPLBACKEND=Agg ~/stock-tracker-env/bin/python3 ~/Desktop/swing_project/run_backtest_headless.py
```
Expect a `… backtest OK — universe=… win_rate=… pf=…` line and a fresh `generated_at` in the file. This also live-tests that `_bt_run_sweep` writes a valid file — the very call Step 1 wires into the nightly. The freshness pill flips stale→fresh on next app open. **Report the OK line verbatim.**

## STEP 1 — fix (b): GUI Friday nightly writes the file (one line)

Michael's chosen fix, unchanged from the 07-28 brief. The coverage table that makes (b) complete with **NO crontab change**:

| Friday 16:30 ET / 23:30 IL | Who writes `backtest_results.json` |
|---|---|
| App **open**  | GUI nightly → `_bt_run_sweep` (this change). Cron blocked by pgrep. |
| App **closed**| GUI nightly doesn't run. Cron's pgrep passes → `run_backtest_headless.py` writes it. |

Exactly one writer per Friday. The cron stays EXACTLY as-is — it finally becomes a correct backstop.

In `_nightly_bg` (def ~4686), the `if run_bt:` Friday branch, line ~4697:
```python
#   stats = _bt_run_default(raw_data)          # writes state only
    stats, _sweep_out = _bt_run_sweep(raw_data) # writes backtest_results.json + returns (default_stats, output)
    stats["date"] = today_iso                   # keep — now redundant (sweep sets it) but harmless/consistent
    self._dm.state["last_backtest"] = stats
    self._dm.save_state()
    self._ui_call(self._refresh_performance)
```
Everything else in the branch unchanged. `_bt_run_sweep` (640) returns `(default_stats, output)` and atomically writes `~/.michael_swing_trader/backtest_results.json` as a side effect; `default_stats` is the same-shape dict the nightly already stores, so the PERFORMANCE scoreboard reads identically. Same call the manual button (2234) and the headless runner already use.

### ⚠ Scoring-neutrality interlock — the fence gate (MANDATORY, do not skip)
`_bt_run_sweep` reads as display-only: it temporarily sets CFG.min_buy/min_conds/min_swing and **restores them in a `finally`**, and writes ONLY the sweep summary file — never the per-symbol `bt_adj` cache (swing_core `Cache.adj`, populated at scan time) that feeds live scoring. **Prove it:**
1. **Golden untouched** (`04b8e0cf`) — byte-identical. Mandatory.
2. **Before/after score spot-check** — scan 2-3 symbols (one BUY-ish, one WATCH), record final_scores; drive a simulated nightly (`_nightly_bg(run_bt=True)`); re-scan the same symbols → final_scores **identical**.

If golden changes OR any score moves → **STOP, do not commit, report.** The change then waits for post-M2. **Timing fence: land before ~Aug 1 or hold past the cluster.**

### Perf / thread (R4 watches)
- `_bt_run_sweep` is ~17× `_bt_run_default`'s walk work, but runs inside the existing daemon bg thread `_nightly_bg`, touches no Tk (`progress_cb=None`) → expect **zero ui_stall**; confirm with the app OPEN during the simulated nightly (no `ui_stall ≥3s`, UI responsive).
- `thread_context_audit.py --scan` still 0 — bg-reachable code now includes `_bt_run_sweep`.
- No concurrent writer: close auto-scan ends well before 16:30; cron can't run while the app is open.

### Gates (standard, every commit)
py_compile · `--selftest-dedup` (+0) · thread audit 0 · golden `04b8e0cf` untouched · `swing_core` `55ffd7f2` untouched · MD5 chain `a10c31d0 → <new>` + `.bak` · mirror check at the next :20 sync.

## STEP 2 — rider: write the logs from the gold+drag run (docs only, no code)

You left these for a separate instruction — this is it:
- `CHANGE_LIST_CONSOLIDATED.md` **Item 46 → CLOSED** (owner-ratified option (e) drag-and-drop, 2026-07-28; note the 05-29 → 07-28 stall at the never-answered design-review gate as the resolution story).
- MASTER_LOG entries for the 07-28 runs: pill cards (`09e2c4d2 → 48e07b4b`) and gold+drag (`48e07b4b → a10c31d0`, Item 46 closed, `_PILL_TINT` "twins" comment rewritten to record the deliberate SIGNALS-white/card-gold asymmetry).

## Report back
Step 0 OK line (or the skip evidence) · one-line diff + MD5 chain · golden + score-proof results · nightly-write proof (`generated_at` old→new after the simulated `_nightly_bg(run_bt=True)`, state still updates, PERFORMANCE refreshes, freshness pill stale→fresh, ui_stall 0) · confirmation of the two log entries · mirror MD5 at :20.

## NOT in scope
The crontab (leave EXACTLY as-is); any scoring / CFG / swing_core change; BT-RET-001 (M2); moving the nightly time; LEAP; further pill/chart styling.
