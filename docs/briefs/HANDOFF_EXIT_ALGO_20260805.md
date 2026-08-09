# HANDOFF — build an exit algorithm that mimics and improves Michael's discretionary selling
**Created 2026-08-05 for a NEW dedicated session. Self-contained: assume no memory of prior conversations.**

---

## 0. The goal, stated precisely

Michael exits trades discretionarily and **his exits are demonstrably good** — better than the system's own targets. Build an exit algorithm that (a) reproduces the behaviour, then (b) improves it.

**This is behaviour cloning of a skilled human, not exit optimization from scratch.** That distinction drives everything: the target variable is *Michael's decision*, and the benchmark to beat is *Michael's realized result* — not a theoretical optimum.

The system already has mechanical exits (2.0×ATR stop, TP1 at 2.0×ATR, TP2 at 3.0×ATR, 21-bar time-stop). **He overrides them and does better.** The algorithm's job is to learn the override.

---

## 1. The evidence that motivates this (all computed 2026-08-05, reproducible)

### 1a. He systematically exits before his own targets — and it works
From `history.json` (58 app-tracked closed trades, 29 with a recorded "Took profit" decision):

> **21 of 29 take-profit exits landed BELOW the system's own TP1 price.** Only 8 reached or exceeded it.

That is not noise or sloppiness. It is a consistent, deliberate pattern, and the results below say it is correct.

### 1b. His fast exits dramatically outperform on capital velocity
From `m2_enriched.json` (125 broker-reconciled closed trades):

| | fast trades (hold 1–3d) | slow trades (hold 4d+) |
|---|---|---|
| n | 17 | 108 |
| mean return | +3.59% | +2.75% |
| median return | +4.12% | +2.98% |
| win rate | 82.4% | 75.0% |
| avg hold | 2.4 days | 16.8 days |
| **return per day of capital** | **+1.49%** | **+0.16%** |

**9.1× the daily rate.**

### 1c. He is not exiting early — he is exiting near local tops
For his 17 fast trades, price action in the **5 sessions after he sold**:
- mean **−2.29%**, median −0.63%
- 8 of 17 fell more than 1%; only 6 rose more than 1%

Across all 122 trades with post-sale coverage: mean drift after exit **−0.51%**; 48% reversed >1%, only 36% continued >1%. **The "leaving money on the table" hypothesis is not supported.**

### 1d. Holding does NOT beat his fast exits (a correction to an earlier wrong conclusion)
An earlier analysis claimed "holding wins" by comparing against a mechanical "take exactly 1%" rule — a strawman Michael never uses — and by reporting **means** inflated by a few large winners. The honest comparison uses medians:

- His actual fast exits (1–3d): **median +4.12%**
- Trades up ≥1% at day 1 and held past day 3: **median +2.38%**

His fast exits beat holding by ~1.7pp in a seventh of the time.

### 1e. Day-1 selling has never been tested because he has never done it
Shortest hold in 125 trades = **2 days**. Zero day-1 exits. Any day-1 rule is extrapolation, not validation.
For context, of 28 trades up ≥1% at day 1: median +1.70% that day → **+2.75% by day 3** (15 improved, 12 gave back). A 1% day-1 exit would capture less than his demonstrated day-2-3 practice.

**Michael's own read: his gut number (1%) is probably too LOW, not too early. Demonstrated sweet spot ≈ +3–4% within 2–3 days.**

---

## 2. THE CENTRAL DATA PROBLEM — read before planning anything

**The reason he sells is mostly not recorded.** Exit-reason values across all 58 app-tracked trades:

| reason | count | usable for learning? |
|---|---|---|
| "Took profit" | 29 | partially — no *why now* |
| "journal sync: real exit per broker CSV" | 17 | **no** — placeholder, decision unknown |
| "reconciliation: already closed per journal" | 8 | **no** |
| "journal sync manual repair" (GOOG alias) | 3 | no |
| "Stop loss hit" | 1 | yes |
| "Manual exit" | 1 | no detail |
| "Other" | 1 | no |

Also note: `history.json` has **58** trades; `m2_enriched.json` has **125**. The app only ever saw a subset — the rest were reconciled in from broker CSVs and carry **no decision metadata at all**.

**Consequence: there is currently no clean label set for "why Michael sold."** Supervised behaviour cloning on decision *reasons* is not yet possible. Two paths:
1. **Learn from revealed behaviour** — treat the exit *timing and price* as the label, ignore the missing reason. Viable now.
2. **Fix the instrumentation first** — capture the reason at decision time going forward (see §6). Better long-run, slow to pay off (~38 new trades/milestone).

