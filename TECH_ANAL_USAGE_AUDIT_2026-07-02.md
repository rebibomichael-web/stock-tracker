# Technical Analysis Usage Audit — Stock Tracker (Swing) + LEAP Scanner

**Date:** 2026-07-02 · **Scope:** `rebibomichael-web/stock-tracker` @ `97844e9` (`app.py`, 566 lines)
**Method:** full-file audit by parallel independent readers, every key claim then adversarially
verified against the code (5/5 claims CONFIRMED). Line numbers refer to `app.py`.

**Tags:** `[Proven]` = verified by direct code/git inspection, adversarially confirmed.
`[Suspected]` = logic-confirmed but runtime-dependent (not reproduced live). `[Rejected]` = claim
from prior research found false or unsupported.

---

## 1. Headline findings

1. **There is no technical-analysis *gate* anywhere in this app.** `[Proven]` Every TA input is
   either display-only or feeds the LEAP score/alert labels. Nothing blocks or allows a signal
   based on an indicator. The only true gates are contract-selection mechanics (DTE ≥ 540 at
   130-131, positive premium at 141, ATH-fetch-succeeded at 246).
2. **No volatility measure participates in any decision.** `[Proven]` No ATR, no historical vol,
   no IV rank. IV is fetched per contract (140) and shown only in the click-detail box (JS 517).
   All thresholds (±5% pivot band, score bands) are fixed percentages — identical for NVDA and BMNR.
3. **Your only "tech-anal composite" is the scraped Barchart opinion, and it is display-only.**
   `[Proven]` Fetched at 212, stored 220-221, rendered 451-452. Never parsed numerically, never
   enters a conditional, score, or filter.
4. **The swing-side (tracker tab) computes exactly one indicator family: classic daily floor
   pivots** (`calc_pivots_correct`, 38-55 — formulas correct), rendered as six columns with
   nearest-level arrows. Pure display. `[Proven]`
5. **The LEAP side's TA is: weekly S2/S3 floor pivots (alert trigger + score), ATH drawdown,
   premium efficiency, leverage, DTE** — a 15-point score plus a proximity alert that overrides it.

## 2. Swing / Tracker tab — TA inventory  `[Proven]`

