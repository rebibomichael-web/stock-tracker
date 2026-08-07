# External review packet — swing-trading analysis session, 2026-08-05
**Prepared for adversarial review by a different LLM. You are asked to find what is still wrong.**

---

## 0. What you are reviewing and what is being asked of you

A retail swing-trading system ("Michael Swing Trader") has a standing analysis protocol. On 2026-08-03 a formal milestone analysis (M2) ran. On 2026-08-05 an assistant (me) ran a series of ad-hoc analyses in response to the owner's questions, reported findings, and then — after the owner challenged the methodology — audited those findings and **retracted six of them**.

**Your task:** review the whole chain adversarially. Specifically:
1. Are the retractions correct, or did I over-retract? (Over-retraction is also a failure.)
2. Are the four "SURVIVED" findings actually safe, or do they have flaws I still haven't found?
3. Is my diagnosis of process-vs-interpretation failure right?
4. Are the four new standing rules sufficient, and are they the right rules?
5. What did nobody check?

**You do not have the data files.** Numbers below are reported as computed; you cannot re-run them. Treat arithmetic as checkable and provenance as assertions. Where you need data to settle a question, say so rather than guessing.

---

## 1. System and data context

**The system:** a Python/tkinter swing-trading app. It scans a ~152-symbol universe, scores each symbol, and fires BUY signals when a score gate (`CFG.min_buy = 70`) and condition gates pass. Positions are managed with ATR-based exits (stop 2.0×ATR, TP1 2.0×ATR, TP2 3.0×ATR) and a 21-bar time-stop.

**Three data sources used in this session:**

