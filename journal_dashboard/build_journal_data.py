#!/usr/bin/env python3
"""
build_journal_data.py — LIVE-DATA ADAPTER for the trader's journal dashboard.

Reads the real generation artifacts:
  • swing_headless_results.json   (written by swing_headless_scan.py)
  • open_positions_export.csv     (the journal's "Export Open" snapshot)
  • ~/.michael_leap_recommendations.json (written by leap_headless_scan.py)
plus live quotes (yfinance, lazily imported — stdlib-only at import time),
and writes journal_data.js:

    window.SWING_JOURNALS = { "<YYYY-MM-DD>": {...}, ... };

per the Journal Dashboard Live-Data Integration Contract (CONTRACT.md).

Design rules honored here:
  • stdlib-only at import time; yfinance imported INSIDE fetch functions only.
  • EVERY live fetch failure degrades to null fields. This script NEVER exits
    nonzero because the network failed — it prints a WARN line and continues.
  • --offline: zero network; quotes come from a --quotes fixture JSON.
  • --selftest: pure-function assertions, zero file/network access.

Run after a scan:
    python3 build_journal_data.py
"""

import argparse
import base64
import csv
import datetime as dt
import hashlib
import hmac
import json
import os
import sqlite3
import struct
import sys
import types

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG — all paths per CONTRACT.md §mappings (override with CLI flags)
# ═══════════════════════════════════════════════════════════════════════════
SWING_JSON_PATH = os.path.expanduser("~/Desktop/swing_headless_results.json")
CSV_PATH        = os.path.expanduser("~/Desktop/swing_project/open_positions_export.csv")
LEAP_JSON_PATH  = os.path.expanduser("~/.michael_leap_recommendations.json")
OUT_PATH        = os.path.expanduser("~/Desktop/swing_project/journal_data.js")
HISTORY_PATH    = os.path.expanduser("~/Desktop/swing_project/journal_history.json")

# The deployed LEAP tracker's 16-name original watchlist (CONTRACT.md).
ORIG_WATCHLIST = [
    "CRWD", "ORCL", "SNOW", "SSYS", "LMND", "PLTR", "BMNR", "TSLA",
    "NVDA", "GRNY", "DE", "MU", "NVMI", "SOFI", "HOOD", "NOW",
]

CARD_MIN     = 75   # trade cards: actionable signal AND final_score > this
CARD_SIGNALS = ("ACT NOW", "ARB BUY", "BUY")  # tiers that get a card (never WATCH/SELL)
TABLE_MIN    = 75   # over75 table: final_score > this (all signals)
HISTORY_KEEP = 30   # most recent dates kept in journal_history.json

# ACT NOW card derivation constants (swing_trader.py:1507-1521, CFG values)
STOP_M    = 2.0
T1_M      = 2.0
T2_M      = 3.0
TIME_STOP = 21      # calendar days -> hardStop; hold text "7-21d"

# ── Trade Journal (locked) section — CONTRACT-JOURNAL.md ──
JOURNAL_KEY_PATH     = os.path.expanduser("~/.journal_dashboard_key")
JOURNAL_PBKDF2_ITERS = 60000
JOURNAL_TRADES_CAP   = 40   # recent-trades table rows per window
JOURNAL_DAILY_MAX    = 60   # <=60 unique sell days -> daily bars, else ISO-week

# Index strip tickers -> display names (order = display order)
INDEX_TICKERS = [
    ("^DJI",    "Dow Jones"),
    ("^IXIC",   "Nasdaq"),
    ("^GSPC",   "S&P 500"),
    ("^VIX",    "VIX"),
    ("BTC-USD", "Bitcoin"),
    ("ETH-USD", "Ethereum"),
    ("KAS-USD", "Kaspa"),
]

# ═══════════════════════════════════════════════════════════════════════════
#  swing_flag.classify import — script dir + cwd on sys.path, else fallback
# ═══════════════════════════════════════════════════════════════════════════
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_SCRIPT_DIR, os.getcwd()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import swing_flag as _swing_flag
    classify = _swing_flag.classify
    _FLAG_ORDER = getattr(_swing_flag, "_FLAG_ORDER",
                          {"STOP": -2, "SUSPECT": -1, "WATCH": 0, "ROT": 0, "EARN": 0,
                           "WATCH?": 1, "ROT~": 1, "HOLD": 2})
    _CLASSIFY_SOURCE = "swing_flag.py"
except ImportError:
    # ── FALLBACK ────────────────────────────────────────────────────────────
    # Embedded copy of swing_flag.classify + thresholds. This is a FALLBACK
    # used only when swing_flag.py is not importable (script dir / cwd).
    # It MUST track swing_flag.py — if you change thresholds or wording
    # there, mirror the change here.
    # ────────────────────────────────────────────────────────────────────────
    WATCH_UNDERWATER_PCT = -8.0
    WATCH_SOFT_PCT       = -5.0
    ROT_DAYS             = 22
    SUSPECT_GAIN_PCT     = 100.0
    HARD_STOP_PCT        = -15.0
    _FLAG_ORDER = {"STOP": -2, "SUSPECT": -1, "WATCH": 0, "ROT": 0, "EARN": 0,
                   "WATCH?": 1, "ROT~": 1, "HOLD": 2}

    def classify(current_gain_pct, underwater_pct, days_held):
        """Fallback copy of swing_flag.classify — keep in sync with swing_flag.py."""
        if (abs(current_gain_pct) >= SUSPECT_GAIN_PCT
                or abs(underwater_pct) >= SUSPECT_GAIN_PCT):
            return ["SUSPECT"], (
                f"{current_gain_pct:+.0f}% gain / {underwater_pct:+.0f}% worst is "
                f"implausibly large -- almost certainly bad price or stale entry. "
                f"Verify; do NOT act on it.")
        flags, reasons = [], []
        if current_gain_pct <= HARD_STOP_PCT:
            flags.append("STOP")
            reasons.append(f"down {current_gain_pct:.1f}% (floor {HARD_STOP_PCT:.0f}%) -- "
                           f"catastrophic stop: EXIT, post-mortem after")
        if underwater_pct <= WATCH_UNDERWATER_PCT:
            flags.append("WATCH")
            reasons.append(f"hit {underwater_pct:.1f}% at worst (past -8% cliff), "
                           f"now {current_gain_pct:+.1f}% -- scrutinize thesis")
        elif underwater_pct <= WATCH_SOFT_PCT:
            flags.append("WATCH?")
            reasons.append(f"worst {underwater_pct:.1f}% (-5 to -8 ring), "
                           f"now {current_gain_pct:+.1f}%")
        if days_held >= ROT_DAYS:
            if current_gain_pct <= 0:
                flags.append("ROT")
                reasons.append(f"{days_held}d held, flat/down {current_gain_pct:.1f}% "
                               f"-- deadweight (cut?)")
            else:
                flags.append("ROT~")
                reasons.append(f"{days_held}d held, up {current_gain_pct:.1f}% "
                               f"-- slow winner (bank?)")
        if not flags:
            return ["HOLD"], (f"now {current_gain_pct:+.1f}%, worst "
                              f"{underwater_pct:.1f}%, held {days_held}d "
                              f"-- within normal range")
        return flags, " | ".join(reasons)

    _CLASSIFY_SOURCE = "embedded fallback (swing_flag.py not importable)"


