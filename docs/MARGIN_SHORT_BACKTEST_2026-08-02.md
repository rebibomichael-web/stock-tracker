# Margin Debt & Short Interest vs. the Swing/LEAP Programs — Backtest (2026-08-02)

Question: should market-level margin debt or per-stock short selling be factored
into the swing or LEAP program? Neither is currently used anywhere in the
four-repo system (audit of stock-tracker + trading-suite code, 2026-08-02).
This backtest measures whether they *would* have helped, using free data only.

Companion script: `docs/backtest_margin_short.py` (self-contained; stdlib +
openpyxl; fetches everything itself — no repo data dependencies).

## Data sources (all free, all verified working from a remote session)

| Source | Coverage | Access |
|---|---|---|
| FINRA margin statistics (monthly debit balances) | Jan 1997 – Jun 2026 | `https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx` — despite the 2021 URL this is the live, updated file |
| FINRA consolidated short interest (bi-monthly, per stock, incl. days-to-cover) | Dec 2017 – present | `POST https://api.finra.org/data/group/otcmarket/name/consolidatedShortInterest` — **no auth required** |
| Daily OHLCV | full history | Yahoo v8 chart API via plain curl (yfinance's curl-cffi TLS impersonation is rejected by the session proxy; plain curl works) |

Look-ahead control: margin month M treated as known at month-end + 25 days
(FINRA publishes ~3rd week of M+1); short interest settlement date + 12 days.

## Method

Proxy for the swing program's dominant fired combo (per
`SWING_AUDIT_2026-07-06.md`, an oversold-bounce signature): **RSI(3) ≤ 20 AND
close ≤ lower Bollinger(20,2σ)**. Entry at next day's open (the audit flagged
same-close entry as optimistic). Outcomes = +7 and +21 trading-day returns.
Signals deduped per ticker (5-day cooldown). 2,381 signals total.

Universes: long-history liquid set (AAPL MSFT AMZN NVDA AMD NFLX ORCL MU DE
TSLA GOOGL META NOW CRWD) for the 1998–2026 margin test; the 16-ticker
watchlist for the short-interest test (2018–2026).

## Results

### Test A — bounce outcomes by margin-debt YoY growth (1998–2026, n=1,594)

| Margin YoY at signal | n | WR 7d | avg 7d | avg 21d |
|---|---|---|---|---|
| ≥40% (extreme) | 113 | **63.7%** | **+3.10%** | +7.13% |
| 20–40% (hot) | 511 | 50.1% | +0.37% | +1.90% |
| 0–20% (normal) | 491 | 60.3% | +1.63% | +2.37% |
| <0% (deleveraging) | 479 | 56.2% | +1.17% | +2.92% |

**Margin froth does NOT hurt 7-day bounce trades — the extreme bucket was the
best.** Extreme margin growth coincides with melt-up phases where dips get
bought aggressively. Wiring margin debt into the swing regime filter as a
suppressor would have *hurt* the program. Non-monotonic (20–40% is the worst
bucket), so it's not a clean dial either.

### Test B — SPY forward 12-month return by margin YoY bucket (LEAP horizon)

| Margin YoY | months | avg fwd 12m | median | % positive |
|---|---|---|---|---|
| ≥40% (extreme) | 25 | **−7.9%** | **−10.1%** | **20%** |
| 20–40% (hot) | 88 | +9.4% | +12.6% | 83% |
| 0–20% (normal) | 113 | +12.3% | +13.5% | 89% |
| <0% (deleveraging) | 104 | +12.7% | +16.5% | 75% |

**June 2026 margin YoY is +49% — inside the extreme bucket.** Caveat: the 25
extreme months are essentially three episodes (1999–2000, 2007, 2021), all
followed by drawdowns; overlapping 12-month windows mean the effective sample
is ~3, not 25. Directionally strong, statistically thin.

### Test C — bounce outcomes by days-to-cover at signal (watchlist, 2018–2026, n=547)

