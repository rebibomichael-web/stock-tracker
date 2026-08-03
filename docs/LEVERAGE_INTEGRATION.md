# Leverage Monitor — integration guide

`leverage_monitor.py` (repo root) implements the three validated leverage
inputs from `MARGIN_SHORT_BACKTEST_2026-08-02.md` / `STEELMAN_2026-08-02.md`.

## Already wired (this repo)

`app.py` (Render) shows a leverage bar on both tabs and a SIZING line in the
LEAP detail box. New endpoint: `/api/leverage`. The LEAP **score is
unchanged** — the dimmer is explicit sizing guidance (×0.75 while margin YoY
>= 40%; ×0.50 on a fresh deceleration confirmed by CAUTION/DANGER regime).

## To wire on the Dell (trading-src/swing_trader.py)

This repo is the canonical home of the module (trading-src is a mirror —
GitHub edits get overwritten). Copy `leverage_monitor.py` next to
swing_trader.py, or import from a clone of this repo. Then, in the scan
scoring path, AFTER the regime multiplier:

```python
from leverage_monitor import swing_adjustment

adj = swing_adjustment()          # one call per scan, not per stock
# adj = {'regime': 'BULL', 'frenzy_z': 1.71, 'veto': False,
#        'score_multiplier': 1.0}
final_score = min(final_score * adj['score_multiplier'], 100)
```

The module handles the validated asymmetry internally: the ×0.75 penalty
applies ONLY when frenzy (z >= 2) fires while the regime filter still says
BULL/NORMAL (backtest: bounce WR7 33.7% vs 56.5% in that cell). When the
regime is already CAUTION/DANGER, frenzy days are capitulation and bounces
WORK (+2.86% avg7) — no penalty is applied, on purpose. Do not "improve"
this by penalizing all frenzy days.

Show `frenzy_z` and `regime` in the scan header next to the existing
breadth-penalty line.

## Nightly logging (Dell cron)

```
55 20 * * 1-5  cd ~/stock-tracker && python3 leverage_monitor.py --nightly
```

Appends one JSON line per weekday to `~/.michael_leverage_log.jsonl`:
regime, margin state, frenzy state, and per-name L-ETF attention ratio,
heavy signed-volume flag, and FINRA days-to-cover. This is profiler feed —
after a few months, join it to signal outcomes to decide whether
days-to-cover / attention earn scored-condition status.

## Monthly margin watch

FINRA posts month M data ~3rd week of M+1 (July print ≈ Aug 20). The things
to check on each print, in order of importance:
1. deceleration flag (YoY back under 40% after 2+ extreme months) — the
   historical top signal (fwd 12m −19.6%, 0% positive, n=4) — but act only
   with regime confirmation;
2. streak age (months 4–6 of an extreme run were 0% positive on fwd 12m);
3. the YoY level itself (dimmer threshold).
`python3 leverage_monitor.py` (no args) prints all three.

## Selftest

`python3 leverage_monitor.py --selftest` — 18 pure-function checks, no
network. Mirrors the `build_journal_data.py --selftest` convention.