| Input | Lines | Source | Role |
|---|---|---|---|
| Price / prev close / change % | 197-203 | yfinance 2d | display-only |
| Daily floor pivots S3…R3 | 38-55 → 204 | yfinance 5d, prior bar H/L/C | display-only |
| Nearest-level arrows (below/above) | 205-210 | computed | display-only |
| Barchart composite opinion (% Buy/Sell) | 72-105 → 212 | **scraped** | display-only |
| Barchart Strength / Direction | 84-92 | scraped | display-only |
| Barchart snapshot history (Y'day/Wk/Mo) | 93-102 | scraped | display-only |
| `calc_daily_pivots` | 24-36 | — | **dead code, and wrong** (see §4.1) |

Data flow: daemon thread at import (561) → every 30 min → per ticker: 2 yfinance fetches +
1 Barchart scrape → in-memory cache → `/api/tracker` → innerHTML table. Nothing feeds a signal.

## 3. LEAP Scanner tab — TA inventory  `[Proven]`

| Input | Lines | Source | Role |
|---|---|---|---|
| Price (1d close) | 240-243 | yfinance | feeds everything |
| ATH (max-history high) | 109-116 | yfinance `period="max"` | score (drawdown p1) + strike target; **never displayed** |
| 52-week high / % from 52W | 113, 253 | yfinance 1y | display-only (score uses ATH, UI shows 52W) |
| Weekly pivots S2/S3 | 57-68 | yfinance 1mo/1wk, prior week | **alert trigger** + score p5 + display |
| vs_s2 / vs_s3 (% distance) | 251-252 | computed | alert trigger (`abs(...) <= 5`) |
| Best LEAP (strike nearest ATH, DTE ≥ 540) | 120-149 | yfinance options chains | score inputs |
| Premium efficiency / Leverage | 248-250, 162-172 | computed | score p2, p3 (exact reciprocals) |
| DTE | 128-131, 174-178 | computed | selection **gate** + score p4 |
| IV | 140, JS 517 | yfinance | display-only (detail box) — the app's *only* volatility number |
| Score (0-15) → signal ladder | 151-189, 263-272 | computed | S3 ALERT > S2 ALERT > STRONG ≥12 > MONITOR ≥8 |

## 4. Defects and design findings

### 4.1 Bugs  `[Proven]` unless noted
- **`calc_daily_pivots` (24-36) is dead and wrong.** Never called anywhere; returns
  `[S1, S2, R1, S1, S2, S3]` instead of `[S3,S2,S1,R1,R2,R3]` (S1/S2 duplicated, R2/R3 absent);
  abandoned mid-edit — literal comment `# s3,s2,s1... wait` at line 33. `calc_pivots_correct`
  (38-55) is the working one. Existence proof that silent pivot-math bugs are a real class here.
- **S2/S3 ALERT uses `abs()` proximity (263-266, 180-187):** the alert fires when price is within
  5% on *either* side of the weekly support — a 5% breakdown below S3 and an approach from above
  are indistinguishable, and both earn the same score points. Alerts also fire regardless of the
  15-point score and even when no LEAP contract exists — while the precedence ladder *hides*
  STRONG SETUP on any high-score name that reaches support. The two signal families mask, not
  confirm, each other.
- **Barchart parse guard (85):** checks `pct` but not `sig` — if the signal span is missing, the
  whole scrape degrades to N/A for all six fields via the bare except.
- **Nearest-level arrow fallback (209-210):** when price is above all pivots (or below S3), the
  arrow marks a level on the wrong side.
- **DTE off-by-one (124, 128-131):** `(exp - now).days` truncates; an exactly-540-day contract is
  rejected, and 720-day contracts can miss the 3-point band.
- **Falsy-zero guards (251-252, JS 493-494):** `if ws2:` drops a legitimate 0.0 level; weekly
  `S3 = P − 2(H−L)` can go ≤ 0 on volatile low-priced names (LMND/BMNR/SOFI-class weeks), making
  `vs_s3` sign-flipped or explosive. `[Suspected — logic confirmed, needs a live wide-week case]`
- **NaN IV → invalid JSON (140, 299-301):** `if row['impliedVolatility']` is truthy for NaN, so
  NaN can reach `jsonify`, emit literal `NaN`, and make the browser's `r.json()` reject with no
  `.catch` — LEAP tab frozen until the 30-min interval. `[Suspected — logic confirmed]`

### 4.2 Fragilities  `[Proven]`
- **yfinance load:** tracker = 2 calls/ticker; LEAP = 5 fixed + 1 per qualifying expiry per
  ticker. ≈130-190 Yahoo requests + 16 Barchart scrapes every 30 min from one Render egress IP,
  no backoff, no caching — the `period="max"` ATH fetch (~768 full-history downloads/day) is pure
  waste for a value that almost never changes. Real throttle/ban risk; throttled cycles are
  indistinguishable from "no data" because…
- **Bare `except:` everywhere** (35, 54, 67, 104, 117, 148, 227, 280) swallows 429s/bans/parse
  drift silently into N/A; an all-fail LEAP cycle overwrites the cache with `[]` (283), discarding
  the last good data.
- **Stale-premium scoring (139):** after-hours `lastPrice` on illiquid LEAPs feeds 6 of 15 score
  points; no OI/volume/spread check exists at all.
- **Import-time daemon thread (561):** under multi-worker gunicorn each worker runs its own
  scrape loop and its own cache. `leap_loading` (233-235) is check-then-set with no lock and no
  try/finally.

### 4.3 Score design notes  `[Proven]`
- **Self-correlation:** prem_pct and leverage are exact reciprocals (p2, p3 double-count one
  quantity); with the strike pinned near ATH, deeper drawdown mechanically ⇒ more-OTM ⇒ "cheaper"
  ⇒ higher p2+p3. Up to 9/15 points reward the single condition "far below ATH", and with delta
  ignored, "leverage" is most overstated exactly on deep-OTM lottery tickets — which the sort
  then top-ranks.
- **Compressed range:** every populated component floors at 1 and p4 is effectively binary
  (2 or 3) because of the DTE≥540 selection gate — baseline ≈5/15, so MONITOR ≥8 and STRONG ≥12
  discriminate less than they appear to.
- **ATH is dividend/split-adjusted** (yfinance `auto_adjust=True` default), so for DE/ORCL/MU the
  strike target and drawdown deviate from the nominal ATH — and ATH is never shown for
  verification. `[Suspected — library-default dependent]`

## 5. Relationship to R1 (tech-anal vs ATR gate)

See `tech_anal_vs_atr_findings.md`: R1 is **BLOCKED** here — `swing_trader.py`, the ATR gate, and
the tech-anal gate have *never existed in this repository* (all-refs git forensics, adversarially
confirmed). This audit is the read-only verify-first artifact: it proves the deployed app has no
gate to swap and no ATR at all. R1 must be re-verified and run wherever `swing_trader.py`
actually lives.

## 6. Skills / MCP decision

Grounded in the audit above, not in the pasted research's claims. Nothing external was installed;
none of the third-party repos could be verified from this sandbox (external repo access is out of
scope for this session) — treat every name, count, and capability claim as unconfirmed until you
check the repo yourself.

### Adopt (build, don't install): 3-5 private skills  — **highest fit**
The generic catalogs solve problems this stack doesn't have; the gaps the audit found are
specific and small:
1. **Signal postmortem / outcome logger** — signals currently vanish with the in-memory cache;
   nothing validates that STRONG SETUP (≥12) predicts anything. Persist every S2/S3 ALERT +
   STRONG SETUP with timestamp/price, score against forward returns, emit
   Proven/Suspected/Rejected. (If tradermonty's `signal-postmortem` verifies as real and
   well-built, use it as a design reference only.)
2. **Math verifier** — independently recompute daily/weekly pivots and the LEAP score from raw
   OHLC and diff against app output. `calc_daily_pivots` is the existence proof for this bug class.
3. **Scraper-drift monitor** — alert when Barchart returns all-N/A across tickers (today that
   failure is silent by construction).
4. **Backtest auditor** + **FIFO journal auditor** — for the private `swing_trader.py` / SQLite
   projects, where they map 1:1 onto the pre-registration discipline.
(8-12 skills, as the research suggested, is over-scoped: more maintenance surface than the
566-line app it serves.)

### Conditional — verify, pin, vendor before use
- **staskh/trading_skills `technical-analysis`** — the one catalog item with a real hook: it could
  replace/cross-check the brittle Barchart scrape with locally computed indicators on data the app
  already pulls. Note it does **not** cover floor pivots (your core math). Do not run blind
  `npx` install: confirm the repo, read every file, pin a commit, vendor it.
- **Alpha Vantage official MCP** — **dev-time cross-validation only** (second opinion on prior-day
  H/L/C pivot inputs). The "production yfinance fallback" framing is `[Rejected]`: an MCP server
  serves your agent sessions, not the deployed gunicorn process; free tier (~25 req/day) cannot
  cover 16 tickers × 48 cycles; options chains aren't free-tier — so it cannot back the LEAP tab.

### Reject
| Candidate | Why |
|---|---|
| Finance Toolkit MCP | Zero fundamentals surface in this codebase; LEAP score is pure price/options mechanics. |
| tradermonty wholesale (esp. `trader-memory-core`) | YAML journal would create a second source of truth conflicting with your SQLite FIFO journal. |
| agiprolabs "62 skills" | Unverifiable, kitchen-sink scope (DeFi/tax), classic fabrication hallmarks; its backtesting would shadow your pre-registered backtester. |
| quant-analyst (sickn33) | Duplicates `swing_trader.py`; unaudited parallel math is what your discipline exists to prevent. Targets a different agent product besides. |
| Longbridge | No Longbridge account in evidence; broker-authenticated skills raise injection blast radius to live-account access. |
| Helium MCP, news-engine | Bare name-drops, unverifiable; news sentiment is non-reproducible input — philosophy mismatch. |
| 1inch DeFi | No on-chain surface anywhere; its presence flags the source list as listicle-padded. |

### Standing red flags for any install
- Third-party SKILL.md files and MCP responses become part of the agent's instruction stream —
  in a trading workflow that's a prompt-injection/supply-chain surface that could bias levels,
  scores, or decisions. Read, pin, vendor; prefer skills you wrote.
- `npx -y skills add …` executes unpinned third-party code at install time.
- Several citations in the prior research carry ChatGPT-search UTM tags and could not be
  confirmed; at least three candidates look padded or fabricated.

## 7. Recommended order of work
1. Locate real `swing_trader.py`; re-run R1 verify-first there (unblocks the 8-weeks-overdue experiment).
2. Build the signal postmortem logger (turns the deployed app into a data source for exactly the
   kind of question R1 asks — does a tech-anal composite gate add anything?).
3. Fix the `[Proven]` bugs in §4.1 and cache the ATH fetch (one-line risk reduction on the ban vector).
4. Then, and only then, evaluate the two "conditional" externals against verified repos.