Recommend doing (1) now and starting (2) immediately in parallel.

---

## 3. Data inventory (paths, contents, limits)

| File | Contents | Limits |
|---|---|---|
| `/workspace/trading-src/analysis/m2_enriched.json` → `["enriched"]` | **125 closed trades**. Keys: ticker, buy_date, sell_date, buy_price, sell_price, qty, hold_days, pl_pct, entry_score, entry_n_conditions, entry_conditions[], entry_move_speed, entry_atr, **mfe_pct** (max favourable excursion), **mae_pct** (max adverse), time_underwater_pct_of_hold, hit_watch_cliff, recovered_10bar, cohort ("NEW"=38 out-of-sample), post_regime_break, held_through_earnings, held_through_exdiv, days_to_next_earnings_at_entry, entry_regime | Broker-reconciled across 39 rolling-90-day exports. No exit reason. No intraday path. |
| `/workspace/trading-data/swing/history.json` → `closed_trades` | **58 app-tracked trades** with `entries[]`, `exits[]` (price, shares, timestamp, **reason**), `suggested_exits{stop_loss_atr, take_profit_1r, take_profit_2r}`, `outcome{status,pnl_usd,pnl_percent,hold_time_days,avg_exit_price}` | Only 32 have a meaningful reason. Only 1 trade has multiple exit fills (no scale-out history to learn from). |
| `/workspace/trading-data/swing/barchart_synth.json` | **5,276 rows, 2026-04-13 → 07-07**, daily sampling (65 dates, 151 symbols): date, symbol, price, synth_pct, synth_signal, live_pct, live_signal | **Best daily price source available.** Ends 07-07. |
| `/workspace/trading-data/swing/synth_daily.json` | 3,361 rows, 2026-07-06 → 08-04 (22 dates) | Only ≥07-16 is "close-era"; earlier rows captured at market OPEN (cron TZ bug). |
| `/workspace/trading-data/swing/signals.json` | 826 signals, outcomes at **7/14/21 days only** | Entry-side; no daily path. |
| `/workspace/trading-data/swing/state.json` | `active_trades`, config | Live positions. |
| `~/.michael_swing_ohlcv_cache/` (**on the Dell, not in the cloud sandbox**) | Per-ticker **daily OHLCV parquet**, ~400 days | **The single most valuable asset for this project** — real daily bars. Not accessible from the cloud session; the Dell can use it directly. |

**Environment:** cloud sandbox has python3 **stdlib only** (no pandas/numpy/scipy), no network, no yfinance. Heavy modelling belongs on the Dell.

---

## 4. Methodological rules — NON-NEGOTIABLE

These were paid for with six retracted findings on 2026-08-05. Violating them reproduces the same failures.

1. **Mechanical-overlap check before any predictor→outcome claim.** If the predictor is measured intra-trade or derives from the outcome, it is invalid or must be reported incrementally. `hold_days` is intra-trade — bucketing by realized %/day and reporting win rate is tautological (rate>0 ⟺ pl>0 by construction). **A fixed threshold set in advance (e.g. "exit at +3%") is NOT tautological** — that is the legitimate form.
2. **Report intervals, not point estimates.** Every comparison gets an observed effect plus a confidence interval computed from the data. No assumed effect sizes.
3. **Prefer medians for skewed outcome data.** A mean carried by two big winners produced one of the retracted conclusions.
4. **Never compare against a strawman.** Benchmark against *what Michael actually does*, not a mechanical rule he has never used. This error caused the "holding wins" mistake.
5. **Permutation-test any threshold found by searching.** Searching many cutoffs and reporting the best produced two retractions (p=0.068, p=0.626).
6. **Check what data exists before analysing.** The single costliest error of 08-05: an analysis ran on a 14-date file while a 65-date file sat unused, and the conclusion reversed sign when corrected.
7. **Sweep the population definition too** — it is a parameter like any other.
8. **Direct calculations over the full book need no interval; sample comparisons do.** Keep the distinction explicit.
9. **n is small.** 125 trades, 17 fast ones, 29 recorded decisions. Expect "cannot answer" often, and say so rather than inferring.

---

## 5. Suggested work plan

