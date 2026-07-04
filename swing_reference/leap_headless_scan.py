#!/usr/bin/env python3
"""
leap_headless_scan.py — Daily headless LEAP scan (155-ticker FULL_UNIVERSE).
No GUI. Logs actionable signals to ~/.michael_leap_recommendations.json
and appends a human-readable summary to ~/leap_scan_log.txt.
"""

import os
import sys
import signal
import time
from datetime import datetime
import zoneinfo
import yfinance as yf

# Force Agg backend before any leap_strategy import (which also sets it, but
# setting it here ensures correctness if import order ever changes).
os.environ.setdefault("MPLBACKEND", "Agg")

sys.path.insert(0, os.path.expanduser("~"))
from leap_strategy import (
    FULL_UNIVERSE,
    LeapTracker,
    _REC_PATH,
    calc_rsi,
    calc_rev_confirmed,
    calc_monthly_pivots,
    calc_ma_rsi_signal,
    get_ath_and_52w,
    get_best_leap,
)
from leap_scoring import (
    score_leap,
    STRONG_THRESHOLD,
    MONITOR_THRESHOLD,
    MAX_SCORE,
)

_LOG_PATH = os.path.expanduser("~/leap_scan_log.txt")
_ET = zoneinfo.ZoneInfo("America/New_York")


def _timeout_handler(signum, frame):
    raise TimeoutError("ticker scan timed out")


# ── Market freshness check ───────────────────────────────────────────────────

def _is_fresh(hist):
    """Return True iff the last bar in hist is from today in ET."""
    if hist.empty:
        return False
    last = hist.index[-1]
    if getattr(last, "tzinfo", None) is not None:
        last_date = last.astimezone(_ET).date()
    else:
        last_date = last.date()
    return last_date == datetime.now(_ET).date()


# ── Premium staleness ─────────────────────────────────────────────────────────

def _build_prior_premium(recs):
    """Map (symbol, strike, exp) -> (premium, stock_price) using the most
    recent prior observation of each LEAP contract, drawn from already-loaded
    JSON records (no extra network/disk I/O)."""
    prior = {}
    dates = {}
    for r in recs:
        leap = r.get("leap")
        if not leap:
            continue
        key = (r.get("symbol"), leap.get("strike"), leap.get("exp"))
        date = r.get("date", "")
        if key not in dates or date > dates[key]:
            dates[key] = date
            prior[key] = (leap.get("premium"), r.get("price"))
    return prior


# ── Per-ticker scan ──────────────────────────────────────────────────────────

def _scan_ticker(ticker, prior_premium):
    """
    Returns (row_dict, skip_reason).
    row_dict is None when the ticker is skipped.
    skip_reason is None when the ticker succeeds.
    """
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(30)
    try:
        stock = yf.Ticker(ticker)
        hist1y = stock.history(period="1y")
        if hist1y.empty:
            return None, "no data"
        closes = hist1y["Close"].dropna()
        if closes.empty:
            return None, "no closes"
        if not _is_fresh(hist1y):
            return None, "stale (market closed)"

        price = round(float(closes.iloc[-1]), 2)
        ath, w52h = get_ath_and_52w(ticker)
        ws2, ws3 = calc_monthly_pivots(ticker)
        rsi = calc_rsi(closes)
        try:
            rev_confirmed = calc_rev_confirmed(closes)
        except Exception:
            rev_confirmed = False
        ma_sig, best_ma, pct_vs_ma = calc_ma_rsi_signal(closes, rsi)
        leap, furthest = (
            get_best_leap(ticker, w52h or ath) if (w52h or ath) else (None, None)
        )
        prem_pct = leverage = None
        if leap and price:
            prem_pct = round((leap["premium"] / price) * 100, 1)
            leverage = round(price / leap["premium"], 1)

        premium_stale = False
        if leap and leap.get("premium") is not None and price:
            key = (ticker, leap["strike"], leap["exp"])
            prev = prior_premium.get(key)
            if prev is not None:
                prev_prem, prev_price = prev
                if prev_prem is not None and prev_price:
                    stock_move = abs(price - prev_price) / prev_price * 100
                    if leap["premium"] == prev_prem and stock_move > 2.0:
                        premium_stale = True
        vs_s2 = round(((price - ws2) / ws2) * 100, 1) if ws2 else None
        vs_s3 = round(((price - ws3) / ws3) * 100, 1) if ws3 else None
        closest = None
        if vs_s2 is not None and vs_s3 is not None:
            closest = "s2" if abs(vs_s2) < abs(vs_s3) else "s3"
        elif vs_s2 is not None:
            closest = "s2"
        elif vs_s3 is not None:
            closest = "s3"
        pct_from_ath = round(((ath - price) / ath) * 100, 1) if ath else None
        sc, bd = score_leap(
            price, ath, prem_pct, leverage,
            vs_s2, vs_s3, leap["dte"] if leap else None, rsi,
            ma_signal=ma_sig,
        )
        if vs_s3 is not None and abs(vs_s3) <= 5:
            sig = "S3 ALERT"
        elif vs_s2 is not None and abs(vs_s2) <= 5:
            sig = "S2 ALERT"
        elif sc >= STRONG_THRESHOLD:
            sig = "STRONG SETUP"
        elif sc >= MONITOR_THRESHOLD:
            sig = "MONITOR"
        else:
            sig = "—"

        return {
            "ticker": ticker, "price": price, "w52h": w52h, "ath": ath,
            "pct_from_ath": pct_from_ath, "rsi": rsi,
            "ma_rsi_signal": ma_sig, "best_ma_len": best_ma, "pct_vs_ma": pct_vs_ma,
            "prem_pct": prem_pct, "leverage": leverage,
            "ws2": ws2, "ws3": ws3, "vs_s2": vs_s2, "vs_s3": vs_s3,
            "closest": closest, "score": sc, "breakdown": bd,
            "signal": sig, "leap": leap, "furthest_leap": furthest,
            "rev_confirmed": rev_confirmed, "premium_stale": premium_stale,
        }, None

    except TimeoutError:
        return None, "timeout"
    except Exception as exc:
        return None, f"error: {exc}"
    finally:
        signal.alarm(0)


