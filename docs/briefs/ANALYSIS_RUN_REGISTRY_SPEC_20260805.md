# ANALYSIS RUN REGISTRY — "every analysis run is loud and clear to the rest of the programs"
**Spec, cloud, 2026-08-05. Michael's directive: "we need to create structure first — any anal[ysis] run is loud and clear to rest of programs." Companion to inbox/BANNER_RUN_BUTTON_SPEC.md (launch side) — this is the completion/announcement side. For the Dell to implement after Michael approves.**

## The problem it solves (this week's demo)
M2 ran 08-03 and produced a rigorous report — and the only system-visible trace was a *stale* banner still saying "Run the standing analysis," because the run's existence lived in a doc footer and an unmade manual `--mark-done`. Michael read the banner as a bug. The run was silent to every program. Same disease class as the silent schedulers, on the analysis lane.

## Design: one registry file, many consumers

### The registry (the only new artifact)
`~/.michael_analysis_runs.json` — **append-only** list of run records (the fidelity_ledger philosophy: prior entries never change). Written by the analysis itself as its LAST step. Schema per record:

```json
{ "run_id": "M2-20260803", "kind": "milestone|s-lane|adhoc-prereg",
  "date": "2026-08-03", "script": "analysis/standing_analysis_milestone2_20260803.py",
  "script_md5": "…", "report": "docs/findings/swing_analysis_milestones.md#milestone-2",
  "status": "awaiting_approval",       // → "approved" | "superseded"
  "headline": "0 graduated · 2 M1 headliners failed OOS · 3 new Suspected",
  "approved_on": null }
```

Rules: writing a record is part of the run (a run that didn't stamp didn't finish — same absent-log lesson); `status` transitions are the ONLY permitted mutation; the file joins the trading-data backup whitelist.

### Consumers (each is a small, separate change)
1. **milestone_banner.py becomes state-aware (the fix for "why banner?").** Three states instead of one:
   - DUE, no unapproved run → today's banner (+ the ▶Run button per the inbox spec).
   - Registry has `awaiting_approval` milestone run → banner flips to: **"⚑ M2 ran 08-03 — report awaiting YOUR review → [Approve & mark done] [Open report]"**. Approve = `--mark-done` + status→approved, stamping `last_analysis_date` with the RUN date (not click date — fixes the drift noted 08-05).
   - Nothing due, nothing pending → no banner (unchanged).
2. **PERFORMANCE learning-loop pill** gains one line from the registry: "analysis: M2 08-03 awaiting approval" / "approved ✓" — the output-side gauge for the analysis lane.
3. **Probe #10 (when built)** treats registry cadence as one more heartbeat: milestone stamp older than ~25 trading days → amber. Run-evidence, not artifacts.
4. **S-lane runs stamp too** (`kind: "s-lane"`, one record per run, headline = modules run + verdict deltas), so the S5 watchlist verdicts arriving in ~2–3 weeks announce themselves instead of waiting to be noticed.

### Explicitly NOT in scope
No change to the 20-trading-day trigger, the frozen methodology, or approval-before-mark discipline (M1 precedent stays — the registry makes the pending state VISIBLE, it does not skip Michael). No auto-run of anything.

---

# Part 2 — M3 SPEC AMENDMENT (proposed): pre-registered curiosity
**Addresses Michael's M2 critique verbatim: "minimal curiosity, i.e. if this and that what then, and had to prod repeatedly … my goal was a very thorough set of criteria that would pull data and then a formulaic analysis looking for patterns; what I got was prod research response instead."**

Add to STANDING_ANALYSIS_SPEC (as amendment 2, alongside the pending ledger amendment):

1. **Branch tables, pre-registered.** Every module M1–M11 + S1–S10 gets an explicit `IF finding crosses X → ALSO run Y` table written into the spec BEFORE the run. Examples seeded from what Michael had to prod for at M2: a band result → automatically run band-free splits AND the same cut on the signal population, not just trades; an exit verdict → automatically run the path-level view (dip depth, rescue rate, day-by-day grid 7/14/21/28); any "insufficient n" → automatically report the n it WOULD need and which future milestone reaches it at current fill rate.
2. **Mandatory curiosity section per module.** Each module's report section ends with "the three questions this result raises," and any of the three computable from already-loaded data MUST be answered in the same run. Not optional prose — a checklist the run self-audits.
3. **The formulaic sweep IS the deliverable.** The session's job is to execute the criteria exhaustively and surface pattern candidates; conversation is for verdicts and new hypotheses, not for extracting the next obvious cut. A milestone report with an empty curiosity section is an incomplete run.
4. **Registry stamping** (Part 1) is a numbered step of the protocol — after the ledger update, before the footer.

Approval path: Michael approves alongside the M2 report + the M3 ledger amendment (one sitting, three approvals). Then a Dell brief implements Part 1's registry + banner consumer; the spec amendments are doc edits.

---

