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

---

# Second verify-first record — 2026-07-02 (later session, branch `claude/intelligent-curie-j1hsfo`)

**Status:** `BLOCKED — verify-first precondition FAILED again. Experiment NOT run.`
**Method:** untouched. The pre-registered April method was not executed and was not re-derived.

## Verify-first result  `[Proven]`

Re-verified independently against the live remote state — not by trusting the record above:

| Check | Result |
|---|---|
| Session repo scope | `rebibomichael-web/stock-tracker` only — **not** `trading-src` |
| `swing_trader.py` in any ref (incl. new branch `claude/tech-anal-vs-atr-gate-4xlygg`) | **Absent** |
| Files ever committed, all refs | `app.py`, `README.md`, `render.yaml`, `requirements.txt` + the two findings/audit docs from the earlier session — nothing else |
| `swing_trader` / `ATR` / `tech_anal` / `exit_mode` / signal-gate strings in any blob | **Zero matches** outside this file and the audit doc themselves |
| Remote drift since first record | None — `main` unchanged at `97844e9`; only addition is `e967be0` (the first record itself) |

**Conclusion:** the §4 unblock path (a session pointed at `trading-src`) was not what this
session got — it is pointed at `stock-tracker` again. There is still no gate to swap here.
Neither accept criterion (win rate +≥3 pp; viable signal count ≥ threshold) was evaluated,
because no experiment ran.

## Bookkeeping

- Gate "Phase 1 exit backtest complete": still SATISFIED (Item 4b, closed 2026-05-05,
  Mode 1 PF 1.272 vs 1.078, `exit_mode='single'` locked). Not in dispute; not re-checked.
- Item 12 / Profile E (ATR as volatility scaler): still a separate question, still no target
  here. Not conflated with R1.
- This file was carried verbatim from branch `claude/tech-anal-vs-atr-gate-4xlygg` onto
  `claude/intelligent-curie-j1hsfo` and appended (session branch rules forbid pushing to the
  earlier branch). The `4xlygg` copy remains the record as of the first entry;
  **this copy is now the most complete one.**

## What unblocks R1 (unchanged, restated)

Start the next session **pointed at the private repo `trading-src`** (where
`swing_trader.py` lives, per the update in §3 above). Run the verify-first step there;
only if both gates exist and are swappable, run the April method as written — one variable,
accept iff win rate +≥3 pp **AND** viable signal count ≥ threshold — and append the result
here tagged Proven/Suspected/Rejected.

*Verification performed read-only. No experiment code was written.*