def warn(msg):
    print(f"WARN: {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
#  Pure mapping functions (covered by --selftest — no file/network access)
# ═══════════════════════════════════════════════════════════════════════════

_SIGNAL_MAP = {
    "ACT NOW": "ACT NOW", "ARB BUY": "ARB BUY", "BUY": "BUY",
    "SELL": "SELL", "WATCH": "WATCH", "SUPPRESSED": "SUPPRESSED",
}


def clean_signal(sig):
    """Strip emoji/star decoration from a swing signal.
    '🔥 ACT NOW ⭐' -> 'ACT NOW', '▲ BUY' -> 'BUY', etc. (CONTRACT.md)."""
    if not sig:
        return "WATCH"
    s = str(sig).replace("⭐", "").strip()
    # drop any leading non-ASCII decoration (🔥 ⚡ ▲ ▼ ◌ ⊘ ...)
    s = "".join(ch for ch in s if ord(ch) < 128).strip()
    return _SIGNAL_MAP.get(s, s if s else "WATCH")


def status_kind(flags):
    """Map a swing_flag flags list to statusKind (CONTRACT.md):
    WATCH/ROT -> bad; WATCH?/ROT~/SUSPECT -> warn; HOLD -> good."""
    fset = set(flags or [])
    if fset & {"WATCH", "ROT"}:
        return "bad"
    if fset & {"WATCH?", "ROT~", "SUSPECT"}:
        return "warn"
    return "good"


def fmt_rr(mult_num, risk):
    return f"{(mult_num / risk):.1f}:1" if risk else "—"


def act_now_row(r, journal_date, as_of):
    """Build one actNow card from a swing result record.
    Derivations per CONTRACT.md / swing_trader.py:1507-1521:
      entry=price; maxChase=price*1.005; stop=price-2*ATR; t1=price+2*ATR;
      t2=price+3*ATR; risk=price-stop; rr=(target-price)/risk."""
    price = float(r["price"])
    atr = float(r.get("atr", 0) or 0)
    stop = price - STOP_M * atr
    t1 = price + T1_M * atr
    t2 = price + T2_M * atr
    risk = price - stop
    sp = r.get("setup_profile") or {}
    hard_stop = journal_date + dt.timedelta(days=TIME_STOP)
    return {
        "t": r["symbol"],
        "signal": clean_signal(r.get("signal")),  # card banner shows real tier
        "score": r.get("final_score"),
        "price": round(price, 2),
        "rsi": round(float(r.get("rsi", 0)), 1),
        "asOf": as_of,
        "entry": round(price, 2),
        "maxChase": round(price * 1.005, 2),
        "stop": round(stop, 2),
        "stopPct": round((stop - price) / price * 100, 1),
        "stopPerShare": round(stop - price, 2),
        "t1": round(t1, 2),
        "t1Pct": round((t1 - price) / price * 100, 1),
        "rr1": fmt_rr(t1 - price, risk),
        "t2": round(t2, 2),
        "t2Pct": round((t2 - price) / price * 100, 1),
        "rr2": fmt_rr(t2 - price, risk),
        "hold": "7-21d",
        "hardStop": f"{hard_stop:%b} {hard_stop.day}, {hard_stop.year}",
        "theme": sp.get("type") or "",
        "note": sp.get("description") or "",
    }


def over75_row(r):
    bt = r.get("bt_adj")
    arb = (r.get("arb") or {}).get("z")
    return {
        "t": r["symbol"],
        "price": round(float(r["price"]), 2),
        "rsi": round(float(r.get("rsi", 0)), 1),
        "tech": r.get("raw_buy"),
        "bt": (f"{int(bt):+d}" if bt else "0") if bt is not None else "—",
        "final": r.get("final_score"),
        "swingPct": round(float(r["atr_swing"]), 1) if r.get("atr_swing") is not None else None,
        "arb": round(float(arb), 1) if arb is not None else None,
        "signal": clean_signal(r.get("signal")),
        "opinion": "—",  # barchart not available headless (CONTRACT.md)
    }


def split_leaps(day_recs, watchlist):
    """ORIG_WATCHLIST split per CONTRACT.md. day_recs = deduped (latest per
    symbol) records for the journal date, leap != null already filtered.
    Returns (original_top3, orig_top_score, exceeders_sorted_desc)."""
    wl = set(watchlist)
    orig = [r for r in day_recs if r["symbol"] in wl]
    others = [r for r in day_recs if r["symbol"] not in wl]
    orig_top = max((r.get("score", 0) for r in orig), default=0)
    original = sorted(orig, key=lambda r: -r.get("score", 0))[:3]
    exceeders = sorted((r for r in others if r.get("score", 0) > orig_top),
                       key=lambda r: -r.get("score", 0))
    return original, orig_top, exceeders


def rev_text(rec):
    if "rev_confirmed" not in rec:
        return "—"
    return "✅ confirmed" if rec["rev_confirmed"] else "❌ not confirmed"


def leap_row(rec, chg):
    leap = rec.get("leap") or {}
    return {
        "t": rec["symbol"],
        "price": rec.get("price"),
        "chg": chg,
        "premium": leap.get("premium"),
        "strike": leap.get("strike"),
        "exp": leap.get("exp"),
        "dte": leap.get("dte"),
        "score": rec.get("score"),
        "signal": rec.get("signal"),
        "rev": rev_text(rec),
        "stale": bool(rec.get("premium_stale", False)),
    }


def weighted_positions(csv_rows, journal_date):
    """Aggregate 'Swing Trader' CSV rows per ticker (CONTRACT.md):
    qty=sum, entry=cost-weighted avg, buy_date=earliest, days from earliest."""
    agg = {}
    for row in csv_rows:
        if (row.get("Strategy") or "").strip() != "Swing Trader":
            continue
        t = (row.get("Ticker") or "").strip().upper()
        try:
            qty = float(row["Quantity"])
            cost = float(row["Buy Cost"])
            bdate = dt.datetime.strptime(row["Buy Date"].strip(), "%Y-%m-%d").date()
        except (KeyError, ValueError) as e:
            warn(f"positions: bad CSV row for {t or '?'}: {e}")
            continue
        a = agg.setdefault(t, {"qty": 0.0, "cost": 0.0, "buy_date": bdate})
        a["qty"] += qty
        a["cost"] += cost
        if bdate < a["buy_date"]:
            a["buy_date"] = bdate
    out = []
    for t, a in agg.items():
        entry = a["cost"] / a["qty"] if a["qty"] else None
        out.append({
            "ticker": t,
            "qty": a["qty"],
            "entry": entry,
            "buy_date": a["buy_date"],
            "days": (journal_date - a["buy_date"]).days,
        })
    return out


def heatmap_groups(csv_rows):
    """ALL CSV tickers, dedup. Group 'active' if the ticker has ANY
    'Swing Trader' lot (even if it also has Excluded lots); otherwise
    'excluded' (CONTRACT.md)."""
    strategies = {}
    order = []
    for row in csv_rows:
        t = (row.get("Ticker") or "").strip().upper()
        if not t:
            continue
        if t not in strategies:
            strategies[t] = set()
            order.append(t)
        strategies[t].add((row.get("Strategy") or "").strip())
    groups = {}
    for t in order:
        groups[t] = "active" if "Swing Trader" in strategies[t] else "excluded"
    return groups, order


# ═══════════════════════════════════════════════════════════════════════════
#  Live fetch functions — yfinance imported LAZILY here, never at module top.
#  Every failure -> WARN + None; NEVER raises out.
# ═══════════════════════════════════════════════════════════════════════════

def fetch_quotes_live(tickers):
    """Return {ticker: {"price": float|None, "chg": float|None}}."""
    out = {t: {"price": None, "chg": None} for t in tickers}
    if not tickers:
        return out
    try:
        import yfinance as yf
    except ImportError as e:
        warn(f"yfinance not importable ({e}) — quotes degraded to null")
        return out
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="5d", interval="1d", auto_adjust=True)
            if h is None or len(h) == 0:
                warn(f"quote fetch empty for {t}")
                continue
            closes = h["Close"].dropna()
            if len(closes) == 0:
                continue
            price = float(closes.iloc[-1])
            chg = None
            if len(closes) >= 2 and float(closes.iloc[-2]) > 0:
                chg = (price / float(closes.iloc[-2]) - 1.0) * 100.0
            out[t] = {"price": round(price, 2),
                      "chg": round(chg, 2) if chg is not None else None}
        except Exception as e:
            warn(f"quote fetch failed for {t}: {e}")
    return out


