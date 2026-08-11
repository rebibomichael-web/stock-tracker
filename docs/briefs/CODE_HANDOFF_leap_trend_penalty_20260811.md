# CODE_HANDOFF — LEAP trend-awareness: shadow capture + retrospective test

**Origin:** Michael, 2026-08-11, on seeing NKE's 3-year chart while LEAP showed it as STRONG SETUP:

> "3 yr death spiral deserves a negtve impact on points, 3 yr or x time of declines should remove from contention until reversal starts porportionate to decline or downtrend"

**Status:** SPEC ONLY — no live scoring change. Phase A (capture) is a small additive
writer change; Phase B (retrospective test) is read-only analysis. Promotion to live
scoring is a separate, ratified, dated event and is explicitly out of scope here.

**Author:** cloud session, 2026-08-11. **Implementer:** Dell (local Claude Code).

---

## 1. The defect, in one sentence

`ATH Drawdown` pays **maximum points for the deepest decline**, and nothing anywhere
in the scorer asks whether that decline has stopped.

This is the crux, and it is worth stating precisely because it changes what needs
building. The instinct is "add a trend pillar." The actual defect is narrower and
sharper: an existing pillar is **unconditioned on direction**. A stock down 76% from
its high and still falling scores *identically* to a stock down 76% that bottomed six
months ago and has turned. Both get 3/3. The scorer cannot tell them apart because it
never looks.

Verified against `leap/leap_scoring.py` (68 lines, the single source of truth). All
five pillars:

| Pillar | Max | What it actually measures |
|---|---|---|
| ATH Drawdown | 3 | How far below the all-time high — **depth only, no direction** |
| Prem Efficiency | 2 | premium as % of price |
| Leverage | 2 | price / premium |
| S2/S3 Level | 3 | proximity to a monthly pivot support band |
| RSI | 5 | RSI level + MA-bounce signal (the only behavioral input) |

Note the compounding: a heavily-fallen stock has a low absolute price, which
mechanically produces a cheap premium and a high leverage ratio. So **7 of the 15
available points key off "this stock has fallen a long way,"** stated three times.
The code's own comment already concedes two of those three are the same fact
("both are just premium-as-%-of-price cut at four thresholds"). Nothing in the
remaining points asks whether the fall is over.

**There is no trend slope, no moving-average relationship, and no downtrend-duration
term anywhere in the scorer.**

---

## 2. Michael's requirement, decomposed

The instruction contains **three separable mechanisms**. They must not be blurred —
they have different failure modes and are separately testable. Build the capture for
all three; test them as distinct challengers.

| | Mechanism | Effect | Note |
|---|---|---|---|
| **M1** | **Penalty** — negative points scaled to downtrend severity | A 10 becomes a 7 → drops STRONG to MONITOR | Continuous, preserves ranking |
| **M2** | **Veto** — "remove from contention" | Excluded regardless of score | Binary, overrides everything |
| **M3** | **Release** — "until reversal starts" | The penalty/veto lifts on evidence of a turn | Defines what "reversal" means, mechanically |

M1 and M2 are genuinely different products. A penalty keeps a deep-downtrend name
visible and ranked lower; a veto makes it disappear. Michael's wording contains both
("negative impact on points" *and* "remove from contention"), which is normal for a
verbal spec and is exactly the kind of ambiguity that must be resolved by data and
by him, not silently by the implementer. **Do not pick one. Build the measurement,
test both, bring him the numbers.**

"Proportionate to decline or downtrend" also names **two different magnitudes** —
*depth* of decline and *persistence* of downtrend. These are not the same variable,
they are only moderately correlated, and depth is **already scored (positively!) by
the ATH Drawdown pillar**. Test them separately or the result is uninterpretable.

---

## 3. Evidence that motivated it

Live price history pulled 2026-08-11 for the four names LEAP currently rates
STRONG SETUP (score ≥ 10):