| File | Contents | Provenance / limits |
|---|---|---|
| `m2_enriched.json` | **125 closed trades**, buy dates 2026-02-02 → sell dates 2026-07-22. Fields: entry/exit price, hold_days, pl_pct, entry_score, entry_n_conditions, entry_conditions, entry_move_speed, entry_atr, **mfe_pct** (max favorable excursion), **mae_pct** (max adverse), hit_watch_cliff, recovered_10bar, cohort, entry_regime | Reconciled by M2 across 39 broker exports. **Critical:** the broker export is a rolling ~90-day window, so no single export holds full history; M2 built a union with a documented dedup rule. Cohorts: 87 "M1" (sell ≤ 2026-06-30) + 38 "NEW" (out-of-sample for M1's hypotheses). |
| `signals.json` | **826 signal records**, 2026-03-26 → 2026-08-04; 792 have a graded 7d outcome. Fields: date, symbol, price, score, regime, vix, conditions[], outcomes{7d,14d,21d} each {price, change_pct, checked} | **Only three outcome checkpoints exist (7/14/21 days).** Each is a single live-quote poll at that elapsed day. There is no daily path. This is the single most consequential data limitation in this packet. |
| `synth_daily.json` | **3361 rows** of a consensus scoreboard (a Barchart-style 0–100 "pct" + Buy/Hold/Sell verdict) across 22 dates; **2184 rows / 14 distinct dates** in the usable "close-era" | A cron timezone bug meant pre-2026-07-16 rows were captured at market *open*, not close. Only close-era data is usable → **14 formation dates**, and forward-return windows overlap heavily across consecutive dates. |

**The dedup rule** used in every signal analysis (itself audited): sort by date; keep a signal only if that symbol has no already-kept signal in the previous 21 days. This yields n=193 graded (172 with 21d outcomes) from 792.

**The standing protocol (pre-existing, not mine):** findings are logged in a hypothesis ledger as `Suspected`; they graduate to `Proven` only by confirming on trades that did not exist when the hypothesis was formed. Reporting bar n≥20. This protocol is genuinely rigorous — at M2 it killed both of M1's headline findings.

---

## 2. The M2 milestone report (2026-08-03) — not mine, included as context

Methodology frozen: every scoring/exit/regime function imported verbatim from the M1 script. The NEW cohort (n=38) was the only sanctioned out-of-sample path.

**Result: zero hypotheses graduated.** Both of M1's most-confident findings failed out-of-sample:
- M1 said *remove the 21-bar time-stop* (live expectancy 0.873 → 1.868). M2's OOS cohort said the opposite: keeping it 1.989 vs removing 1.555. An independent module (M7) agreed — the 22d+ hold bucket is the only negative-expectancy bucket (44.4% WR, −0.631%, n=27). **Verdict: keep the time-stop.**
- M1 said slower entry velocity predicts better outcomes (r = −0.407). M2 OOS: **r = +0.001**. Not confirmed.
- Shipped ATR×2.0 floor went from local *maximum* at M1 to local *minimum* among ATR variants at M2 (OOS 1.989 vs ATR×1.5's 2.519). Fragile in both directions; nothing adoptable.
- Partially confirmed, best M3 graduation candidate: an oversold-bounce signature, OOS n=21, 76.2% WR, +3.657%. Control arm n=17, three short of the bar.
- New Suspected: ex-dividend drag (+0.517% vs +3.146%, n=20); 22d+ negative bucket; −8% cliff recovery = 36% (n=25).

M2's own meta-observation, quoted: *"the standing analysis is currently better at killing hypotheses than at confirming them, which at n≈38 new trades per milestone is the correct and expected behaviour."*

**Reviewer note:** I consider this report methodologically sound. Please check whether you agree — particularly the population-rebuild caveat (M2's "ALL" column n=125 is not the same population as M1's n=83, so only cohort-restricted comparisons are valid).

---

## 3. What I did on 2026-08-05, and what happened to it

The owner asked a series of questions. I ran analyses on the cached data and reported findings conversationally, labeling them "Suspected" and citing n counts. The owner then made four escalating methodological challenges, the last of which triggered a full audit of my own work.

### 3.1 Findings I reported, then RETRACTED

| # | Claim as reported | Retraction reason | Key numbers |
|---|---|---|---|
| R1 | "A fast start means MORE room to run" — signals reaching +2% by day 7 averaged +10.16% at day 21 vs −7.02% for those that never got there | **Mechanical/tautological.** "Reached +2% by day 7" is arithmetically contained in "cumulative return at day 21." | Fast bucket's day-21 edge = +12.47pp, of which **+13.23pp was already banked at day 7**. Incremental day7→day21: fast **+0.70%** vs rest **+1.45%**. Negative edge at every threshold +0.5%→+6%. |
| R2 | "Riding beats taking profit early" (+10.16% vs +2.00%) | **Logic error, verdict REVERSED.** I compared riding against exiting at +2% when those positions were up +9.47% at day 7. | Corrected: bank real day-7 amount (+9.47%), redeploy 14d at population's own +2.90% → **+12.37% vs riding +10.16%**. Take-and-redeploy wins ~2.2pp at T=+1/2/3/5%. |
| R3 | Consensus board has an anomalous "11–20 shelf" (−1.97pp, breaking an otherwise monotonic contrarian pattern) | **Banding artifact.** I created it by choosing band edges. | Under deciles: score 0–14 = **+0.49pp**, 14–21 = **+0.93pp** — both positive, no shelf. Gone under every rolling window and alternate scheme. |
| R4 | "7+ prior fires" is the data-found optimal split (+21.8pp gap vs my hand-picked 5+) | **Search-selection bias.** | Permutation test (2000 shuffles, same search re-run each time): **p = 0.068**. Null median gap 11.9pp, 95th pct 22.7pp vs observed 21.8pp. |
| R5 | "Fires again within 42 days" → 70.8% WR vs 52.9% beyond (+17.9pp) — presented as a genuinely new dimension | **Search-selection bias, severe.** | Permutation **p = 0.626**. Null median gap **20.1pp** — noise *beats* the observed 17.9pp more often than not. |
| R6 | "Deep-dip checks add, confirmation checks subtract; RSI is just average" — a table of ~20 filter conditions vs a +1.90% baseline | **Multiple comparisons.** 20 conditions tested simultaneously, extremes reported. | Observed max deviation from baseline **14.6pp**; permutation null median max-deviation **14.9pp**; **p = 0.607**. The entire table is indistinguishable from chance. |

### 3.2 Findings that SURVIVED the audit

| # | Claim | Robustness evidence |
|---|---|---|
| S1 | Repeat-firing stocks win more often (0 prior fires vs 5+) | Positive at **every** dedup window: none +8.5pp (n=118/372), 5d +9.0, 10d +12.2, 14d +16.8, 21d +13.8, 30d +12.4, 45d +22.4. **Magnitude varies 3×** — must be quoted as a range. Note the deliberate circularity check: dedup suppresses exactly the frequently-firing stocks the finding is about, yet the effect survives even with dedup *off* (largest n, smallest gap). |
| S2 | Consensus-loved stocks lag (high side of the synth board) | Negative in every binning: deciles 7–10 all negative (−0.66/−0.99/−1.53/−1.00pp); every rolling window ≥45; quartiles, thirds, and 6-band schemes all agree. State version: stable-Buy **−1.42pp excess, 39% up-rate, n=288**; stable-Sell +0.60pp (n=345). |
| S3 | No forced take-profit rule beats as-traded | Continuous sweep of 60 caps, +0.25% → +15%. Best (+13%) = +2.76% vs as-traded **+2.87%**. Nothing beats it anywhere on the curve. |
| S4 | Owner's discretionary quick trades outperform | Was n=10 (unreportable). Widened: ≤4d n=21 +3.42%/81%; **≤5d n=27 +4.24%/85%** vs rest +2.49%; ≤7d n=39 +4.16%/82%. Holds at every reportable definition. |

### 3.3 A data-determined value produced (offered, not adopted)

Sweeping the live constant `stop_m` (currently 2.0), measuring how often the predicted "Swing%" (= stop_m × ATR / price × 100) contains the realized excursion across 125 trades:

| stop_m | mean predicted | covers realized MFE | covers \|MAE\| |
|---|---|---|---|
| 1.0 | 4.52% | 32% | 52% |
| 1.5 | 6.78% | 56% | 70% |
| **2.0 (live)** | 9.05% | **78%** | **77%** |
| 2.5 | 11.31% | 87% | 87% |
| 3.0 | 13.57% | 90% | 90% |

Realized means: MFE 7.03%, |MAE| 5.29%. **Reading:** 2.0 is well-calibrated as a *risk bracket* (~77% containment) but ~1.5 is the honest *target* estimate (predicted 6.78% ≈ realized 7.03%). One multiplier cannot serve both jobs — this is the quantitative case for splitting Swing% into a target estimate and a risk estimate. **Nothing has been changed in the live system.**

---

## 4. Diagnosis offered to the owner (please critique this too)

Asked whether the failure was in the process or in my interpretation, I answered **both, ~70/30 interpretation over process**:

- **Process gaps (real, now fixed):** the protocol had an n≥20 bar, a Suspected→Proven ledger, and OOS confirmation — but **no** multiple-comparison guard, **no** search-selection guard, **no** mechanical-overlap check, and **no** requirement to sweep a parameter before quoting a value derived from it.
- **Pure interpretation failures (no process would catch):** R1's tautology and R2's rigged comparison are logic errors, not missing checklist items.
- **The meta-failure:** the owner asked for *exploratory* pattern-hunting; exploration should yield candidates, not verdicts. I reported exploratory results using the *confirmatory* system's vocabulary ("Suspected", n counts, ledger references), which lent them credibility earned by a protocol I wasn't following. The M2 milestone process is sound and worked; my ad-hoc work operated outside it while borrowing its clothes.
- **The uncomfortable observation:** every correction this week originated from the owner asking a question, not from the process catching itself. The surviving findings survived because they happened to be real, not because anything verified them pre-report.

### The four standing rules added
1. **Sweep every threshold; report the range, not the best value.**
2. **Permutation-test every search-derived cutoff *before* it is reported at all.**
3. **Mechanical-overlap check on every "X predicts Y"** — if X is partly contained in Y by construction, report the *incremental* effect.
4. **Multiple-comparison correction whenever >3 variants are tested.**

---

## 5. Known-unresolved items (do not need rediscovering; do critique)

- **No daily-resolution outcome data.** The grader polls 3 fixed checkpoints. A separate daily OHLCV parquet cache (~400 days/ticker) already exists on disk for backtesting and has **never been joined** to the signal log. A brief exists to fix this; it is not yet implemented. Until then, all velocity/timing work is checkpoint-limited.
- **Synth analysis rests on 14 formation dates with heavily overlapping forward windows.** Effective independent sample is far smaller than the row counts (n=288, n=345, etc.) suggest. I flagged this but did **not** quantify it or re-run on non-overlapping dates. **This is a live weakness in the S2 "survived" finding.**
- **The 21-bar time-stop, −8% cliff, `min_buy`=70, and 10-bar recovery window** are live constants nobody has swept. M2 tested exit-floor variants but not these.
- **`entry_move_speed`** correlates −0.402 with realized MFE (a possible upside-magnitude signal) and `entry_n_conditions` correlates −0.287 with |MAE| (a possible downside signal). Both are single-sample, in-sample, uncorrected for multiple comparisons — i.e. exactly the class of finding that just got retracted elsewhere in this document. **Treat with the same suspicion.** Note `entry_move_speed` is the same field M2 rejected against a *different* target (trade outcome).
- **A regime discrepancy is unexplained:** on signals, "normal" regime shows 67.3% WR vs "bull" 52.6%; M2 examined the same gap on *trades* and called it noise. Unresolved cross-population conflict.

---

## 6. Specific questions for the reviewer

1. **Is R1's retraction right?** I claim the velocity finding is tautological. Counter-argument I did not fully address: even if the day-7 gain is mechanically banked, "reached +2% fast" could still be a real *selection* signal for stronger dislocations. My incremental test shows fast movers gain *less* after day 7 (+0.70 vs +1.45) — but is that just regression to the mean among a sample where the "rest" bucket contains deeply-negative names that bounce? **How would you separate these?**
2. **Is R2's correction right, and is the redeploy leg fair?** I used the population's own average 14-day signal return (+2.90%) as the redeploy rate. Is that the correct counterfactual, or does it assume away execution reality (finding a fresh signal on demand, slippage, correlated entries)? Does the conclusion survive a more conservative redeploy assumption?
3. **Did I over-retract R6?** The permutation test shuffles outcomes across signals, but conditions co-occur heavily (a signal carries many conditions at once), which may inflate the null's max-deviation and make the test too conservative. **Is p=0.607 an artifact of a badly-specified null?** If so, the condition table may deserve partial reinstatement.
4. **Is S1 safe?** The finding survives dedup-window variation, but "prior fire count" and "dedup" are entangled by construction. Is there a residual circularity I've missed?
5. **Is S2 safe given the overlapping-window problem in §5?** I did not correct for it. What is the honest effective n, and does the finding survive?
6. **Is S4 (quick trades) confounded?** Short holds and good outcomes may be linked by the exit rule itself (a fast TP1 hit produces both). Is this the same tautology class as R1, which I retracted — i.e. **am I being inconsistent** by retracting R1 but keeping S4?
7. **Are the four standing rules the right four?** What is the fifth rule that would have caught something these four miss?
8. **Is the 70/30 process-vs-interpretation split defensible**, or is it self-serving in either direction?

---

## 7. Statistical methods used (so you can check them)

- **Permutation tests:** outcomes shuffled across signals (2000 iterations, fixed seed), the *identical* search procedure re-run on each shuffle, p = fraction of shuffles producing a statistic ≥ observed. Used for R4, R5, R6.
- **Win rate** = % of observations with positive return at the stated horizon. **Excess/market-adjusted** = observation's forward return minus the same-day universe mean.
- **Reporting bar:** n≥20 per cell (n≥15 in two places, flagged where used). This bar is itself a preset and was not swept.
- **No corrections for:** overlapping forward windows (synth), autocorrelation, survivorship in the universe definition, or the fact that the same 125 trades / 193 signals were reused across many tests in one session (a session-level multiple-comparisons problem that none of my per-test corrections address). **This last one may be the largest uncorrected issue in the entire packet.**
