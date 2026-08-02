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
