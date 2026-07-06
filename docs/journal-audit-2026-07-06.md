# Trading Journal Overhaul — Audit, Improvement Plan & Investment Review

**Date:** 2026-07-06 · **Scope:** all four repos — `stock-tracker` (deployed dashboard), `trading-src` (desktop source of truth), `trading-suite` (successor web app), `trading-data` (data backup).

---

## Verdict

The journal's plumbing is far better than a typical personal project — FIFO lot matching with orphan preservation, time-weighted deployed-capital returns, an encrypted dashboard section with a proper encrypt-then-MAC scheme, graceful degradation everywhere. But the audit found **three confirmed correctness bugs in the P&L engine itself**, the finance math is **copied into three repos with zero tests on any copy**, and the canonical file's runtime home is literally `~/Downloads`.

From an investment perspective, the journal measures **activity and return, but not edge**: there is no expectancy, no realized R-multiples, no drawdown, no profit factor, and no benchmark comparison anywhere in the codebase — even though the data needed to compute most of these is already being collected.

**Recommendation, in one line:** fix correctness and consolidate to one tested core *first*, add the edge-measurement metrics *second*, finish the trading-suite migration *third*, and leave UI redesign for last.

---

## System map

```
Fidelity (manual CSV export, 32 overlapping files, 290 unique rows)
        │
        ▼
trade_journal.py  ←── canonical P&L engine (trading-src/journal, 2,068 lines,
        │              GUI + finance mixed, runtime home = ~/Downloads)
        │  parser · FIFO matcher · time-weighted returns · tags.db (SQLite)
        │
        ├─→ swing_flag.py (position health: WATCH/ROT/HOLD, −8% cliff, 22d rot)
        ├─→ stock-tracker/journal_dashboard/build_journal_data.py (1,326 lines)
        │       imports trade_journal.py headlessly (GUI stubs), encrypts
        │       journal → journal_data.js → journal_dashboard.html (2,296 lines)
        ├─→ trading-suite/journal/fidelity.py (596 lines, VERBATIM AST copy)
        └─→ trading-data/ (auto-committed backups: CSVs, tags.db, engine JSON)
```

---

## Part 1 — Code audit findings

### P0 — Correctness bugs in the P&L engine (all three verified against real data)

**1. Commissions and fees are double-counted.**
`trade_journal.py:334` derives `amount_per_unit` from Fidelity's `Amount` column, which is **already net of commission and fees** — verified: the NOW Jan-28 $200 call buy shows `Amount −843.67` = 843.00 premium + 0.65 commission + 0.02 fees; the ORCL Jan-28 $300 call sell shows `Amount 1167.30` = 1168.00 − 0.65 − 0.05. Then `fifo_match` (`trade_journal.py:571–580`) subtracts `buy_comm + buy_fees + sell_comm + sell_fees` **a second time**. Every option leg is understated ≈ $1.35 per contract per round trip; marginal winners can be recorded as losers, distorting win rate. Inherited verbatim by `trading-suite/journal/fidelity.py` and by the dashboard adapter.
*Fix:* compute P&L from `Amount`-derived values only (costs are already inside), or from `Price × qty × multiplier` with costs subtracted exactly once.

**2. FIFO matches lots across accounts.**
`fifo_match` groups only `by_symbol` (`trade_journal.py:529–537`); the parser drops the `Account` column entirely. The CSVs span **Joint WROS, ROTH IRA, and SEP-IRA**, including same-ticker holdings and inter-account TSLA transfers. Lots bought in one account are matched against sells in another → wrong pairing, wrong P&L, wrong hold-days, and strategy tags keyed to legs that don't correspond to real trades.
*Fix:* key the matcher on `(account, symbol)`; parse and retain `Account`; treat `TRANSFERRED` rows as lot moves between books.

**3. Expired and assigned options never close their lots.**
The parser only recognizes actions containing BOUGHT/BUY/SOLD/SELL (`trade_journal.py:301–304`). The real data contains `EXPIRED PUT …` rows and 24 `ASSIGNED` rows — all silently skipped. Consequences: a long option expiring worthless is an **invisible 100% loss** (P&L and win rate overstated); a short put that expires keeps its collected premium stuck in the "orphan sells" bucket, never realized. The engine has no short-position model at all (sell-to-open simply becomes an orphan).
*Fix:* map `EXPIRED` to a $0 closing transaction, model assignment, and support sell-to-open/buy-to-close legs.

### P1 — Structural risks

