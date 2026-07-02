# R1 — Technical Analysis Filter vs ATR as Signal Gate — Findings

**Date:** 2026-07-02
**Status:** `BLOCKED — verify-first precondition FAILED against this repository`
**Experiment run:** NO. The pre-registered method was not executed and was not re-derived.

---

## 1. Gate status (unchanged, not in dispute)

- Gate: "Phase 1 exit backtest complete" — SATISFIED 2026-05-05 (Item 4b, Mode 1 vs Mode 3,
  verdict DON'T SHIP, Mode 1 wins PF 1.272 vs 1.078, winner locked `exit_mode='single'`).
- The experiment is administratively clear to run. That part of R1 holds.

## 2. Verify-first result (the ⚠ step) — FAILED  `[Proven]`

The April spec requires verifying, read-only, that the tech-anal gate AND the ATR gate both
still exist in current `swing_trader.py` and are swappable. Against
`rebibomichael-web/stock-tracker` (the only repository in scope for this session):

| Check | Result |
|---|---|
| `swing_trader.py` in working tree | **Absent** |
| `swing_trader.py` anywhere in git history (all refs, all 10 commits) | **Never existed** — only `app.py`, `README.md`, `render.yaml`, `requirements.txt` have ever been committed |
| String `atr`/`ATR` in any historical blob | **Zero matches** (only hit is the branch *name* in ref metadata, not code) |
| Strings `tech_anal`, `exit_mode`, `swing_trader` in any blob | **Zero matches** |
| Planning docs (`MASTER_LOG`, `CHANGE_LIST_CONSOLIDATED`, TODO) | **Not in this repo** |

Verified twice independently: `git grep` across `git rev-list --all`, and a per-blob scan via
`git cat-file --batch-all-objects` (method validated against a known-present string).

**Conclusion:** neither an ATR gate nor a technical-analysis gate exists in this codebase,
under any name. There is nothing to swap. The one-variable gate-swap experiment has no
target here.

## 3. Interpretation  `[Proven]`

The spec's fear was code *drift*. Reality is stronger: this is the wrong codebase entirely.
`app.py` is the deployed Flask dashboard (pivot tracker + LEAP scanner) — display and
alerting only, no backtester, no signal pipeline with a swappable gate, no volatility
measure used in any decision (see `TECH_ANAL_USAGE_AUDIT_2026-07-02.md` §5).
`swing_trader.py` — with its ATR gate, exit modes, and the Item 4b machinery — lives
somewhere this session cannot see (most likely the local machine; it has never been pushed
to this GitHub repository).

**Update, later same day (2026-07-02):** target located. Per Michael's report, the full
trading project (including `swing_trader.py` and `leap_strategy.py`) was pushed to the
private repo **`trading-src`** (135 files, planning docs, and `MASTER_STATUS_BOARD.md`
included; hourly auto-sync armed). This session is scoped to `stock-tracker` and cannot
read `trading-src`; the steps below now have a concrete target.

## 4. What unblocks R1  `[Action]`

1. ~~Locate the repository/directory that actually contains current `swing_trader.py`.~~
   **DONE 2026-07-02** — it is the private repo `trading-src`. Start a fresh Claude Code
   session pointed at `trading-src` for the remaining steps.
2. Re-run the verify-first step there: confirm the tech-anal gate and ATR gate both exist
   and are swappable as the April method assumes.
3. Only then run the pre-registered experiment as written — one variable (gate swap), accept
   iff win rate +≥3% **AND** viable signal count ≥ threshold, output tagged
   Proven/Suspected/Rejected into this file.
4. If that code has also drifted (gate removed/renamed), record it here as a spec-drift
   finding before touching the method.

## 5. Adjacent items — status notes

- **Item 12 / Profile E** (ATR as volatility *scaler*, not gate replacement): also has no
  target in this repo, for the same reason. Different question, same relocation needed.
- Do not conflate with R1; recorded here only so the next reader doesn't re-check.

---
*Verification performed read-only. No experiment code was written; the pre-registered
method remains untouched and authoritative.*