# Part 3 — SEEDED BRANCH TABLE (Michael, 2026-08-05, verbatim list) + answers-now status
| # | Michael's question | Ran now (cloud, backups) | Standing module / needs |
|---|---|---|---|
| B1 | Repeat buy signals — triggered before, multiple times | ✅ 0-prior 57.6% vs 3+-prior 71.4% WR7 (n=118/49) — corroborates S2 on the tracker population | fold into S2; bucket 1–2 prior needs n |
| B2 | Speed exits — "sold XOM +1.5% in a day; what if always ≤2d/+2%?" + all variables | ✅ take-profit grid run (1/1.5/2/3/5%): EVERY cap cuts per-trade return (2% cap: +1.69 vs +2.87 actual); realized ≤2d trades +3.94% n=10 | the "within-2-days" arm + per-DAY efficiency need daily paths → Dell bars cache, M3 module |
| B3 | Every filter component alone + all combos (does RSI work? RSI+MA20?) | ✅ single-condition table + key pairs run (dedup, n≥20 rule) | standing per-milestone module: full pairwise matrix + top-k combos, era-adjusted |
| B4 | Synthetic Barchart score correlation | ✅ close-era: r=−0.113; synth-Sell +1.59% vs synth-Buy −0.53% fwd5 — ANTI-correlation | exploratory only; the pre-registered Aug-18 C1–C3 confirmation stands untouched |
| B5 | Score diff aggressive/normal/conservative | ⚠ unmeasurable yet: mode logged only since 07-17 (normal 73, aggressive 12, rest absent) | either deliberate mode A/B weeks or an offline reclassification study (Dell, scoring engine + bars) |
| B6 | (found while running B5) market-regime gap on SIGNALS: normal 67.3%/+3.58 vs bull 52.6%/+0.17 — but M2 called the same gap "noise" on TRADES | — | cross-population discrepancy → M3 branch |

**Format question ("create a format to be populated, or let data determine?") — BOTH, in that order:** the branch table above is the FORMAT (pre-registered rows, populated every run — that is the discipline that stops dredging); each run may APPEND data-discovered candidate rows, which are not graded in the run that found them — they become pre-registered for the NEXT run. Questions get the same Suspected→Proven ledger treatment as hypotheses.

---

# Part 4 — B4 DEEP-DIG (Michael 08-05: "dig deep — maybe an opposite indicator; consider for sell signals too")
All close-era (post-07-16), in-sample, **Suspected**. ~9 independent formation dates — treat every number as provisional.

| Cut | Result |
|---|---|
| Horizon sweep (low≤40 minus high≥61, raw) | fwd1 +0.41pp · **fwd3 +2.35pp · fwd5 +1.83pp** · fwd8 +0.13pp — a 3–5-day mean-reversion effect that decays |
| Market-adjusted (excess vs same-day universe) | high side robust: 61–80 −1.10pp, 81–100 −1.07pp. Low side NON-monotonic: 21–40 +0.97pp, ≤10 +2.35pp (n=69), **but 11–20 −1.97pp (n=89)** — a shelf that breaks clean monotonic contrarianism |
| Per-date consistency | spread positive on **6/9** formation dates (worst −2.83pp) — not a one-day artifact, not ironclad |
| **Transitions (the opposite-indicator verdict)** | It is a STATE effect more than a flip effect: **stable synth-Buy is the worst state on the board (−1.42pp excess, 39% up-rate, n=288)**; stable Sell +0.60pp (n=345); fresh downgrade→Sell +1.24pp (n=110); fresh upgrade→Buy −0.33pp (n=99) |
| Sub-indicator anatomy | INCONCLUSIVE from data — detail tokens nearly constant in-window (oscillators uniform; B/N encoding ambiguous); needs the synth generator source (Dell) |
| Our buys × synth | fired-while-synth≤40: 75.6% WR7 / +3.97% (n=41) vs fair close-era baseline 69.6% / +3.16% (n=56) — modest lift; the system already self-selects into hated names (only 2 close-era signals fired at synth≥61) → entry gate ≈ redundant; the SELL side is where the value is |

## Proposed pre-registrations → fold into the Aug-18 synth confirmation (join C1–C3)
- **C4 — contrarian cross-section:** on data generated AFTER 2026-08-05, market-adjusted low(≤40)-minus-high(≥61) fwd5 spread. CONFIRM if pooled ≥ +1.0pp AND positive on ≥60% of formation dates. Also grade the 11–20 shelf: real or noise.
- **C5 — synth-state-as-exit ("euphoria headwind"):** stable-synth-Buy state (≥2 consecutive Buy days) excess fwd5 ≤ −0.5pp on fresh data. If C4+C5 both confirm → **DISPLAY-ONLY** badge on the SYMBOL holding pill ("consensus euphoric — historically a 5-day headwind") + sell-context line in DIAGNOSE. Any use beyond display (exit rules, sizing) needs separate out-of-sample validation + ratification — standing no-wiring discipline.
- **Instrumentation rider (→ M2-aftermath Dell bundle):** log synth pct + signal state at SIGNAL time and at EXIT time on every trade/signal record, so S9/M3 can grade synth-as-exit on realized trades instead of state proxies.

