# Swing Trading Program — Full Audit, Improvement Plan & Recommendation
**Date:** 2026-07-06 · **Scope:** `trading-src/swing/*`, `trading-src/journal/*`, `trading-data/swing/*`, `stock-tracker/journal_dashboard/*` · **Method:** three parallel read-only audits (code health, data-pipeline integrity, investment review), synthesized here. Nothing was modified. Companion to the LEAP audit effort (MASTER_STATUS_BOARD Part III).

---

## EXECUTIVE SUMMARY

**Investment verdict: UNPROVEN — a well-run experiment, not yet an investable edge.** The process discipline (pre-registration, hypothesis ledger, small-cell rules) is genuinely excellent — better than the results it protects. But the measured edge (526 signals, 54.0% WR, PF 1.23) rests on one bullish year, a survivorship-tilted universe, an execution model that enters at the same close the signal is computed from, and zero benchmark comparison. The edge halves in the second half of its own backtest window (59.1% → 51.3% WR). Bear-regime data: n=0 everywhere. The live 82-trade sample (~75% WR, +2.86%/trade) measures Michael's discretionary trade selection and exits, not the system.

**Code/data verdict: the newer modules are healthy; the debt is concentrated in two 3,900-line GUI monoliths, and the swing program has its own versions of the LEAP "discard defect"** — data being irrecoverably lost with every trade, today. Three one-line-class fixes stop the bleeding.

**Recommendation (see §5):** keep trading at clean-trial size only; spend the next 2–4 weeks on (a) the forward-only logging fixes, (b) two cheap analyses that decide whether the edge is real (benchmark restatement + next-day-open backtest rerun), and (c) a hard tail-stop + kill-switch. Defer the big refactor until a golden test exists. Do not scale capital until Milestone 2 (~Aug) confirms out-of-sample.

---

## 1. INVESTMENT REVIEW

### 1.1 What the strategy actually is
A ~24-condition additive composite (`_buy_rules`, swing_trader.py:233–268; hand-set weights, e.g. MACD cross +28, EMA9/21 cross +26, RSI(3)≤20 +24), capped at 75, then bt_adj [−20,+10] + ARB bonus + regime multiplier (bull 1.10 → danger 0.65) + breadth penalty. Gates: score ≥70, ≥7 conditions, ≥1 strong primary, ATR gate (2×ATR/price ≥4%). The dominant fired combo is an **oversold-bounce signature** (rsi_recovery + at_lower_bb + stoch_oversold + willr_oversold) — a mean-reversion dip-buyer on volatile large-caps.

### 1.2 Why the measured edge is not yet trustworthy
| Issue | Evidence |
|---|---|
| One year, one regime | Backtest window 2025-06→2026-06; live signals: bull 303 / normal 356 / **bear 0** |
| Edge decays in-window | Baseline WR 59.1% (half 1) vs 51.3% (half 2) — near coin-flip in the recent half |
| Survivorship | 152-name universe hand-picked *in 2026* (MSTR, IONQ, RGTI, HOOD…), backtested over the prior year — selection on outcome. Flagged for the medium lane, never for swing |
| Backtest ≠ live pipeline | `_bt_walk` scores candidates one at a time — breadth penalty, ARB, bt_adj, regime mult, earnings skip never operate in backtest; Fib conditions only set on the last row (:210–223) so backtest ~never sees them but live does |
| Optimistic execution | Entry at the signal bar's own close; nightly scan runs post-close, so the earliest real fill is next open — and for bounce signals the overnight rebound is a large share of the alpha. No slippage/costs in `_bt_walk` (CFG.comm unused) |
| No benchmark | No SPY/QQQ excess-return comparison anywhere in the repo. Raw signal outcomes (+4.15% mean at 7d, 67.9% up) are unadjusted for a rising, beta-heavy tape |
| Parameter sensitivity | Exit A/B v1 showed PF **0.92** (losing) vs v2 PF 1.36 after a min_conds 7→3 change — the baseline PF 1.23 deserves wide error bars |