| Days-to-cover | n | WR 7d | avg 7d | avg 21d |
|---|---|---|---|---|
| <1.5 (light) | 168 | 51.2% | +1.01% | +4.44% |
| 1.5–3 | 202 | 55.4% | +1.08% | +3.37% |
| 3–6 (heavy) | 114 | 53.5% | +0.60% | +2.08% |
| ≥6 (crowded) | 63 | 55.6% | **+3.14%** | **+8.79%** |

Crowded shorts + oversold = fattest bounces (squeeze fuel), but the edge is in
the mean, not the median (fat right tail), and n=63 with se 1.45% on the 7d avg
means it is suggestive, not proven.

## Conclusions

1. **Margin debt as a swing-timing input: rejected.** At the 7-day horizon it
   would have suppressed the program's best periods.
2. **Margin debt as a LEAP regime dimmer: supported (directionally).** High
   YoY growth (current: +49%) historically preceded negative 12-month SPY
   returns — exactly the horizon of long-dated calls. Sensible use: size-down /
   longer-dated / raise score threshold when YoY ≥ 40%; never as a swing gate.
3. **Days-to-cover: log it, don't score it yet.** Add it as an
   indicator-profiler dimension and a scan display column; promote to a scored
   condition only if forward stats keep confirming the crowded-short tail.

## Deep dive (same day): episodes, streak age, deceleration, sectors, per-stock

Companion script: `docs/margin_deep_dive.py`. Highlights (SPY = S&P 500 proxy;
all signals publication-lagged):

**Episodes (YoY ≥ 40%).** Sustained episodes: 1999-10→2000-09 (peak 80%),
2007-05→10 (peak 63%), 2021-01→10 (peak 72%), and 2026-04→ongoing (peak 54%
so far). The market peak came 0 to +9 months AFTER the YoY peak (2000: same
month; 2007: +2; 2021: +9), with 24-month drawdowns of −36%, −55%, −25%.
Two single-month blips (1998, 2010) were base-effect artifacts and benign —
duration matters, not just the print.

**Streak age (is month 6 different from month 1?).** Yes:

| streak age | n | fwd 3m avg | fwd 6m avg | fwd 12m avg (%pos) |
|---|---|---|---|---|
| months 1–3 | 17 | +1.0% | +1.4% | −6.4% (29%) |
| months 4–6 | 7 | +2.0% | +2.5% | **−11.8% (0%)** |

Near-term returns stay positive deep into an episode (melt-up), while the
12-month outcome worsens with age. **Current streak: month 3** (Apr–Jun 2026).

**Deceleration trigger.** First month YoY drops back below 40% after ≥2
extreme months — fired 4× (Jun 2000, Nov 2000, Oct 2007, Aug 2021): fwd 12m
avg −19.6%, 0% positive. Historically the cleanest "top is in" margin signal.
Next test: July 2026 print, due ~Aug 20.

**Sectors (fwd 12m in extreme months vs all other months — "froth drag").**
Comm Svcs −46pp, Nasdaq-100 −40pp, Semis −39pp, Tech −38pp, Small caps −24pp,
S&P −20pp, **Transports −17pp (mid-pack)**, Industrials −16pp, Financials
−13pp, Staples +1pp, Utilities +2pp, **Energy +14pp** (late-cycle commodity
strength). The unwind concentrates precisely where leveraged growth money
lives; defensives are immune and energy historically benefited.

**Per-stock froth drag (fwd 12m extreme vs other).** Negative for all 16
tested — CRWD −91pp, META −87pp, TSLA −76pp … DE −18pp (least). Recent IPOs'
samples are dominated by the single 2021 episode; treat rank, not magnitude.

**Per-stock margin data does not exist publicly** — FINRA publishes only the
aggregate. Closest per-stock leverage lenses: days-to-cover (in kit),
options open interest, single-stock leveraged ETF AUM (no clean free feed),
securities-lending utilization (paid). Empirically the aggregate unwind
expresses itself through high-beta growth names (tables above).