---

# Part 5 — VELOCITY / REPEAT-FIRE MATRIX / SWING% ACCURACY (Michael 08-05, round 2)
Michael's exact asks: "look at speed of velocity eg getting 2% in 1 day good over 5 days not as much where is the line" · "'stocks keep firing' too vague — break it down: if stock fired x amount within x time, win% was x" · "look at the swing% prediction — how accurate." All in-sample, close-era/checkpoint-limited, **Suspected**.

## Speed of arrival (proxy for velocity — see caveat)
No daily-resolution price paths are cached anywhere in the backups (`trading-data`/`trading-src`) and this sandbox has no `yfinance` — so the true "+2% in 1 day vs 5 days" question needs the Dell's bars cache. Built the closest honest proxy from the 7d/14d/21d signal checkpoints: bucket by **how many days it took to first cross +2%**, then look at day-21 outcome.

| Arrival speed | n | Day-21 avg | Day-21 win rate |
|---|---|---|---|
| Within 7 days | 76 | +10.16% | 82.9% |
| By day 14 (not 7) | 28 | +5.40% | 89.3% |
| By day 21 (not 14) | 9 | insufficient — monitoring | — |
| Never by day 21 | 59 | −7.02% | 10.2% |

**Finding is the OPPOSITE of the hypothesis:** fast arrival predicts MORE follow-through, not less. Taking +2% at day 7 instead of riding nets +2.00% guaranteed vs +10.16% average riding to day 21 (83% still positive). Same shape at +1%/+3% thresholds (see run output). Realized-trade cross-check (125 trades, pl_pct/hold_days): the 27 fastest movers (>1%/day) averaged +8.64% in 4.4 days — consistent but not independent (rate and outcome share sign by construction, so this check is directional support only, not confirmation).
**Caveats, both real:** (1) checkpoint resolution is 7 days, not 1 day — the exact line Michael asked for needs daily bars, carded for M3/Dell; (2) correlational — signals that hit +2% fast may simply be stronger dislocations overall, not "fast" in a causally distinct sense. Composition vs. causation is unresolved.

## Repeat-fire matrix (real breakdown, not a binary split)
Win rate (7d) by **exact prior-fire count** within **four lookback windows**, at signal time:

| Window | 0 prior | 1 | 2 | 3–4 | 5+ |
|---|---|---|---|---|---|
| 30d | 59% (n=161) | thin (n=6) | thin (n=5) | thin (n=6) | 67% (n=15, thin) |
| 60d | 57% (n=127) | 50% (n=16, thin) | thin (n=11) | thin (n=8) | 71% (n=31) |
| 90d | 58% (n=118) | thin (n=14) | thin (n=12) | thin (n=14) | 71% (n=35) |
| 180d | 58% (n=111) | 38% (n=16, thin) | thin (n=11) | 72% (n=18) | 70% (n=37) |

**The honest line:** only 0-vs-5+ is reportable at any window (n≥15 rule); the gap (~12–14pp) is stable across all four windows, so it's not a window-choice artifact. Buckets 1–4 are individually too thin everywhere — that IS the answer to "where's the line," not a gap to paper over.

## Swing% accuracy
Confirmed from source (`swing_core.py`/`data_layer.py`): **Swing% = 2.0 × ATR / price × 100** (`CFG.stop_m` default 2.0) — a volatility/stop-distance estimate, NOT a directional price-move prediction. Tested against realized MFE (best excursion) / |MAE| (worst) on all 125 trades with `entry_atr`.

- r(predicted swing%, realized MFE) = **+0.565** (decent — predicts upside opportunity reasonably)
- r(predicted swing%, realized |MAE|) = **+0.197** (weak — poor risk-sizing signal)
- Predicted swing% covered the realized MFE 78% of the time, |MAE| 77% — behaves as a **ceiling estimate**, not a point forecast (mean predicted 9.05% vs mean realized MFE 7.03%, |MAE| 5.29%)
- By band, bigger predicted swing → bigger realized move AND bigger trade result (moderate 5–7%: +1.72%/77%WR · wide 7–10%: +2.82%/80%WR · very wide >10%: +4.72%/71%WR) — win rate is non-monotonic; the widest band pays more per trade while winning slightly less often

**Verdict:** the ruler points the right way for upside sizing, is weak for downside sizing, and runs mildly hot as an upper bound rather than a precise forecast.

## Standing-module implications (→ M3 branch table)
Add rows: velocity-bucketed exit study (needs Dell daily bars — flagged, not yet buildable from cloud backups alone); repeat-fire matrix as a standing per-milestone cut (0/1/2/3–4/5+ × 30/60/90/180d); Swing%-band reconciliation (does the predicted-vs-realized relationship hold OOS at M3, and does it differ by regime).