| Ticker | Price | % of trailing 3y below 200-MA | 200-MA slope (60d) | Drawdown from ATH | Time since ATH |
|---|---|---|---|---|---|
| **NKE** | 40.99 | **88.4%** | **−14.3%** | −76.9% | 4.8 yr |
| **DIS** | 103.17 | 48.1% | −4.2% | −44.5% | 4.9 yr |
| **AA** | 53.24 | 49.5% | **+15.0%** | −44.0% | 4.4 yr |
| CRWD | 224.44 | 19.8% | +18.1% | −0.3% | 1 day |

The discriminating pair is **NKE vs AA**. Both fell hard off their highs; both are
scored 10/15 STRONG. But NKE has been below its 200-day average for ~88% of three
years with that average *still falling at −14%*, while AA's 200-day average has been
*rising for two months*. One is a decline that hasn't stopped; the other is a
reversal already underway. The scorer sees them as the same trade.

**A note against over-reading this table:** four names, chosen because they are
today's STRONG list, is an illustration of the *mechanism*, not evidence of an
*effect*. It shows the proposed variables separate cases the current scorer cannot
separate. It does not show that separation predicts returns. That is Phase B's job.

**Also flagged (separate bug, not this brief's scope):** CRWD's most recent record
still carries the retired `Time Horizon` pillar, meaning it has not been rescanned
since that pillar was removed in June. Its "STRONG" is stale. `Time Horizon` appears
in **350 of 3,369** records — a retired pillar still polluting the key set. Worth its
own ticket.

---

## 4. Why this question is privileged — it escapes the C1 blocker

The 2026-08-11 protocol review established a hard constraint (its finding **C1**),
which I re-verified independently against the live file:

> `recommendations.json`, 3,369 records. Persisted keys are exactly: `date`, `symbol`,
> `price`, `score`, `signal`, `breakdown`, `leap`, `outcomes`, `barchart_opinion`,
> `furthest_leap`, `premium_stale`, `rev_confirmed`.
> **`ath`, `rsi`, `vs_s2`, `vs_s3`, `ma_signal` are present in 0 of 3,369 records.**

The scorer's continuous inputs are discarded — only bucketed points survive. So most
proposed shadow scorers **cannot be tested retrospectively at all**; they can only
start accruing forward. That is why the mid-basis premium challenger is stuck (its
bid/ask doesn't exist before July, and covers only ~43–49% of records even now).

**This question is different, and the difference is the reason to prioritise it.**
Its predictor is a pure **stock-price** fact — 200-MA level, 200-MA slope, time spent
below it, drawdown depth — and every record stores `date` + `symbol`. Price history
is fully reconstructable from yfinance for any historical date. **The predictor can
be rebuilt for every record already logged.**

This is the same logic that made III-5 (bottom-calling backtest) viable: *"Pure
stock-price backtest, reconstructable from yfinance — escapes the JSON sample wall."*
Same escape hatch, much smaller build.

**Feasibility proven, not assumed.** I reconstructed the 200-MA and its 60-day slope
as of the actual recommendation date for a 6-symbol probe and joined back to the
stored price:

```
AA    rec 2026-06-26  stored $54.02  yf $54.10  (0.1%)  200MA $54.48   slope +25.7%
ADBE  rec 2026-06-09  stored $242.50 yf $237.88 (1.9%)  200MA $300.46  slope -11.5%
BLK   rec 2026-06-26  stored $964.71 yf $964.71 (0.0%)  200MA $1067.01 slope  -1.5%
BMY   rec 2026-06-18  stored $53.97  yf $54.00  (0.1%)  200MA $53.59   slope  +6.2%
CEG   rec 2026-06-11  stored $245.26 yf $246.71 (0.6%)  200MA $320.49  slope  -2.8%
BMNR  ERR ZeroDivisionError
```

Join sane on 5/5 that completed. **`BMNR` is a real edge case, not a fluke** — its
200-MA sixty days prior was near zero (recent listing / explosive re-rating), so the
percentage-slope denominator blows up. BMNR is also the single largest cluster in the
sample (49 records). Handle explicitly: require ≥260 bars of history before computing
a slope, and use an absolute-or-log slope measure rather than a raw percentage change.
**Do not let it silently drop** — a symbol vanishing from one arm is exactly the kind
of silent population change the standing rules exist to catch.

---

## 5. Honest sizing — read this before designing the test

Measured on the live file:

| Population (matured 30d) | Raw n | Symbols | Episodes @30d gap | Episodes @60d gap |
|---|---|---|---|---|
| ALL scored | 1,317 | 115 | **136** | 126 |
| MONITOR+ (≥7) | 1,307 | 115 | **135** | 125 |
| STRONG (≥10) | 521 | 58 | **71** | 62 |

**Raw n is badly misleading and must not be quoted as sample size.** The 521 STRONG
records come from 58 symbols, and the top clusters are BMNR 49, NOW 41, ORCL 28,
SNOW 23, HOOD 21, SOFI 21 — while 11 symbols contribute a single record each. Records
on consecutive days for one symbol carry near-identical predictors *and* heavily
overlapping 30-day forward windows. Declustering to non-overlapping episodes gives
the honest figure: **71 episodes for STRONG, 136 for the full population.**

Using the review's measured LEAP dispersion (σ ≈ 14.59pp at 30d):

| Population | Episodes | SE of arm difference | MDE (1-sided α=0.10, power 0.8) |
|---|---|---|---|
| STRONG only | 71 | 3.46pp | **7.34pp** |
| MONITOR+ / ALL | ~136 | 2.51pp | **5.30pp** |

**What this means, plainly:** on STRONG alone, a trend penalty must move 30-day
outcomes by more than ~7pp to be detectable at all. That is a large effect. Testing on
the full scored population roughly halves the requirement and is the better-powered
choice — at the cost of answering a slightly different question ("does trend predict
outcomes across all LEAP signals?" rather than "does it rescue the STRONG tier?").
**Run both; report both; do not let the better-powered one silently substitute for
the one Michael actually asked about.**

**Single-regime caveat, load-bearing.** Matured 30-day STRONG outcomes span only
**2026-04-03 → 2026-06-26** — one market era, and the LEAP log carries **no regime
field at all** (confirmed: not among the 12 persisted keys). Whatever Phase B finds
is a single-regime result and cannot reach `confirmed` under the protocol's
different-regime clause. Say so in the writeup rather than letting the reader assume
generality.

---

## 6. What to build

### Phase A — capture (small, additive, ships first)

Compute at scan time and persist. All of it derives from the year+ of OHLC the
scanner **already downloads per ticker**, so the marginal network cost is zero.

Per record, under **one namespaced key**:

```python
rec['trend'] = {
    'sma200':            float|None,   # 200-day simple MA
    'price_vs_sma200':   float|None,   # (price - sma200)/sma200 * 100
    'sma200_slope_60d':  float|None,   # log-slope, see §4 BMNR note
    'sma200_slope_120d': float|None,
    'pct_below_sma200_1y': float|None, # % of trailing bars with close < sma200
    'pct_below_sma200_2y': float|None,
    'pct_below_sma200_3y': float|None,
    'days_since_ath':    int|None,     # ath already computed by get_ath_and_52w()
    'days_since_52wh':   int|None,
    'bars_available':    int,          # guard: slope is None if < 260
}
```

**Namespacing is not cosmetic — it is a correctness requirement.** Do **not** add
these keys inside `breakdown`. `breakdown` is consumed by the LM1 pillar census, and
the log already contains a live instance of exactly that corruption: retired
`Time Horizon` in 350 of 3,369 records. One nested `trend` dict keeps the pillar key
set clean.

Implementation notes verified against source:
- `LeapTracker.log()` writes a **hardcoded dict literal with a fixed whitelist**
  (`leap_strategy.py:196-212`). This needs a writer change *and* a signature change —
  it is not a config toggle. Wire the kwarg at all call sites, same pattern as the
  I6 `furthest_leap` change.
- `get_ath_and_52w()` (`:473-478`) already computes both the all-time high and the
  52-week high in one call. Both are discarded today. `days_since_ath` /
  `days_since_52wh` are nearly free.
- `log()` already returns early on *"Don't double-log same stock same day"*
  (`:191-195`), so the record unit stays one-per-symbol-per-night. Good — nothing to fix.
- **Log-size check before shipping:** `recommendations.json` is ~2.8 MB and is
  committed daily to `trading-data`. This adds ~10 floats/record. Measure the delta
  and state it; it should be small, but measure rather than assume.

### Phase B — retrospective test (read-only, no code shipped)

1. Rebuild the Phase-A trend variables **as of each historical record's date** for all
   3,369 records (or the 1,317 with matured 30d outcomes). Cache bars to parquet;
   throttle; tolerate delisted symbols to a `failed_symbols.txt` — reuse the existing
   `swing/ohlcv_cache.py` pattern rather than writing a new fetcher.
2. Verify the join the way §4's probe did: stored `price` vs reconstructed close on
   the record date. **Report the match distribution and drop nothing silently.**
3. Decluster to episodes (§5). Report raw n *and* episode n everywhere, always.
4. Test M1 (continuous penalty) and M2 (binary veto) separately, and both the
   depth and persistence magnitudes separately (§2).
5. Cluster-robust intervals — symbol-clustered bootstrap, per the pinned §6 test #1
   convention. Overlapping forward windows are the real dependency; a plain t-test
   will overstate significance.

---

## 7. Data-determined parameters — sweep, do not preset

Michael's standing directive applies with full force here:

> "all dates and all parameters should be dictated by data not preset"

**"3 years" in the origin quote is his description of what he saw on a chart, not a
parameter to hardcode.** Every one of the following is a knob, and every knob gets
swept with the full range reported — not the best value:

- **Lookback for "% time below"** — 1y / 2y / 3y / 5y, and continuous
- **The moving average itself** — 100 / 150 / 200 / 250-day
- **Slope window** — 20 / 60 / 120 / 250-day
- **The "still declining" cutoff** — slope < 0? < −5%? swept continuously
- **The "reversal has started" definition (M3)** — this is the most consequential and
  least specified: slope crossing zero? price crossing the MA? MA crossing MA?
  *n* consecutive higher lows? Each is a different rule with a different lag.
- **Penalty magnitude and shape (M1)** — linear in severity? stepped? capped?
- **Population definition** — STRONG-only / MONITOR+ / all-scored. **Population is
  itself a parameter** (standing rule #8; the swing dedup window once moved n from
  792→129 and the effect size 3×). Sweep it and report stability across it.
- **Horizon** — 30d primary (1,317 matured); 7d/14d as secondary. 60d has only 323
  matured records and is underpowered.

**Permutation-test any cutoff that is chosen by searching**, before reporting it at
all. This is standing rule #2 and it killed four findings on 2026-08-05. With this
many knobs, something will look good by chance — the permutation test is what tells
you whether it beats noise.

---

## 8. Hard rules

1. **No live scoring change in this brief.** `score_leap()` returns the same value for
   the same inputs before and after. The `/15` denominator, the pillar set, the
   `STRONG ≥ 10` / `MONITOR ≥ 7` ladder, and every existing logged field are untouched.
2. **Additive keys only**, namespaced under `trend`. Old records simply lack the key.
3. **Scoring fingerprint gate:** the 4 `tracker.log` / `_score_history.log` call sites
   must be byte-identical to baseline. Same gate as I6/I7/L3-b.
4. **Static write-proof:** no new writes to any store other than the additive key.
5. **Backup before edit**, `*.bak_TREND_<timestamp>`, as with every prior batch.
6. **Report raw n and episode n together, everywhere.** A raw count of 521 must never
   appear unaccompanied by "71 episodes, 58 symbols, top cluster 49."
7. **Phase B is exploratory.** Under the status ladder it produces `Exploratory`
   findings at most — nothing frozen, nothing OOS-tested, nothing promoted.

---

## 9. What NOT to do

- **Do not hardcode 3 years, or 200 days, or any threshold from this document.**
  They are starting points for a sweep. The origin quote says "3 yr **or x time**" —
  Michael himself left it open.
- **Do not ship a penalty into live scoring because Phase B looks good.** Promotion is
  a separate ratified event. Every prior scoring-adjacent change here (Cheapness
  shadow, L3 mid-basis) shadowed first for months.
- **Do not fold M1 and M2 together** into one "trend adjustment." They are different
  products with different consequences.
- **Do not let BMNR silently drop.** It is the largest cluster and the known edge case.
- **Do not treat the §3 four-name table as evidence of an effect.** It illustrates the
  mechanism only.
- **Do not report a p-value computed as though 521 records were independent.**
- **Do not analyze only the file you happen to open.** The most expensive error of the
  2026-08-05 cycle was using a 14-date file when a 65-date file existed. Before
  starting Phase B, inventory what price/trend data already exists locally — the swing
  lane's OHLCV parquet cache may already hold much of what Phase B needs to fetch.

---

## 10. Decisions for Michael — AFTER Phase B, not before

**None of these block the build.** They are recorded here so Phase B measures what is
needed to answer them, and so nobody resolves them silently by implementation.

1. **Penalty or veto (M1 vs M2)?** His wording had both. My read is a veto for the
   extreme case (NKE) and a penalty for the middle (DIS) — but that is inference, and
   it should be his call once there are numbers.
2. **Does "removed from contention" mean hidden from the scanner, or shown with the
   signal suppressed?** Different UI products. Given the existing air-gap discipline
   (liquidity flags inform, never refuse), suppress-and-label is more consistent with
   how this system already works.
3. **Condition the ATH Drawdown pillar on direction, or add a separate 6th term?**
   Conditioning is arguably the most honest fix since that is where the defect lives;
   a separate term is easier to test and to reverse.

---

## 11. Relationship to existing roadmap items

- **III-5 (bottom-calling backtest, DESIGN-LOCKED, not built)** — same underlying
  question, far larger scope. This brief is a deliberate fast-track precursor: it
  answers "is the downtrend still active?" without building the full HELD/PARTIAL/BROKE
  taxonomy. **RESOLVED 2026-08-11 — III-5 is NOT imminent, so there is nothing to fold
  into and no reason to wait.** Checked directly: the only related code is
  `analysis/bottom_call.py` (87 lines, ad-hoc, last touched 2026-08-04), which is not
  III-5 — it has no swing-low detection, no S1/S2/S3 pivot construction, no
  SNATCH/DURABLE outcome layer and no staging. It does already fetch history for
  score≥10 records, so **read it before writing Phase B's fetcher** — but treat it as
  a starting point, not a dependency. Proceed.
- **LM6 (drawdown-depth conditioning)** — the standing LEAP spec's active module on the
  *same variable*. The protocol review flagged LM6 as the sharpest omission from the §3
  conversion table, and warned this exact hypothesis risks living as both an
  unconverted lens and a new question. **Register this against LM6; do not open a
  parallel ID.**
- **Pillar-power finding (III-4)** — already recorded that ATH Drawdown is
  *"saturated (66% at max)"*. That is the same defect from the other direction: a
  pillar that awards max points to two-thirds of the field is not discriminating.
  This brief proposes what would make it discriminate.
- **Protocol v2 review, C1 / C4** — this brief is written to comply: C1 is the reason
  Phase B is possible here and not elsewhere; C4 is why the shadow key is namespaced.

---

## 12. Deliberate omission

**I did not compute the outcome relationship.** I proved the reconstruction works
(§4), sized the sample honestly (§5), and stopped.

This is not incompleteness — it is the point. Every correction in the 2026-08-05 cycle
originated in a question from the owner, not from the process catching itself, and the
specific mechanism was always the same: look first, then build a test around what was
seen. Leaving the first look to Phase B's pre-registered run is the cheapest available
way to make that run a genuine first look rather than a confirmation of something
already peeked at.

If Phase B finds nothing, that is a real answer and it should be reported as one. The
NKE chart establishes that the scorer is *blind* to trend. Whether that blindness
*costs* anything is a separate question, and it is genuinely open.