### Phase A — characterise the behaviour (cloud-feasible now)
1. Reconstruct each trade's daily path from `barchart_synth.json` + `synth_daily.json` (Dell: use the OHLCV cache instead — far better).
2. For each exit, compute the **state at the moment of sale**: days held, current gain, MFE so far, drawdown from peak, distance to TP1/TP2/stop, recent momentum, market regime.
3. Ask: **what distinguishes the trades he sold fast from those he held?** Match on entry characteristics; the difference should be in post-entry path.
4. Quantify the override: for the 21 below-TP1 exits, how far below, and what did price do afterwards?

### Phase B — candidate rules
Express the behaviour as testable rules, e.g.:
- "Exit when gain ≥ X% AND days ≤ N" (his revealed pattern ≈ +3–4% in 2–3 days)
- "Exit when the move stalls" (gain flat or fading over k days)
- "Exit at a fraction of TP1 when reached quickly"
- Trailing variants; partial scale-out (**note: only 1 trade in history has multiple fills — no precedent to learn from**)

### Phase C — evaluate honestly
Backtest each rule on the 125 trades **and** on the 826-signal population (n=193 deduped). For each report: median and mean return, win rate, **return per day of capital**, drawdown, and **the counterfactual specified in full** (what happens to the position, when, where the capital goes, whether another signal is guaranteed, costs).
**Benchmark = Michael's actual results, not zero.** A rule that beats "do nothing" but loses to Michael is a failure.

### Phase D — out-of-sample
The `cohort == "NEW"` field marks 38 trades that were out-of-sample for the previous milestone. Preserve a genuine holdout. At ~38 new trades per milestone, expect slow resolution — do not manufacture confidence.

---

## 6. Instrumentation to start immediately (parallel to analysis)

Every day of delay costs data that cannot be recovered retroactively:
1. **Capture the exit reason at decision time** — a required field, not free text: took-profit / stalled / risk-off / needed-capital / stop / time / other + a one-line note.
2. **Capture the state at exit** (price, gain, days held, MFE, drawdown-from-peak, distance to TP1) so future analysis does not have to reconstruct it.
3. **Join the daily OHLCV cache to the signal/trade log** — a brief for this already exists (`CODE_HANDOFF_daily_outcome_path_20260805.md`); it is additive, uses an existing on-disk cache, and requires no new network calls.
4. If scale-out is ever considered, **log partial fills properly** — there is currently no data on it.

---

## 7. Open questions for the new session

1. Is the fast-exit advantage **selection or timing**? He *chose* which to sell fast. If it is selection skill, an algorithm may not reproduce it — that is the central risk to this whole project and should be tested first, not last.
2. Does the pattern survive the mechanical-overlap objection? (A fast TP1 hit produces both a short hold and a good return.) The post-sale decline evidence (§1c) is the strongest independent support, since it cannot be produced by the exit rule.
3. What is the right objective — total return, return per day of capital, or risk-adjusted? These give different answers. **Michael's stated interest is capital velocity**, but this must be traded against needing a fresh signal to redeploy into.
4. Is there a stall signature detectable *before* the price gives back gains?
5. Should the rule differ by entry quality, regime, or ATR?

---

## 8. What NOT to do

- Do not optimize a threshold by searching many values and reporting the best without a permutation test.
- Do not use `hold_days` or any intra-trade variable as a predictor of trade outcome.
- Do not benchmark against a rule Michael does not use.
- Do not trust `synth_daily.json` alone for price paths — `barchart_synth.json` is far longer and was previously overlooked.
- Do not report a mean without also reporting the median for these distributions.
- Do not treat the 21-bar time-stop, −8% cliff, or 2.0×ATR multiples as validated — **they are hand-picked constants that have never been swept.** (M2 did test exit-floor variants and found the curve noisy in backtest and smooth in live; nothing was adopted.)
- Do not promote an exploratory finding to a rule inside one session. Prior context: a "Suspected → Proven" ledger exists and requires confirmation on data that did not exist when the hypothesis was formed.

---

## 9. Prior context worth reading (optional)

In `stock-tracker` repo, `docs/briefs/`: `EXTERNAL_REVIEW_PACKET_20260805.md` (the six retractions and why), `RESEARCH_PROTOCOL_V2_20260805.md` (the discovery/adjudication split), `CODE_HANDOFF_daily_outcome_path_20260805.md` (the daily-bars join).
In `trading-src`: `docs/findings/swing_analysis_milestones.md` (M1/M2 milestone reports — M2 is methodologically sound and is the model for rigour), `docs/planning/STANDING_ANALYSIS_SPEC_20260703.md`.