def fetch_worst_low_live(ticker, start_date):
    """Min Low since start_date (swing_flag semantics). None on any failure."""
    try:
        import yfinance as yf
    except ImportError as e:
        warn(f"yfinance not importable ({e}) — worst_low null for {ticker}")
        return None
    try:
        end = (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        h = yf.Ticker(ticker).history(start=start_date.strftime("%Y-%m-%d"),
                                      end=end, interval="1d",
                                      auto_adjust=False, actions=False)
        if h is None or len(h) == 0 or "Low" not in h.columns:
            warn(f"worst_low fetch empty for {ticker}")
            return None
        lows = h["Low"].dropna()
        if len(lows) == 0:
            return None
        wl = float(lows.min())
        return wl if wl > 0 else None
    except Exception as e:
        warn(f"worst_low fetch failed for {ticker}: {e}")
        return None


def _fmt_index_value(name, price):
    if price is None:
        return "—"
    if name in ("Bitcoin", "Ethereum", "Kaspa"):
        return f"${price:,.4f}" if price < 1 else f"${price:,.2f}"
    return f"{price:,.2f}"


def fetch_indexes_live():
    """Index strip tiles. Any per-ticker failure -> that tile's chg/value null."""
    try:
        import yfinance as yf
    except ImportError as e:
        warn(f"yfinance not importable ({e}) — index strip empty")
        return []
    tiles = []
    for tkr, name in INDEX_TICKERS:
        price = chg = None
        try:
            h = yf.Ticker(tkr).history(period="5d", interval="1d", auto_adjust=True)
            if h is not None and len(h) > 0:
                closes = h["Close"].dropna()
                if len(closes) > 0:
                    price = float(closes.iloc[-1])
                    if len(closes) >= 2 and float(closes.iloc[-2]) > 0:
                        chg = round((price / float(closes.iloc[-2]) - 1.0) * 100.0, 2)
        except Exception as e:
            warn(f"index fetch failed for {tkr}: {e}")
        if price is None:
            warn(f"index tile {name}: no price — skipped")
            continue
        tiles.append({"name": name, "value": _fmt_index_value(name, price),
                      "chg": chg})
    return tiles


# ═══════════════════════════════════════════════════════════════════════════
#  Loaders (all degrade to empty on missing/broken files, with a WARN)
# ═══════════════════════════════════════════════════════════════════════════

def load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        warn(f"{what} not found at {path} — section degraded")
    except Exception as e:
        warn(f"{what} unreadable at {path}: {e} — section degraded")
    return None


def load_csv(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        warn(f"positions CSV not found at {path} — heatmap/positions empty")
    except Exception as e:
        warn(f"positions CSV unreadable at {path}: {e} — heatmap/positions empty")
    return []


# ═══════════════════════════════════════════════════════════════════════════
#  Section builders
# ═══════════════════════════════════════════════════════════════════════════

def build_market(swing, indexes):
    if swing:
        vix = swing.get("vix")
        vix_s = f"{float(vix):.1f}" if isinstance(vix, (int, float)) else "—"
        mult = swing.get("regime_mult")
        mult_s = f"{float(mult):.2f}" if isinstance(mult, (int, float)) else "—"
        breadth = swing.get("breadth_mult")
        breadth_s = f"{float(breadth):.2f}" if isinstance(breadth, (int, float)) else "—"
        context = (f"VIX {vix_s} — regime {swing.get('regime_state', '—')}"
                   f" · mult {mult_s} · breadth {breadth_s}")
    else:
        context = "No scan data"
    return {"context": context, "indexes": indexes or []}


def build_swing_section(swing, journal_date, as_of):
    act_now, over75 = [], []
    if not swing:
        return act_now, over75
    for r in swing.get("results", []):
        try:
            final = r.get("final_score")
            if final is None:
                continue
            sig = clean_signal(r.get("signal"))
            # cards: every ACTIONABLE tier over CARD_MIN (ACT NOW / ARB BUY /
            # BUY — WATCH and SELL never get trade cards, table only).
            if sig in CARD_SIGNALS and final > CARD_MIN:
                act_now.append(act_now_row(r, journal_date, as_of))
            if final > TABLE_MIN:
                over75.append(over75_row(r))
        except Exception as e:
            warn(f"swing row {r.get('symbol', '?')} skipped: {e}")
    act_now.sort(key=lambda x: -(x["score"] or 0))
    over75.sort(key=lambda x: -(x["final"] or 0))
    return act_now, over75


def build_positions(csv_rows, journal_date, quotes, offline):
    positions = []
    for p in weighted_positions(csv_rows, journal_date):
        t = p["ticker"]
        q = quotes.get(t) or {}
        cur = q.get("price")
        worst_low = q.get("worst_low")
        if worst_low is None and not offline:
            worst_low = fetch_worst_low_live(t, p["buy_date"])
        entry = p["entry"]
        gain_pct = under_pct = None
        if cur is not None and entry:
            gain_pct = (cur - entry) / entry * 100.0
        if worst_low is not None and entry:
            under_pct = (worst_low - entry) / entry * 100.0
        days = p["days"]

        if gain_pct is not None:
            flags, reason = classify(gain_pct,
                                     under_pct if under_pct is not None else gain_pct,
                                     days)
        else:
            flags, reason = [], "price unavailable — not classified"

        gpd = (gain_pct / days) if (gain_pct is not None and days > 0) else None
        mom_per_bar = f"{gpd:+.2f}%/d" if gpd is not None else "—"
        value = cur * p["qty"] if cur is not None else None
        unreal = (cur - entry) * p["qty"] if (cur is not None and entry) else None

        if under_pct is not None:
            wv = f"{under_pct:+.1f}%"
            if under_pct <= -8:
                wv += " (past -8% cliff)"
            elif under_pct <= -5:
                wv += " (-5..-8 ring)"
            worst_check = {"k": "Worst since entry", "v": wv, "ok": under_pct > -5}
        else:
            worst_check = {"k": "Worst since entry", "v": "—", "ok": None}
        if value is not None and unreal is not None:
            vv = (f"${value:,.0f} · unreal "
                  f"{'+' if unreal >= 0 else '-'}${abs(unreal):,.0f}")
            value_check = {"k": "Value", "v": vv, "ok": unreal >= 0}
        else:
            value_check = {"k": "Value", "v": "—", "ok": None}
        checks = [
            worst_check,
            {"k": "Gain/day", "v": mom_per_bar,
             "ok": (gpd > 0) if gpd is not None else None},
            value_check,
            {"k": "Why", "v": reason, "ok": None},
        ]
        positions.append({
            "t": t,
            "status": "+".join(flags) if flags else "—",
            "statusKind": status_kind(flags) if flags else "warn",
            "entry": round(entry, 2) if entry is not None else None,
            "cur": round(cur, 2) if cur is not None else None,
            "pct": round(gain_pct, 1) if gain_pct is not None else None,
            "days": days,
            "momentum": None,   # no momentum meter source headless -> hide meter
            "momPerBar": mom_per_bar,
            "checks": checks,
            "_sort": (min((_FLAG_ORDER.get(f, 2) for f in flags), default=2),
                      gain_pct if gain_pct is not None else 0.0),
        })
    positions.sort(key=lambda x: x["_sort"])
    for pos in positions:
        del pos["_sort"]
    return positions


def build_heatmap(csv_rows, quotes, leap_scores, leap_max):
    groups, order = heatmap_groups(csv_rows)
    tiles = []
    for t in order:
        q = quotes.get(t) or {}
        g = groups[t]
        tiles.append({
            "t": t,
            "price": q.get("price"),
            "chg": q.get("chg"),
            "leap": leap_scores.get(t),
            "leapMax": leap_max,
            "group": g,
            "reason": "Excluded strategy (journal)" if g == "excluded" else None,
        })
    return tiles


def build_leaps(leap_recs, date_key, quotes):
    """LEAP section for the journal date. Returns (leaps_dict, day_scores)."""
    # leap_scoring.MAX_SCORE if importable (same dir/cwd already on sys.path)
    try:
        from leap_scoring import MAX_SCORE as leap_max
    except ImportError:
        leap_max = 15
    day = [r for r in (leap_recs or [])
           if str(r.get("date", "")).startswith(date_key)]
    # dedup per symbol -> latest record of the day
    latest = {}
    for r in day:
        s = r.get("symbol")
        if s and (s not in latest or str(r.get("date", "")) > str(latest[s].get("date", ""))):
            latest[s] = r
    day_scores = {s: r.get("score") for s, r in latest.items()}
    tradeable = [r for r in latest.values() if r.get("leap")]  # skip leap == null
    original, orig_top, exceeders = split_leaps(tradeable, ORIG_WATCHLIST)

    def row(rec):
        q = quotes.get(rec["symbol"]) or {}
        return leap_row(rec, q.get("chg"))

    return {
        "max": leap_max,
        "origTopScore": orig_top,
        "original": [row(r) for r in original],
        "exceeders": [row(r) for r in exceeders],
    }, day_scores, leap_max


# ═══════════════════════════════════════════════════════════════════════════
#  Trade Journal crypto — CONTRACT-JOURNAL.md §1 (stdlib only, byte-exact
#  scheme mirrored by the pure-JS decryptor in journal_dashboard.html)
# ═══════════════════════════════════════════════════════════════════════════

def _journal_kdf(password, salt, iters=JOURNAL_PBKDF2_ITERS):
    """PBKDF2-HMAC-SHA256, dklen=64 -> (enc_key[32], mac_key[32])."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters,
                             dklen=64)
    return dk[:32], dk[32:]


def _journal_keystream(enc_key, nonce, length):
    """Keystream block i = HMAC-SHA256(enc_key, nonce || BE_uint32(i))."""
    out = bytearray()
    i = 0
    while len(out) < length:
        out += hmac.new(enc_key, nonce + struct.pack(">I", i),
                        hashlib.sha256).digest()
        i += 1
    return bytes(out[:length])


def encrypt_journal(plaintext, password, salt=None, nonce=None,
                    iters=JOURNAL_PBKDF2_ITERS):
    """Encrypt plaintext bytes -> SWING_JOURNAL_LOCKED blob dict.
    salt/nonce parameters exist for the test vector only; production callers
    leave them None (os.urandom)."""
    if salt is None:
        salt = os.urandom(16)
    if nonce is None:
        nonce = os.urandom(16)
    enc_key, mac_key = _journal_kdf(password, salt, iters)
    ct = bytes(a ^ b for a, b in
               zip(plaintext, _journal_keystream(enc_key, nonce, len(plaintext))))
    mac = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "kdf": "pbkdf2-sha256", "iter": iters,
            "salt": b64(salt), "nonce": b64(nonce),
            "ct": b64(ct), "mac": b64(mac)}


def decrypt_journal(blob, password):
    """Verify MAC (constant-time) BEFORE decrypting; raise ValueError on
    mismatch (= wrong password / tampered blob). Returns plaintext bytes."""
    salt = base64.b64decode(blob["salt"])
    nonce = base64.b64decode(blob["nonce"])
    ct = base64.b64decode(blob["ct"])
    mac = base64.b64decode(blob["mac"])
    enc_key, mac_key = _journal_kdf(password, salt,
                                    int(blob.get("iter", JOURNAL_PBKDF2_ITERS)))
    expect = hmac.new(mac_key, salt + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, mac):
        raise ValueError("MAC mismatch — wrong password or corrupted blob")
    return bytes(a ^ b for a, b in
                 zip(ct, _journal_keystream(enc_key, nonce, len(ct))))


def resolve_journal_password(args):
    """Password source, first match wins (CONTRACT-JOURNAL.md §1):
    --journal-password · --journal-password-file · env JOURNAL_DASH_PASSWORD
    · default file ~/.journal_dashboard_key. Returns str or None."""
    if args.journal_password:
        return args.journal_password
    if args.journal_password_file:
        try:
            with open(args.journal_password_file, "r", encoding="utf-8") as f:
                pw = f.read().strip()
            if pw:
                return pw
            warn(f"journal password file {args.journal_password_file} is empty")
        except OSError as e:
            warn(f"journal password file unreadable: {e}")
    env_pw = os.environ.get("JOURNAL_DASH_PASSWORD", "").strip()
    if env_pw:
        return env_pw
    if os.path.isfile(JOURNAL_KEY_PATH):
        try:
            mode = os.stat(JOURNAL_KEY_PATH).st_mode & 0o777
            if mode != 0o600:
                warn(f"{JOURNAL_KEY_PATH} permissions are {mode:o}, expected 600 "
                     f"— fix with: chmod 600 {JOURNAL_KEY_PATH}")
            with open(JOURNAL_KEY_PATH, "r", encoding="utf-8") as f:
                pw = f.read().strip()
            if pw:
                return pw
            warn(f"{JOURNAL_KEY_PATH} is empty")
        except OSError as e:
            warn(f"{JOURNAL_KEY_PATH} unreadable: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  Trade Journal computation — REUSES trade_journal.py (headless import with
#  GUI-dep stubs), CONTRACT-JOURNAL.md §2. Every failure -> WARN + skip.
# ═══════════════════════════════════════════════════════════════════════════

def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _prepare_gui_stubs():
    """Pre-inject minimal stubs for trade_journal.py's GUI-only imports —
    ONLY where the real import fails. pandas must be real (not stubbed)."""
    os.environ.setdefault("MPLBACKEND", "Agg")

    # tkinter (+ttk/filedialog/messagebox)
    try:
        import tkinter, tkinter.ttk, tkinter.filedialog, tkinter.messagebox  # noqa
    except Exception:
        tk_mod = _stub_module("tkinter")
        for sub in ("ttk", "filedialog", "messagebox"):
            setattr(tk_mod, sub, _stub_module(f"tkinter.{sub}"))

    # tkcalendar / tkinterdnd2: empty stubs are enough — trade_journal.py wraps
    # `from tkcalendar import DateEntry` etc. in try/except ImportError, and a
    # stub without the attribute raises exactly that.
    for name in ("tkcalendar", "tkinterdnd2"):
        try:
            __import__(name)
        except Exception:
            _stub_module(name)

    # matplotlib (+pyplot, dates, backends.backend_tkagg, figure)
    try:
        import matplotlib  # noqa
        try:
            import matplotlib.backends.backend_tkagg  # noqa
        except Exception:
            # real matplotlib but no Tk backend: neuter use("TkAgg") and stub
            # the backend module (only used at class-instantiation time).
            matplotlib.use = lambda *a, **k: None
            _stub_module("matplotlib.backends.backend_tkagg",
                         FigureCanvasTkAgg=object)
    except Exception:
        mpl = _stub_module("matplotlib", use=lambda *a, **k: None)
        mpl.pyplot = _stub_module("matplotlib.pyplot")
        mpl.dates = _stub_module("matplotlib.dates",
                                 DateFormatter=lambda *a, **k: None)
        backends = _stub_module("matplotlib.backends")
        backends.backend_tkagg = _stub_module(
            "matplotlib.backends.backend_tkagg", FigureCanvasTkAgg=object)
        mpl.backends = backends
        mpl.figure = _stub_module("matplotlib.figure", Figure=object)


def import_trade_journal():
    """Headless import of the user's trade_journal module.
    sys.path candidates in order: this script's dir, ~/Downloads,
    ~/trading-src/journal. Raises on failure (caller WARNs + skips)."""
    for cand in (_SCRIPT_DIR,
                 os.path.expanduser("~/Downloads"),
                 os.path.expanduser("~/trading-src/journal")):
        if os.path.isdir(cand) and cand not in sys.path:
            sys.path.append(cand)
    _prepare_gui_stubs()
    import trade_journal
    return trade_journal


def load_journal_tags(db_path):
    """{trade_key: method} from the tags DB. Missing file/table -> {}."""
    if not os.path.isfile(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT trade_key, method FROM tags").fetchall()
        finally:
            conn.close()
        return {k: v for k, v in rows}
    except sqlite3.Error as e:
        warn(f"journal tags DB unreadable at {db_path}: {e} — no saved tags")
        return {}


def apply_journal_tags(tj, legs, tags):
    """App tagging rules: saved tag wins (via LEGACY_METHOD_MAP), else
    trade_journal.default_method_for(leg). Mutates legs in place."""
    for leg in legs:
        saved = tags.get(leg["trade_key"])
        if saved:
            leg["method"] = tj.LEGACY_METHOD_MAP.get(saved, saved)
        else:
            leg["method"] = tj.default_method_for(leg)


def _journal_method_block(tj, method, stats_legs, ws, we, allocation):
    """Per-strategy card block (formulas from _refresh_summary L1543)."""
    ml = [l for l in stats_legs if l.get("method") == method]
    pl = sum(l["pl_dollar"] for l in ml)
    count = len(ml)
    wins = sum(1 for l in ml if l["pl_dollar"] > 0)
    wr = (wins / count * 100) if count else 0.0
    avg_cap = mo_dep = turnover = 0.0
    if ml and ws is not None and we is not None:
        avg_cap, mo_dep, turnover = tj.compute_time_weighted_return(ml, ws, we)
    block = {
        "pl": round(pl, 2), "count": count, "wr": round(wr, 1),
        "avgDeployed": round(avg_cap, 2), "moDeployed": round(mo_dep, 2),
        "turnover": round(turnover, 2),
    }
    if method == "Swing Trader":
        wd = (we - ws).days if (ws is not None and we is not None) else 0
        mo_sleeve = (tj.monthly_equivalent(pl / allocation, wd)
                     if (allocation > 0 and wd > 0) else 0.0)
        block["moSleeve"] = round(mo_sleeve, 2)
        block["allocation"] = allocation
        if mo_dep >= tj.SWING_MONTHLY_TARGET_PCT:
            block["badge"] = "ok"
        elif mo_dep >= 0:
            block["badge"] = "below"
        else:
            block["badge"] = "losing"
    return block


def _journal_charts(window_legs):
    """Chart series from closed non-Excluded legs (app's closed-only mode)."""
    from collections import defaultdict

    # split: P/L by strategy
    method_pl = defaultdict(float)
    for l in window_legs:
        method_pl[l.get("method", "Swing Trader")] += l["pl_dollar"]
    split = {"swing": round(method_pl.get("Swing Trader", 0.0), 2),
             "leap": round(method_pl.get("LEAP Strategy", 0.0), 2)}

    # monthly: stacked bars by sell month
    monthly_pl = defaultdict(lambda: defaultdict(float))
    for l in window_legs:
        monthly_pl[l["sell_date"].strftime("%Y-%m")][
            l.get("method", "Swing Trader")] += l["pl_dollar"]
    monthly = [{"m": m,
                "swing": round(monthly_pl[m].get("Swing Trader", 0.0), 2),
                "leap": round(monthly_pl[m].get("LEAP Strategy", 0.0), 2)}
               for m in sorted(monthly_pl.keys())]

    # periodPL: daily when <=60 unique sell days, else ISO-week (L1732)
    day_pl, day_cost = defaultdict(float), defaultdict(float)
    for l in window_legs:
        d = l["sell_date"].date()
        day_pl[d] += l["pl_dollar"]
        day_cost[d] += l["buy_cost"]
    sorted_days = sorted(day_pl.keys())
    bars = []
    if len(sorted_days) > JOURNAL_DAILY_MAX:
        mode = "weekly"
        week_pl, week_cost = defaultdict(float), defaultdict(float)
        for d in sorted_days:
            iso = d.isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
            week_pl[wk] += day_pl[d]
            week_cost[wk] += day_cost[d]
        for k in sorted(week_pl.keys()):
            pct = (week_pl[k] / week_cost[k] * 100) if week_cost[k] > 0 else 0.0
            bars.append({"k": k, "v": round(week_pl[k], 2), "pct": round(pct, 2)})
    else:
        mode = "daily"
        for d in sorted_days:
            pct = (day_pl[d] / day_cost[d] * 100) if day_cost[d] > 0 else 0.0
            bars.append({"k": d.strftime("%m/%d"), "v": round(day_pl[d], 2),
                         "pct": round(pct, 2)})
    period_pl = {"mode": mode, "bars": bars}

    # winRateBy: pct or null per strategy
    wr_by = {}
    for short, method in (("swing", "Swing Trader"), ("leap", "LEAP Strategy")):
        ml = [l for l in window_legs if l.get("method") == method]
        if ml:
            wr_by[short] = round(
                sum(1 for l in ml if l["pl_dollar"] > 0) / len(ml) * 100, 1)
        else:
            wr_by[short] = None

    # cumulative: running P/L by sell date per strategy
    cumulative = {"swing": [], "leap": []}
    running = {"Swing Trader": 0.0, "LEAP Strategy": 0.0}
    key_of = {"Swing Trader": "swing", "LEAP Strategy": "leap"}
    for l in sorted(window_legs, key=lambda x: x["sell_date"]):
        m = l.get("method", "Swing Trader")
        if m not in running:
            continue
        running[m] += l["pl_dollar"]
        cumulative[key_of[m]].append(
            [l["sell_date"].strftime("%Y-%m-%d"), round(running[m], 2)])

    return {"split": split, "monthly": monthly, "periodPL": period_pl,
            "winRateBy": wr_by, "cumulative": cumulative}


def _journal_window(tj, window_legs_all, ws, we, allocation, counts, src_csv):
    """One precomputed window. window_legs_all = the window's closed legs
    INCLUDING Excluded (visible in the trades table); stats/charts use the
    non-Excluded subset only (per _refresh_summary/_refresh_charts)."""
    stats = [l for l in window_legs_all
             if l.get("method") != "Excluded" and not l.get("is_open", False)]

    total_pl = sum(l["pl_dollar"] for l in stats)
    total_cost = sum(l["buy_cost"] for l in stats)
    total_pct = (total_pl / total_cost * 100) if total_cost > 0 else 0.0
    wins = sum(1 for l in stats if l["pl_dollar"] > 0)
    win_rate = (wins / len(stats) * 100) if stats else 0.0

    trades = []
    for l in sorted(window_legs_all, key=lambda x: x["sell_date"],
                    reverse=True)[:JOURNAL_TRADES_CAP]:
        trades.append({
            "d": l["sell_date"].strftime("%Y-%m-%d"),
            "t": l["ticker_label"],
            "m": l.get("method", "Swing Trader"),
            "qty": l["qty_str"],
            "buy": round(l["buy_price"], 2),
            "sell": round(l["sell_price"], 2),
            "pl": l["pl_dollar"],
            "plPct": l["pl_pct"],
            "held": l["hold_days"],
            "opt": bool(l.get("is_option")),
        })

    return {
        "totals": {"pl": round(total_pl, 2), "pct": round(total_pct, 2),
                   "trades": len(stats), "costBasis": round(total_cost, 2)},
        "winRate": {"pct": round(win_rate, 1), "w": wins, "l": len(stats) - wins},
        "swing": _journal_method_block(tj, "Swing Trader", stats, ws, we, allocation),
        "leap": _journal_method_block(tj, "LEAP Strategy", stats, ws, we, allocation),
        "charts": _journal_charts(stats),
        "trades": trades,
        "counts": counts,
        "sourceCsv": src_csv,
    }


def build_journal_payload(tj, closed, opens, orphans, csv_path):
    """The journal JSON (CONTRACT-JOURNAL.md §2.4-2.6): four windows exactly
    like _apply_filters (trade_journal.py L1292)."""
    now = dt.datetime.now()
    allocation = tj.get_swing_allocation()
    counts = {"closed": len(closed), "open": len(opens), "orphans": len(orphans)}
    src_csv = os.path.basename(csv_path)

    windows = {}
    for key in ("30d", "90d", "ytd", "all"):
        if key == "30d":
            ws, we = now - dt.timedelta(days=30), now
            legs = [l for l in closed if l["sell_date"] >= ws]
        elif key == "90d":
            ws, we = now - dt.timedelta(days=90), now
            legs = [l for l in closed if l["sell_date"] >= ws]
        elif key == "ytd":
            ws, we = dt.datetime(now.year, 1, 1), now
            legs = [l for l in closed if l["sell_date"] >= ws]
        else:  # all — window start = min(first buy, first sell), end = now
            legs = list(closed)
            if legs:
                first_sell = min(l["sell_date"] for l in legs)
                first_buys = [l["buy_date"] for l in legs if l.get("buy_date")]
                first_buy = min(first_buys) if first_buys else first_sell
                ws, we = min(first_buy, first_sell), now
            else:
                ws, we = None, None
        windows[key] = _journal_window(tj, legs, ws, we, allocation,
                                       counts, src_csv)

    return {"generated": now.isoformat(timespec="seconds"),
            "allocation": allocation,
            "windows": windows,
            "counts": counts,
            "sourceCsv": src_csv}


def build_journal_locked(args):
    """Full journal pipeline -> encrypted SWING_JOURNAL_LOCKED blob dict, or
    None (with a WARN) on any degrade path. NEVER emits plaintext."""
    if args.no_journal:
        print("journal section skipped (--no-journal)", flush=True)
        return None

    password = resolve_journal_password(args)
    if not password:
        warn("journal password not configured — Trade Journal section omitted. "
             "Create ~/.journal_dashboard_key (chmod 600) or pass "
             "--journal-password / --journal-password-file / "
             "JOURNAL_DASH_PASSWORD (see README).")
        return None

    try:
        tj = import_trade_journal()
    except Exception as e:
        warn(f"journal section unavailable: trade_journal.py not importable "
             f"({e}) — expected next to this script, in ~/Downloads, or "
             f"~/trading-src/journal")
        return None

    if args.journal_config:
        tj.CONFIG_PATH = args.journal_config  # test override

    csv_path = args.journal_csv
    if not csv_path:
        try:
            csv_path = tj.load_config().get("last_csv_path")
        except Exception as e:
            warn(f"journal config unreadable: {e} — journal section omitted")
            return None
    if not csv_path or not os.path.isfile(csv_path):
        warn(f"journal CSV not found ({csv_path or 'no last_csv_path in config'})"
             f" — journal section omitted (pass --journal-csv or load a CSV in "
             f"the journal app once)")
        return None

    try:
        closed, opens, orphans = tj.parse_fidelity_csv(csv_path)
    except Exception as e:
        warn(f"journal CSV parse failed for {csv_path}: {e} — journal section "
             f"omitted")
        return None

    tags = load_journal_tags(args.journal_tags_db or tj.DB_PATH)
    apply_journal_tags(tj, closed + opens + orphans, tags)

    try:
        journal = build_journal_payload(tj, closed, opens, orphans, csv_path)
    except Exception as e:
        warn(f"journal computation failed: {e} — journal section omitted")
        return None

    plaintext = json.dumps(journal, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return encrypt_journal(plaintext, password)


# ═══════════════════════════════════════════════════════════════════════════
#  Self-test — pure mapping functions only, ZERO file/network access
# ═══════════════════════════════════════════════════════════════════════════

def selftest():
    # signal cleanup
    assert clean_signal("🔥 ACT NOW") == "ACT NOW"
    assert clean_signal("🔥 ACT NOW ⭐") == "ACT NOW"
    assert clean_signal("⚡ ARB BUY") == "ARB BUY"
    assert clean_signal("▲ BUY") == "BUY"
    assert clean_signal("▼ SELL") == "SELL"
    assert clean_signal("◌ WATCH") == "WATCH"
    assert clean_signal("⊘ SUPPRESSED") == "SUPPRESSED"
    assert clean_signal(None) == "WATCH"

    # statusKind mapping
    assert status_kind(["HOLD"]) == "good"
    assert status_kind(["WATCH?"]) == "warn"
    assert status_kind(["ROT~"]) == "warn"
    assert status_kind(["SUSPECT"]) == "warn"
    assert status_kind(["WATCH"]) == "bad"
    assert status_kind(["ROT"]) == "bad"
    assert status_kind(["WATCH", "ROT"]) == "bad"
    assert status_kind(["WATCH?", "ROT"]) == "bad"

    # ACT NOW derivations vs hand-computed values (MCHP: price 84.64, ATR 5.375)
    r = {"symbol": "MCHP", "price": 84.64, "rsi": 40.8, "atr": 5.375,
         "final_score": 93, "signal": "🔥 ACT NOW",
         "setup_profile": {"type": "Oversold Bounce", "description": "note"}}
    row = act_now_row(r, dt.date(2026, 7, 4), "14:32")
    assert abs(row["stop"] - 73.89) < 0.005, row["stop"]        # 84.64 - 2*5.375
    assert abs(row["t1"] - 95.39) < 0.005, row["t1"]            # 84.64 + 2*5.375
    assert abs(row["t2"] - 100.77) < 0.015, row["t2"]           # 84.64 + 3*5.375
    assert abs(row["maxChase"] - 85.06) < 0.005, row["maxChase"]  # *1.005
    assert abs(row["stopPerShare"] - (-10.75)) < 0.005
    assert abs(row["stopPct"] - (-12.7)) < 0.05, row["stopPct"]
    assert abs(row["t1Pct"] - 12.7) < 0.05 and abs(row["t2Pct"] - 19.1) < 0.05
    assert row["rr1"] == "1.0:1" and row["rr2"] == "1.5:1"
    assert row["hold"] == "7-21d"
    assert row["hardStop"] == "Jul 25, 2026", row["hardStop"]   # 2026-07-04 + 21d
    assert row["entry"] == 84.64 and row["theme"] == "Oversold Bounce"
    assert row["signal"] == "ACT NOW", row["signal"]

    # card selection: actionable tiers over CARD_MIN; WATCH/SELL never card
    fake_swing = {"results": [
        {"symbol": "HPE",  "price": 48.88,  "rsi": 60, "atr": 11.59, "final_score": 93, "signal": "🔥 ACT NOW"},
        {"symbol": "COST", "price": 914.21, "rsi": 35, "atr": 23.37, "final_score": 80, "signal": "⚡ ARB BUY"},
        {"symbol": "GS",   "price": 1055.0, "rsi": 54, "atr": 5.87,  "final_score": 85, "signal": "▲ BUY"},
        {"symbol": "IONQ", "price": 44.75,  "rsi": 36, "atr": 3.0,   "final_score": 77, "signal": "▲ BUY"},
        {"symbol": "LOW",  "price": 212.88, "rsi": 42, "atr": 5.90,  "final_score": 85, "signal": "◌ WATCH"},
        {"symbol": "SCHW", "price": 101.89, "rsi": 72, "atr": 2.0,   "final_score": 99, "signal": "▼ SELL"},
        {"symbol": "GM",   "price": 76.15,  "rsi": 41, "atr": 2.0,   "final_score": 75, "signal": "▲ BUY"},  # not > 75
    ]}
    cards, table = build_swing_section(fake_swing, dt.date(2026, 7, 9), "22:59")
    assert [c["t"] for c in cards] == ["HPE", "GS", "COST", "IONQ"], [c["t"] for c in cards]
    assert [c["signal"] for c in cards] == ["ACT NOW", "BUY", "ARB BUY", "BUY"]
    assert {r_["t"] for r_ in table} == {"HPE", "COST", "GS", "IONQ", "LOW", "SCHW"}  # >75, all signals

    # watchlist split
    recs = [
        {"symbol": "ORCL", "score": 11, "date": "d"},   # watchlist
        {"symbol": "BMNR", "score": 10, "date": "d"},   # watchlist
        {"symbol": "NOW",  "score": 9,  "date": "d"},   # watchlist
        {"symbol": "SSYS", "score": 4,  "date": "d"},   # watchlist (4th, cut from top3)
        {"symbol": "MRVL", "score": 13, "date": "d"},   # exceeder
        {"symbol": "INTC", "score": 12, "date": "d"},   # exceeder
        {"symbol": "PYPL", "score": 8,  "date": "d"},   # below orig top -> dropped
    ]
    original, top, exceeders = split_leaps(recs, ORIG_WATCHLIST)
    assert top == 11
    assert [x["symbol"] for x in original] == ["ORCL", "BMNR", "NOW"]
    assert [x["symbol"] for x in exceeders] == ["MRVL", "INTC"]
    o2, t2_, e2 = split_leaps([x for x in recs if x["symbol"] in ("MRVL", "INTC")],
                              ORIG_WATCHLIST)
    assert t2_ == 0 and [x["symbol"] for x in e2] == ["MRVL", "INTC"] and o2 == []

    # rev mapping
    assert rev_text({"rev_confirmed": True}) == "✅ confirmed"
    assert rev_text({"rev_confirmed": False}) == "❌ not confirmed"
    assert rev_text({}) == "—"

    # ── Journal crypto: cross-language TEST VECTOR (CONTRACT-JOURNAL.md §1) ──
    vec_salt = bytes(range(0x00, 0x10))
    vec_nonce = bytes(range(0x10, 0x20))
    vec_pt = b'{"hello":"journal","n":42}'
    blob = encrypt_journal(vec_pt, "test-password-123",
                           salt=vec_salt, nonce=vec_nonce, iters=60000)
    assert blob["salt"] == "AAECAwQFBgcICQoLDA0ODw==", blob["salt"]
    assert blob["nonce"] == "EBESExQVFhcYGRobHB0eHw==", blob["nonce"]
    assert blob["ct"] == "8b1TGRkQdqIcsvBz3ROoD9QduZQji8LmU1Q=", blob["ct"]
    assert blob["mac"] == "4k9ErqDSS9amw8ch9P1EnmanvpePwwxk9AeoEQOHxgM=", blob["mac"]
    assert blob["v"] == 1 and blob["kdf"] == "pbkdf2-sha256" and blob["iter"] == 60000
    assert decrypt_journal(blob, "test-password-123") == vec_pt

    # random round-trip (multi-block keystream: > 32 bytes)
    rnd_pt = os.urandom(517)
    rnd_blob = encrypt_journal(rnd_pt, "hunter2-λ-ünïcode")
    assert decrypt_journal(rnd_blob, "hunter2-λ-ünïcode") == rnd_pt
    assert rnd_blob["ct"] != base64.b64encode(rnd_pt).decode("ascii")

    # wrong password -> MAC rejection BEFORE any decryption
    try:
        decrypt_journal(rnd_blob, "hunter2-wrong")
        raise AssertionError("wrong password was NOT rejected")
    except ValueError:
        pass
    # tampered ciphertext -> MAC rejection too
    bad = dict(rnd_blob)
    ct_bytes = bytearray(base64.b64decode(bad["ct"]))
    ct_bytes[0] ^= 0x01
    bad["ct"] = base64.b64encode(bytes(ct_bytes)).decode("ascii")
    try:
        decrypt_journal(bad, "hunter2-λ-ünïcode")
        raise AssertionError("tampered ciphertext was NOT rejected")
    except ValueError:
        pass

    print("selftest OK — signal cleanup, statusKind, ACT NOW derivations, "
          "watchlist split, rev mapping, journal crypto (test vector + "
          "random round-trip + wrong-password rejection) all pass")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

def main(argv=None):
    ap = argparse.ArgumentParser(description="Build journal_data.js for the journal dashboard")
    ap.add_argument("--offline", action="store_true", help="no network; use --quotes fixture")
    ap.add_argument("--quotes", default=None, help="offline quotes fixture JSON")
    ap.add_argument("--swing", default=SWING_JSON_PATH)
    ap.add_argument("--csv", default=CSV_PATH)
    ap.add_argument("--leap", default=LEAP_JSON_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--history", default=HISTORY_PATH)
    ap.add_argument("--no-history", action="store_true", help="write single date only")
    ap.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                    help="override journal date (default: swing timestamp's date, else today)")
    ap.add_argument("--selftest", action="store_true", help="run pure-function assertions and exit")
    # Trade Journal (locked) section — CONTRACT-JOURNAL.md
    ap.add_argument("--journal-csv", default=None,
                    help="Fidelity trade-history CSV (default: trade_journal "
                         "config's last_csv_path)")
    ap.add_argument("--journal-tags-db", default=None,
                    help="override tags SQLite path (default: trade_journal.DB_PATH)")
    ap.add_argument("--journal-config", default=None,
                    help="override trade_journal config JSON path (tests)")
    ap.add_argument("--journal-password", default=None,
                    help="journal encryption password (prefer the key file)")
    ap.add_argument("--journal-password-file", default=None,
                    help="file containing the journal password")
    ap.add_argument("--no-journal", action="store_true",
                    help="skip the encrypted Trade Journal section")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    print(f"classify source: {_CLASSIFY_SOURCE}")

    # ── Load inputs (each degrades independently) ──
    swing = load_json(args.swing, "swing results")
    csv_rows = load_csv(args.csv)
    leap_recs = load_json(args.leap, "LEAP recommendations")
    if leap_recs is not None and not isinstance(leap_recs, list):
        warn("LEAP file is not a list — ignored")
        leap_recs = None

    # ── Journal date + scan time ──
    swing_ts = None
    if swing and swing.get("timestamp"):
        try:
            swing_ts = dt.datetime.fromisoformat(str(swing["timestamp"]))
        except ValueError:
            warn(f"unparseable swing timestamp {swing.get('timestamp')!r}")
    if args.date:
        journal_date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    elif swing_ts:
        journal_date = swing_ts.date()
    else:
        journal_date = dt.date.today()
    date_key = journal_date.isoformat()
    as_of = swing_ts.strftime("%H:%M") if swing_ts else "—"
    session = f"Scan {as_of} ET" if swing_ts else "No scan data"

    # ── Quotes (offline fixture or live yfinance; failures -> null) ──
    quotes, indexes = {}, []
    if args.offline:
        if args.quotes:
            qfix = load_json(args.quotes, "quotes fixture") or {}
            quotes = qfix.get("quotes", {}) or {}
            indexes = qfix.get("indexes", []) or []
        else:
            warn("--offline without --quotes: all quote-derived fields will be null")
    else:
        tickers = set()
        for row in csv_rows:
            t = (row.get("Ticker") or "").strip().upper()
            if t:
                tickers.add(t)
        for r in (leap_recs or []):
            if str(r.get("date", "")).startswith(date_key) and r.get("symbol"):
                tickers.add(r["symbol"])
        quotes = fetch_quotes_live(sorted(tickers))
        indexes = fetch_indexes_live()

    # ── Build sections ──
    act_now, over75 = build_swing_section(swing, journal_date, as_of)
    positions = build_positions(csv_rows, journal_date, quotes, args.offline)
    leaps, day_scores, leap_max = build_leaps(leap_recs, date_key, quotes)
    heatmap = build_heatmap(csv_rows, quotes, day_scores, leap_max)
    market = build_market(swing, indexes)

    day_payload = {
        "label": f"{journal_date:%a}, {journal_date:%b} {journal_date.day} {journal_date.year}",
        "session": session,
        "market": market,
        "heatmap": heatmap,
        "leaps": leaps,
        "swing": {"actNow": act_now, "over75": over75, "positions": positions},
        "meta": {"source": "live",
                 "generated": (swing_ts or dt.datetime.now()).isoformat(timespec="seconds")},
    }

    # ── History maintenance ──
    if args.no_history:
        journals = {date_key: day_payload}
    else:
        history = {}
        if os.path.exists(args.history):
            loaded = load_json(args.history, "journal history")
            if isinstance(loaded, dict):
                history = loaded
            elif loaded is not None:
                warn("journal history is not an object — starting fresh")
        history[date_key] = day_payload
        kept = sorted(history.keys(), reverse=True)[:HISTORY_KEEP]
        journals = {k: history[k] for k in sorted(kept)}
        try:
            with open(args.history, "w", encoding="utf-8") as f:
                json.dump(journals, f, indent=1, ensure_ascii=False)
        except Exception as e:
            warn(f"could not write history {args.history}: {e}")

    # ── Trade Journal (locked) section — encrypted blob, recomputed fresh
    #    each run, emitted top-level only (never into per-date payloads or
    #    journal_history.json). None => section omitted (WARN already printed).
    journal_locked = build_journal_locked(args)

    # ── Emit journal_data.js ──
    payload = json.dumps(journals, indent=1, ensure_ascii=False)
    out_text = "window.SWING_JOURNALS = " + payload + ";\n"
    if journal_locked is not None:
        out_text += ("window.SWING_JOURNAL_LOCKED = "
                     + json.dumps(journal_locked, separators=(",", ":"))
                     + ";\n")
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_text)
    except Exception as e:
        print(f"ERROR: could not write output {args.out}: {e}", flush=True)
        return 1

    # ── One-screen summary ──
    print()
    print("=" * 60)
    print(f"Journal date : {date_key}  ({day_payload['label']})")
    print(f"Session      : {session}")
    print(f"Market strip : {len(market['indexes'])} index tiles · {market['context']}")
    print(f"ACT NOW      : {len(act_now)}  -> {', '.join(x['t'] for x in act_now) or '(none)'}")
    print(f"Over-{TABLE_MIN} table: {len(over75)} rows")
    print(f"Positions    : {len(positions)} swing tickers")
    n_act = sum(1 for h in heatmap if h["group"] == "active")
    n_exc = sum(1 for h in heatmap if h["group"] == "excluded")
    print(f"Heatmap      : {len(heatmap)} tiles ({n_act} active / {n_exc} excluded)")
    print(f"LEAPs        : original {len(leaps['original'])} (top {leaps['origTopScore']}/{leaps['max']}) "
          f"· exceeders {len(leaps['exceeders'])}")
    if args.no_journal:
        journal_line = "skipped (--no-journal)"
    elif journal_locked is not None:
        journal_line = (f"encrypted blob emitted ({len(journal_locked['ct'])} "
                        f"b64 chars, PBKDF2 {journal_locked['iter']} iters)")
    else:
        journal_line = "omitted (see WARN above)"
    print(f"Journal      : {journal_line}")
    print(f"Dates in file: {len(journals)}"
          + ("" if args.no_history else f" (history keeps {HISTORY_KEEP})"))
    print(f"Output       : {args.out}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