## Leveraged ETFs & options as per-stock leverage gauges (same day)

Companion script: `docs/letf_leverage.py`. Single-stock leveraged ETFs exist
for 12+ watchlist names (TSLA and NVDA have five each, oldest from mid-2022);
the index complex (TQQQ/SQQQ/SOXL/... 13 funds) trades back to 2006-2010.
Gauge = L-ETF dollar volume / benchmark (SPY+QQQ+IWM) dollar volume, 21d
smoothed, z-scored vs trailing 252d (point-in-time).

**Key finding — it measures the opposite phase from margin debt.** Total
L-ETF share YoY correlates NEGATIVELY with margin-debt YoY (peak r=-0.36 with
ETF activity leading margin by ~1 month): leveraged-ETF activity (bull+bear
funds together) spikes during volatility/deleveraging, just before/as margin
debt rolls over. It is a fast daily stress gauge complementing the slow
monthly leverage stock — not a duplicate of it.

**Actionable for swing — the frenzy flag.** Bounce-signal outcomes by total
L-ETF intensity z at signal (2011-2026):

| z band | n | WR 7d | avg 7d | avg 21d |
|---|---|---|---|---|
| z < 0 | 659 | 56.6% | +1.09% | +3.07% |
| 0–1 | 262 | 61.8% | +2.09% | +6.08% |
| 1–2 | 186 | 59.1% | +1.92% | +1.62% |
| **z >= 2 (frenzy)** | 201 | **47.3%** | **+0.62%** | +1.83% |

At z>=2 the bounce edge collapses to a coin flip. Computable daily in real
time from free Yahoo volume — unlike margin debt. Best new-input candidate so
far. Bull-only and bull/bear-ratio variants tested: noisier, non-monotonic.
No 12-month market-timing power in any variant (all bands +14-17% avg fwd
12m, 2010-2026 secular bull) — margin YoY keeps the LEAP-horizon role.

**Current readings (2026-07-31):** total L-ETF share 33% of benchmark $vol,
z=+1.7 (elevated, below frenzy); bull/bear ratio 2.53 (z=+0.6). Per-stock
leverage attention (L-ETF $vol / underlying $vol, 20d): PLTR 9.4% (z+1.2,
rising — most levered name on the watchlist), TSLA 6.1% (falling), MU 3.7%
(rising), NVDA 3.7% (falling), all others <2%. Per-stock signal overlays
(2023+) have n<=15 per name — log nightly, let the profiler accumulate.

**Options volume:** no free historical per-stock source exists (OCC endpoint
404, Cboe put/call archives access-denied). Two viable paths: (a) log Yahoo
option-chain snapshots (volume/OI/put-call per name) nightly — free, starts
accruing history immediately; (b) Alpha Vantage HISTORICAL_OPTIONS (free key,
25 req/day, daily chains back to ~2008) — backfilling only the ~550 historical
signal dates would take ~22 days of quota and enable a proper options-volume
overlay test. Mechanical effect note: leveraged funds rebalance in the
direction of the day's move at each close (flow ~ AUM x leverage^2 x move), so
late-day amplification concentrates in exactly the high-attention names above.

## Caveats

- Proxy signature ≈ the real ~24-condition engine's dominant combo, not the
  engine itself (no ATR gate, regime multiplier, breadth penalty, bt_adj).
- No costs/slippage; entries at next open.
- Bucket samples cluster in calendar time; effective n is well below nominal n.
- Long-history universe is survivor-biased (today's winners). Cross-bucket
  *comparisons* are time-based so this mostly cancels; absolute levels are
  inflated.
- Rule changes in flight will improve short data: FINRA proposal (May 2026) for
  weekly short-interest reporting; SEC Rule 13f-2 aggregates publishing since
  ~April 2026.
