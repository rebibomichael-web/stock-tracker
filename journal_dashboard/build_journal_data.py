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
import csv
import datetime as dt
import json
import os
import sys

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

ACT_NOW_MIN  = 85   # actNow guard: final_score >= this AND signal is ACT NOW
TABLE_MIN    = 75   # over75 table: final_score > this
HISTORY_KEEP = 30   # most recent dates kept in journal_history.json

# ACT NOW card derivation constants (swing_trader.py:1507-1521, CFG values)
STOP_M    = 2.0
T1_M      = 2.0
T2_M      = 3.0
TIME_STOP = 21      # calendar days -> hardStop; hold text "7-21d"

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
                          {"SUSPECT": -1, "WATCH": 0, "ROT": 0,
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
    _FLAG_ORDER = {"SUSPECT": -1, "WATCH": 0, "ROT": 0,
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
            # actNow: the fired ACT NOW tier (score guard ACT_NOW_MIN).
            if sig == "ACT NOW" and final >= ACT_NOW_MIN:
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

    print("selftest OK — signal cleanup, statusKind, ACT NOW derivations, "
          "watchlist split, rev mapping all pass")
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

    # ── Emit journal_data.js ──
    payload = json.dumps(journals, indent=1, ensure_ascii=False)
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("window.SWING_JOURNALS = " + payload + ";\n")
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
    print(f"Dates in file: {len(journals)}"
          + ("" if args.no_history else f" (history keeps {HISTORY_KEEP})"))
    print(f"Output       : {args.out}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
