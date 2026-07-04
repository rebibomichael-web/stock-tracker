"""
leap_scoring.py — Pure scoring logic for LEAP recommendations.

No imports required. No I/O, no network, no GUI.
Importable headlessly for unit testing.

Extracted from leap_strategy.py in Commit 0.5.
"""

# ── Scoring schema ─────────────────────────────────────────────────────────
# Single source of truth for pillar structure and policy thresholds.
# MAX_SCORE is derived — never hardcode it elsewhere.

PILLAR_MAX = {
    'ATH Drawdown':   3,
    'Prem Efficiency': 2,
    'Leverage':        2,
    'S2/S3 Level':     3,
    'RSI':             5,
}

MAX_SCORE = sum(PILLAR_MAX.values())  # currently 15 — derives automatically

STRONG_THRESHOLD  = 10   # sc >= STRONG_THRESHOLD → STRONG SETUP
MONITOR_THRESHOLD =  7   # sc >= MONITOR_THRESHOLD → MONITOR


def score_leap(price, ath, prem_pct, leverage, vs_s2, vs_s3, dte, rsi=None, **kwargs):
    bd = {}
    if ath and price:
        dd = ((ath - price) / ath) * 100
        bd['ATH Drawdown'] = 3 if dd >= 30 else (2 if dd >= 15 else 1)
    else:
        bd['ATH Drawdown'] = 0
    if prem_pct:
        bd['Prem Efficiency'] = 2 if prem_pct < 10 else (1 if prem_pct < 15 else 0)
    else:
        bd['Prem Efficiency'] = 0
    if leverage:
        bd['Leverage'] = 2 if leverage >= 8 else (1 if leverage >= 4 else 0)
    else:
        bd['Leverage'] = 0
    if vs_s3 is not None and abs(vs_s3) <= 5:
        bd['S2/S3 Level'] = 3
    elif vs_s2 is not None and abs(vs_s2) <= 5:
        bd['S2/S3 Level'] = 2
    elif vs_s2 is not None and abs(vs_s2) <= 15:
        bd['S2/S3 Level'] = 1
    else:
        bd['S2/S3 Level'] = 0
    ma_signal = kwargs.get('ma_signal', None)
    if ma_signal == 'Diverg':
        bd['RSI'] = 5
    elif ma_signal == 'Entry' and rsi is not None and rsi < 30:
        bd['RSI'] = 4
    elif ma_signal == 'Entry':
        bd['RSI'] = 3
    elif rsi is not None and 40 <= rsi <= 55:
        bd['RSI'] = 2
    elif rsi is not None and rsi > 55:
        bd['RSI'] = 1
    else:
        bd['RSI'] = 0
    return sum(bd.values()), bd