# ── Summary writer ───────────────────────────────────────────────────────────

def _write_summary(results, skipped, logged_count, total_recs, ts, stale_count):
    strong  = [r for r in results if "STRONG" in r["signal"]]
    s2      = [r for r in results if "S2"     in r["signal"]]
    s3      = [r for r in results if "S3"     in r["signal"]]
    monitor = [r for r in results if r["signal"] == "MONITOR"]

    def _rev(r):
        return "✅" if r.get("rev_confirmed") else "❌"

    lines = [
        f"\n=== {ts} ===",
        f"Scanned: {len(FULL_UNIVERSE)} tickers | New records logged: {logged_count} | Total JSON records: {total_recs} | Stale premiums flagged: {stale_count}",
        f'STRONG SETUP (score >= {STRONG_THRESHOLD} AND signal contains "STRONG"):',
    ]
    if strong:
        for r in strong:
            lines.append(f"  {r['ticker']} ({r['score']}/{MAX_SCORE}, Rev?{_rev(r)})")
    else:
        lines.append("  (none)")

    lines.append("S2 ALERT:")
    if s2:
        for r in s2:
            lines.append(f"  {r['ticker']} ({r['score']}/{MAX_SCORE})")
    else:
        lines.append("  (none)")

    lines.append("S3 ALERT:")
    if s3:
        for r in s3:
            lines.append(f"  {r['ticker']} ({r['score']}/{MAX_SCORE})")
    else:
        lines.append("  (none)")

    lines.append("MONITOR (score >= MONITOR_THRESHOLD, just for visibility):")
    if monitor:
        lines.append("  " + "  ".join(r["ticker"] for r in monitor))
    else:
        lines.append("  (none)")

    lines.append(f"Skipped (no data or market closed): {len(skipped)}")
    for ticker, reason in skipped:
        lines.append(f"  {ticker} ({reason})")

    lines.append("=" * 24)

    with open(_LOG_PATH, "a") as fh:
        fh.write("\n".join(lines) + "\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    tracker = LeapTracker()
    prior_premium = _build_prior_premium(tracker.recs)
    results = []
    skipped = []
    logged_count = 0
    stale_count = 0

    total = len(FULL_UNIVERSE)
    for i, ticker in enumerate(FULL_UNIVERSE, 1):
        print(f"[{i}/{total}] {ticker}", flush=True)
        row, skip_reason = _scan_ticker(ticker, prior_premium)
        if row is None:
            skipped.append((ticker, skip_reason))
            print(f"       skipped: {skip_reason}", flush=True)
        else:
            results.append(row)
            print(f"       score={row['score']} signal={row['signal']}", flush=True)
            if row["signal"] != "—":
                before = len(tracker.recs)
                tracker.log(
                    ticker, row["price"], row["score"],
                    row["signal"], row["breakdown"], row["leap"],
                    barchart_opinion=None,
                )
                if len(tracker.recs) > before:
                    logged_count += 1
                    # LeapTracker.log() builds its own entry dict and doesn't
                    # accept arbitrary fields, so attach premium_stale to the
                    # freshly-appended record directly and persist it here.
                    tracker.recs[-1]["premium_stale"] = row["premium_stale"]
                    tracker._save()
                    if row["premium_stale"]:
                        stale_count += 1
        time.sleep(0.4)

    results.sort(key=lambda r: -r["score"])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    _write_summary(results, skipped, logged_count, len(tracker.recs), ts, stale_count)


if __name__ == "__main__":
    main()