### 1.3 The velocity thesis is confounded
The program's "intellectual core" (<7d holds +1.22%/day vs ≥7d +0.21%/day, tagged PROVEN) has a structural confound: **return/day on discretionary exits is endogenous** — Michael sells fast winners fast and holds laggards, so the sort happens at exit, not entry. Three of five hold buckets fail the program's own <20-trade small-cell rule. The one live policy counterfactual (Milestone-1 M2) points the **opposite way** (hold-to-target without time-stop: expectancy 1.868 vs 0.873). And M10 found *below*-median entry-velocity trades did far better (83.3% WR, +5.10% vs 65.9%, +0.55%) — the edge, if any, is in patient washed-out entries. **Downgrade IV-2 to Suspected on the status board.**

### 1.4 Risk gaps (position and portfolio)
- **No hard stop by design.** −8% is a triage flag, never auto-sell. Tail risk uncapped: a gap through −20% on earnings has no mechanical response. (Also: the shipped −8% "cliff" is contradicted by the Milestone-1 live floor curve, where −10/−12% floors beat −8% in expectancy — downgrade to Suspected.)
- **ROT≥22d is contested by its own data** (backtest says keep; live counterfactual says it costs half the expectancy).
- **No take-profit at all** — the velocity-take threshold that supposedly *is* the edge was never derived (swing_flag.py's own KNOWN GAP).
- **Concurrency:** avg 15, max 27 positions; pairwise correlation never computed (M9 deferred). The 06-29 incident (AMZN/FDX/AVGO/GOOG all crossing −8% together) says 15–27 positions ≈ 2–3 independent bets levered on QQQ.
- **Sizing contradiction:** "$100/trade clean trial [PROVEN]" and "dollar-weighted instinct CONFIRMED [PROVEN]" coexist with no sizing rule (no vol targeting, no per-name/sector cap).
- **Unmanaged:** earnings holds (14/83 real trades held through earnings, tagged "insufficient — monitoring"), weekend/overnight gaps (close-only logic everywhere), regime failure (caution n=2, danger n=4; multiplier values hand-set), yfinance single-vendor risk, raw-vs-adjusted price mismatch biasing drawdown display against dividend payers.
- **No equity-curve tracking, no kill-switch.** Per-trade stats only; portfolio NAV, max drawdown, beta, exposure-over-time — nowhere. Nothing says "stop trading if X."
- **Scale reality check:** median real trade $119; even at the optimistic live mean that's ~$3.40/trade. Fine as data collection (which $100 sizing explicitly is); not yet an investment program.

### 1.5 Statistical hygiene
Genuinely good: pre-registration honored under a failing result (R1b), Suspected→Proven ledger, small-cell rule, spike-vs-plateau, live/backtest never pooled. A skeptic downgrades: IV-2 velocity (confounded, small cells), IV-6 sizing instinct (one in-sample cut), the −8% cliff (in-sample, contradicted live), and the 526-signal baseline (deserves the same survivorship stamp Part II already carries). Every sub-analysis re-slices the same 82 trades — heavy multiple-comparisons exposure. **Nothing shipped has ever been validated on data that post-dates its design; Milestone 2 (~Aug) is the project's first true out-of-sample test.**

---

## 2. DATA-PIPELINE INTEGRITY — swing-side "discard defects"

The LEAP audit's headline (rev_confirmed computed but 0/1,317 persisted) has direct swing equivalents. **Forward-only = data being irrecoverably lost NOW; every day of delay is data gone forever.**

### 2.1 Forward-only defects (urgent, mostly one-line-class)
| # | Defect | Evidence | Fix |
|---|---|---|---|
| D1 | **`entry_atr` null in 34/34 live exit records** — ATR is computed per candidate and shown in the log-trade modal, but `_TA_PERSIST_KEYS` (swing_trader.py:120–123) omits "ATR", so it's dropped at persist time. All future R-multiple analysis impossible | data_layer.py:523–524 | add ATR (and Close) to `_TA_PERSIST_KEYS` |
| D2 | **`exit_regime` hardcoded `None`** at close (swing_trader.py:2807) though `self._regime.state` is live in the same object. Null in 34/34 live + 489/489 backtest records | swing_trader.py:2807 | wire the real regime in |
| D3 | **MFE/MAE machinery built but never fires** — `update_active_trade_excursion` only runs from the SL/TP monitor, which defaults OFF. 0/13 active, 0/32 closed trades have excursion; mfe/mae 0.0 in every sink record. Intraday extremes cannot be reconstructed later | data_layer.py:54, :325 | update excursion in the regular scan's position-monitoring block (price + trade already in hand at swing_trader.py:3205–3250) |
| D4 | **Only BUY-tier signals ever logged** (swing_trader.py:3183–3185). SELL/WATCH/SUPPRESSED rows are fully scored then discarded — no negative-class examples, so "does the gate add alpha?" is structurally unanswerable (same class as LEAP's rev_confirmed) | swing_trader.py:3183 | log all tiers (or a sample) with outcomes |
| D5 | **Score decomposition discarded**: Tracker.log persists only final `score` — no bt_adj, arb, event penalty, sell_score; scan summaries fake `passes_gates` from the signal string and record `spy_price: null` (57/57) and `watchlist_count == signals_generated` | swing_trader.py:1267–1287, 3190–3194 | persist the components |
| D6 | **Partial-close P&L truncation** (the roll-truncation analog): `_partial_close` shrinks shares without rolling partial legs' realized P&L into `outcome`; sink emits nothing on partials — realized P&L is mis-stated in every downstream stat | data_layer.py:563–577 | fold partial exits into `_full_close` P&L + emit sink records |
| D7 | **Un-synced history**: scan-summary gz archives (>30 days) and `swing_headless_results.json` (overwritten every run) never reach trading-data — ~7 weeks of scan summaries exist only on one machine | data_layer.py:640–679 | add to backup-data.sh |
| D8 | `sector:"Unknown"` in 32/32 closed trades (modal never passes it); headless scans persist nothing to Tracker/sinks by design | swing_trader.py:2728; swing_headless_scan.py | pass sector; decide headless logging policy |

### 2.2 Durability
- **signals.json — the crown-jewel 659-record outcome dataset — is written non-atomically with `except: pass`** (swing_trader.py:1265), and `check_outcomes` rewrites the whole file after network calls. One crash = entire outcome history gone. No backup. Same pattern on bt cache, barchart live/synth, e0 backtest sink, headless results, universe file. (data_layer's atomic `_save_json` already exists — reuse it.)
- **Cross-process clobbering:** headless cron scan and the GUI both write `state.json` / bt cache with only a per-process lock — last-writer-wins.
- **Unbounded growth:** signals.json, barchart_synth (5,135 records), momentum_history per trade (50 entries on GOOGL already), 98/98 fire alerts perpetually "pending", 32 accumulating Fidelity CSV copies.

### 2.3 Join integrity & drift
- No shared key between swing `trade_id`, journal `trade_key`, and signals.json rows (which have 137 duplicate symbol/day pairs). UTC-vs-local date mismatch breaks (ticker, date) joins for evening entries.
- Date-format zoo: same field names carry naive-local ISO, UTC "Z", and integer bar indices across sinks.
- `swing_flag.classify` vs the dashboard's embedded fallback: **currently in sync, but comment-enforced only** — and in the stock-tracker deployment the fallback is what actually runs. `trading-src/inbox/build_journal_data.py` is a stale 535-line-diff fork of the stock-tracker copy. Dashboard hardcodes STOP_M/T1_M/T2_M/ACT_NOW_MIN separately from CFG.

---

## 3. CODE HEALTH

### 3.1 Structure
`swing_trader.py` (3,935 lines) holds ≥13 responsibilities: config, universe, fetch, indicators, scoring+gates, pipeline, **two separate backtest engines** (:493 and :1167 — different exit semantics), telemetry, health monitor, regime/arb/events, setup classification, Barchart scraping, and a ~90-method tkinter App. Importing the strategy drags in tkinter/matplotlib/bs4 at module level — the cron path pays the GUI tax.

### 3.2 Duplication (the big ones)
- **`swing_trader_quiet.py` is a 97%-identical fork** — diff is 211 lines, all of it expressible as one `POSTSCAN_QUIET` flag. Every strategy fix since the fork silently exists on only one side. Single largest maintenance liability.
- **Headless scan copy-pastes the ~90-line Phase-1 scan loop** (swing_headless_scan.py:124–212 vs swing_trader.py:3128–3165) incl. the 25-key indicator snapshot; a third partial copy in `_quick_score`. A new indicator key added in one place silently changes scores between GUI and headless.
- **Two score-cap implementations already disagree**: `score_and_assign` clamps bt_adj to [−20,+10] (:307) vs `DataManager.cap_score` [−10,+10] (data_layer.py:919).
- ATR-exit math recomputed in 5 places incl. hardcoded again in the dashboard; signal tiers are emoji-string-matched in ≥8 places + JS; config values live in CFG, data_layer DEFAULT_CONFIG, and build_journal_data.py with nothing reconciling them.

### 3.3 Correctness risks
- **Hardcoded UTC−4 in three places** (:157, :925, :3850) — during EST (Nov–Mar) every market-hours computation, auto-scan window, the 16:30 nightly trigger, and extended-hours detection are off by one hour. (`_monitor_loop` uses pytz correctly — inconsistent within one file.) Also 9:30–9:59 is misclassified as extended hours, which feeds a scoring bonus path.
- **Network fetch on the GUI thread, run twice** in `_detail`'s consensus tab (:3586–3587) — the known thread_context_audit bug class.
- Bg threads mutate `self._scan_res`/`r["price"]` while the GUI renders them; `Events._cache`/`Arb._spy` are unlocked class globals; `Events.check` has a latent NameError that silently kills earnings-date population.
- **37 `except: pass` swallows** in swing_trader.py alone — including the entire Friday nightly backtest (:3890) and every tracker/cache save. A dead nightly job or dead persistence is currently invisible, contradicting ENGINEERING_PHILOSOPHY's detection-over-prevention rule.
- `CFG` is mutated temporarily by `_bt_run_sweep` (:825) — a sweep concurrent with a scan changes live gate thresholds mid-scan.
- "Bars held" computed as calendar days while the comment says trading days (:3215) — momentum speed diluted ~40% over weekends.

### 3.4 Tests
One real test file (`test_ohlcv_cache.py` — good) plus two inline self-tests. **Zero tests on the load-bearing strategy code** (`_buy_rules`, `score_and_assign`, `passes_gates`, `add_indicators`, `_bt_walk`).

### 3.5 What's healthy
data_layer.py (atomic writes, schema migrations, self-test), ohlcv_cache.py, swing_web_reader.py, swing_flag.py, and the headless scan's import-don't-reimplement approach. The debt is concentrated in the two pre-philosophy GUI monoliths; ADR-1's direction is right.

---

## 4. THE PLAN — phased, ordered, effort-tagged

### Phase 0 — Stop the data loss (this week; small, safe, forward-only)
Every item here loses irrecoverable data daily until done. None touches strategy logic.
1. Add ATR/Close to `_TA_PERSIST_KEYS` (D1). **S**
2. Wire real `exit_regime` into close telemetry (D2). **S**
3. Update excursion (MFE/MAE) from the scan's position-monitoring block (D3). **S**
4. Atomic writes + backup for signals.json and the other bare `json.dump` sites — reuse `data_layer._save_json`; remove the `except: pass` on saves (§2.2). **S**
5. Log non-BUY tiers + score decomposition + real passes_gates + spy_price (D4/D5). **S–M**
6. Fix partial-close P&L fold-in (D6). **M**
7. Sync scan archives + headless results into trading-data (D7). **S**
8. Fix hardcoded UTC−4 with one `zoneinfo` helper (behavior will change, correctly — log it). **S**
9. Replace the ~10 worst error swallows with log lines (nightly backtest first). **S**

### Phase 1 — Decide whether the edge is real (next 2 weeks; analysis, no live-code change)
10. **Benchmark restatement:** per-trade excess return vs SPY and QQQ over identical holding windows, for all live trades and backtest trades; re-state the 3%/mo bar and velocity buckets in excess terms. All data is already local. *This single analysis decides whether there is any alpha at all.*
11. **Execution-honest backtest rerun:** next-day-open entries + 5–10bp haircut on the 526-signal baseline. PF ≥ ~1.15 → entry edge probably real; PF ~1.0 → the "edge" is the unmodeled overnight bounce.
12. **Portfolio risk series:** daily NAV/exposure/beta/max-drawdown from the Fidelity CSVs; M9 pairwise correlation of concurrent positions → informs a concurrency/sector cap.
13. Board hygiene: downgrade IV-2 (velocity), IV-6 (sizing instinct), and the −8% cliff to Suspected; stamp the swing baseline with the survivorship caveat Part II already carries.

### Phase 2 — Risk controls (before any capital increase)
14. **Hard catastrophic stop** (e.g. −15/−20% per position — by their own floor curves, −12%≈no-floor in cost) + **earnings-hold rule** + **sleeve-drawdown kill-switch** (e.g. −12% from high-water → flat, post-mortem before resuming).
15. Pre-register the exit contradiction for Milestone 2: time-stop and velocity-take tested *as rules* on post-registration trades, not inferred from discretionary buckets.

### Phase 3 — Code health refactor (after a golden test exists)
16. **Golden-file test** for the scoring pipeline (fixture OHLCV → indicators → scores → signals, pinned outputs). Prerequisite for everything below. **M**
17. Kill the `swing_trader_quiet.py` fork (port the 10-line flag, delete the file). **S**
18. Extract a GUI-free `swing_core.py` (config, indicators, scoring, gates, pipeline, regime/arb/events, setup/momentum) imported by GUI + headless; acceptance test = identical headless scan output before/after. **M**
19. De-duplicate the Phase-1 scan loop + indicator snapshot (one `scan_one_symbol()`); reconcile the two score caps (canonical = score_and_assign's [−20,+10]); collapse the two backtest engines question into one documented engine. **M**
20. Single-writer discipline for state.json (headless uses a read-only stub or a cross-process lockfile); fix the `_detail` GUI-thread fetch; shared-config module for the dashboard's hardcoded constants + a parity test for `classify()`; retire the stale inbox fork; bound file growth. **S–M each**

---

## 5. RECOMMENDATION

1. **Do not scale capital now.** Stay at clean-trial sizing ($100/trade). The measured edge is consistent with zero net alpha over same-beta index exposure until Phase 1's two analyses say otherwise.
2. **Ship Phase 0 immediately** — it's small, safe, and every day of delay destroys data the later analyses need. Items 1–3 are the swing equivalents of the LEAP rev_confirmed fix and belong in the same "one stable session" batch discipline already planned for the LEAP logger.
3. **Run Phase 1 before Milestone 2 (~Aug).** The benchmark restatement (#10) and execution-honest rerun (#11) are the two cheapest experiments with the highest information value in the entire program. If the excess-return restatement shows the live P&L ≈ beta, fold the swing effort into the better-validated medium-horizon lane and keep swing as paper-trading until Milestone 3.
4. **Add the tail-stop and kill-switch regardless of edge verdict** (Phase 2). The program has never seen a bear regime; the controls cost little expectancy by its own floor curves and are what let it survive the regime the data lacks.
5. **Refactor only behind the golden test** (Phase 3). The monolith is a liability, but strategy-touching refactors without a pinned-output test violate the program's own one-variable-at-a-time discipline.
6. **What would upgrade the verdict to "real edge":** Milestone-2+ out-of-sample confirmation on post-registration trades, in excess-of-benchmark terms, under the honest execution model (~≥100 further trades or a second backtest year including a drawdown regime); the baseline surviving a survivorship-honest universe; one caution/danger-regime period with controlled drawdown.

---

*Full agent-level evidence (file:line for every claim) is preserved in the three underlying audit reports; this document is the synthesis. Counts measured directly from the artifacts on 2026-07-06.*