**4. One finance core, three divergent copies.** The parser/FIFO/returns math exists as (a) `trade_journal.py` (canonical), (b) `trading-suite/journal/fidelity.py` — a self-described verbatim AST lift, (c) re-derived formulas in `build_journal_data.py` annotated by *line numbers* into the GUI file ("formulas from `_refresh_summary` L1543"). Plus: the `swing_flag.classify` rules are embedded as a "MUST keep in sync" fallback copy in the adapter, `trading-src/inbox/build_journal_data.py` is a stale 811-line snapshot of the 1,326-line adapter, and `module.py` exists byte-identical in three places. Every P0 fix above currently has to be hand-mirrored ≥3 times.

**5. Zero tests on the P&L core.** The only tests in the ecosystem are `swing_flag.py`'s classify selftest and the adapter's mapping/crypto selftest (both good — the adapter's passes in this environment). The parser, FIFO matcher, and return math — where all three P0 bugs live — have none. No CI runs any of it.

**6. Known-broken positions parser, deliberately deferred.** `_parse_positions` detects Fidelity's quoted-header/trailing-comma dialect and refuses to parse ("a dialect fix is planned"). Failing loud is right; staying broken isn't.

**7. `~/Downloads` is the production runtime.** The canonical journal app runs from `~/Downloads` (docs: "yes, really — being fixed"); an hourly copy-sync mirrors files between `~`, `~/Desktop/swing_project`, `~/Downloads` and the repos, and trading-src's own CLAUDE.md documents a 2026-07-05 incident where the sync resurrected quarantined files. The adapter's import path even prefers `~/Downloads` over the repo mirror.

### P2 — Code health

**8. Ingestion re-parses browser-download duplicates.** The journal reads whatever `Accounts_History (N).csv` was last loaded; `trading-data` holds 32 overlapping exports (3,731 rows, only 290 unique). No deduplicating store; the config pointer references a `~/Downloads` path.

**9. `stock-tracker/app.py` legacy issues.** Dead-and-buggy `calc_daily_pivots` left beside its fixed replacement (with a `# wait` comment); bare `except:` throughout; unsynchronized cross-thread `cache` dict and a racy `leap_loading` flag; 245 lines of HTML in a Python string; frontend hardcodes 16 tickers. The suite port drops some of this but keeps the swallow-everything exception pattern, and both repos ship **unpinned dependencies** (`yfinance` unpinned is a known breakage source).

**10. Hand-rolled crypto.** The dashboard implements a custom HMAC-SHA256-CTR cipher in Python and ~250 lines of pure-JS SHA-256/HMAC/PBKDF2 (no WebCrypto), at 60k PBKDF2 iterations (OWASP guidance: 600k). The construction is competent — encrypt-then-MAC, constant-time compare, cross-language test vector — but it's nonstandard, slow in the browser, and more surface than needed. WebCrypto + AES-GCM would be less code and stronger; raising iterations is the minimum stopgap.

**11. PII in trading-data.** Real brokerage account numbers and full trade history sit in plaintext in the (private) repo. Acceptable while private; consider masking account numbers at ingest, and never make that repo public.

**12. Minor:** emoji embedded in data-layer signal strings; `entry_bar` is a timestamp in one sink and an integer index in another; three different top-level JSON shapes across the data files; docs reference a `CONTRACT.md` that exists in no repo; ~330 KB of design mockups could move out of the deploy repo.

---

## Part 2 — Coding improvement plan (phased)

| Phase | What | Why first | Size |
|---|---|---|---|
| **0. Correctness** | Fix findings 1–3 + the positions-dialect gate. Add regression tests using the deduped 290-row real corpus as a fixture (assert known trades produce known P&L). | Every number downstream is wrong until this lands. | 1–2 sessions |
| **1. Consolidation** | Extract `journal_core` (parser, FIFO, returns, classify) as one package in trading-src; GUI, trading-suite, and the dashboard adapter import it. Delete `fidelity.py` copy, embedded classify fallbacks, `inbox/`/`_attic/` dupes. Retire `~/Downloads` as runtime home (repo checkout becomes the home; kill the copy-sync for code). | Makes every future fix single-site. The AST-lift and line-number coupling break silently otherwise. | 2–3 sessions |
| **2. Data layer** | One SQLite journal DB: dedup-ingest all Fidelity CSVs by row hash, keep `Account`, fold in tags.db. Adapter and suite read the DB, not "latest download (N).csv". | Ends duplicate-parsing fragility; enables per-account and tax reporting. | ~2 sessions |
| **3. Tests & CI** | pytest + GitHub Actions on trading-src and trading-suite; pin dependencies; ruff; replace print-WARN with `logging`; surface data staleness in the UI instead of silent empty tables. | Locks in phases 0–2. | 1–2 sessions |
| **4. Platform** | Finish the trading-suite migration (journal tab imports `journal_core`), then retire `stock-tracker/app.py`. Swap the homegrown crypto for WebCrypto/AES-GCM (or 600k iterations as stopgap). UI overhaul from the mockups **last**. | The suite is the declared successor; migrating a *tested* core is cheap, migrating the current one copies the bugs. | 2–4 sessions |

---

## Part 3 — Investment-perspective review

### What the journal does well

- **Net-of-cost realized P&L on FIFO lots** (once finding 1 is fixed) with orphan-sell preservation — most retail journals silently drop unmatched trades.
- **Time-weighted deployed-capital return** (`compute_time_weighted_return`) with a geometric monthly equivalent and turnover — this is the *right* question ("what am I earning on capital actually at work?") and it correctly exposes idle-cash drag against the 3%/month sleeve target.
- **Position-health rules grounded in the owner's own 82-trade backtest** (−8% worst-since-entry cliff → WATCH; 22-day time-stop → ROT; SUSPECT guard against bad data).
- **Prospective discipline at entry**: ATR-based stop (−2×ATR), targets (+2/+3×ATR), a 0.5% max-chase rule, and a 21-day hard stop on every ACT NOW card.

### What's missing, in order of importance

1. **No expectancy, no realized R-multiples.** Stops and `risk_usd` are already recorded at entry in the engine's `history.json` — but never joined to outcomes. Win rate alone cannot validate an edge. The live engine's exit mix (29 of 32 closed trades exited "took profit", 1 stop) is the classic signature of cutting winners early while time-stops absorb the losses — expectancy and payoff ratio would show whether the backtest's 56.1% win rate / 1.3 profit factor is holding up live. This is the single highest-value analytics gap.
2. **First target is fixed at 1.0:1 reward:risk by construction** (T1 = +2×ATR against a −2×ATR stop; T2 = 1.5:1). A 1:1 target needs >50% win rate *after costs and slippage* just to break even; the backtest's 56% is a thin margin. Track realized R to validate, and consider asymmetric targets or trailing rules.
3. **No drawdown, exposure, or risk-of-ruin view.** The cumulative P&L series is already computed — max drawdown and underwater duration are one fold away. Nothing tracks % of account risked per trade or concurrent exposure (state.json shows 13 simultaneous open positions).
4. **No benchmark.** 3%/month ≈ 43%/year. Without an SPY-relative comparison, bull-market beta is indistinguishable from edge.
5. **Options performance is structurally unmeasurable** until finding 3 is fixed — the LEAP sleeve (17 tagged trades) and wheel byproducts (assignments, expiries, short puts) are precisely the legs the engine drops.
6. **Tax blindness.** Taxable Joint + ROTH + SEP results are pooled. Wash-sale exposure is invisible — a loss realized in the taxable account and repurchased within 30 days in *any* account (including the IRAs — the classic permanent-disallowance trap) goes undetected, and the same tickers are demonstrably traded across accounts. No short-term/long-term split either. Per-account books (finding 2's fix) are a prerequisite.
7. **No account-equity snapshots** anywhere → true portfolio return and real drawdown can't be reconstructed. One daily balance number captured by `backup-data.sh` would unlock both.
8. **Dividends and interest are excluded** (those rows are skipped), understating total return on dividend payers held in the journal.
9. **No entry thesis/notes field.** Exit reasons exist; entry rationale doesn't. The behavioral half of journaling — grading execution against plan, tagging setups, reviewing mistakes — has no home.
10. **Signal→fill reconciliation absent.** Engine trade IDs and broker rows are never linked, so slippage versus scan price (and adherence to the max-chase rule) is unmeasured.

### Concrete additions (after Phase 0/1)

- A `metrics` module over closed legs: realized R per trade, expectancy, payoff ratio, profit factor, max drawdown + longest underwater streak, monthly SPY-relative delta.
- Per-account × per-strategy × per-setup breakdown (strategy tags exist; setup tags can come from the engine's `conditions_met`).
- A wash-sale flag: any taxable loss followed ≤30 days by a same-symbol buy in any account.
- Daily equity capture in `backup-data.sh`.
- Keep the deployed-capital monthly metric as the north star — and validate the 3%/month target against measured expectancy after ~90 days of clean data.

---

## Final recommendation

Sequence the overhaul: **correctness → consolidation → edge metrics → migration → UI.** Resist starting with the dashboard redesign (the mockups make it tempting) — the numbers every dashboard displays are currently wrong in three distinct ways, and each improvement layered onto the triplicated core multiplies the eventual fix cost. The highest-leverage single step is Phases 0+1 together: one tested `journal_core` with the three P&L bugs fixed — roughly a week of focused sessions — after which the investment-metrics layer is mostly straightforward aggregation over data the system already collects.
