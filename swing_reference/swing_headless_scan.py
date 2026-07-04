#!/usr/bin/env python3
"""
swing_headless_scan.py — diagnostic harness

Replicates swing_trader.py's REAL _scan_bg pipeline (L3075-3139) with zero
tkinter/GUI involvement, to determine whether the scan/scoring LOGIC works
independently of the render layer. Read-only against swing_trader.py — only
imports its functions/classes, never modifies it.

Scope: faithfully runs Phase 1 (per-ticker fetch + raw scoring), Phase 2
(backtest adjustment via the real Cache), then the real score_and_assign(cands,
mult, dm) batch call — exactly the part of _scan_bg that runs BEFORE any
tkinter touches (self.root.after progress updates, _render_scan, fire popups,
Tracker.log, dm.save_scan_summary, dm.log_fire_alert, Barchart scraping,
position-monitoring). Those downstream steps are GUI-adjacent or write
secondary records and are out of scope for this diagnostic.

GUI-coupling note: the ONLY tkinter dependency found in _scan_bg's path up to
score_and_assign is `self._mode` (a tk.StringVar) used to pick the regime
multiplier index. This harness hardcodes MODE = "normal" instead of
self._mode.get() — see below. Everything else on the path (Regime, Cache,
DataManager, Arb, Events, fetch_ohlcv, add_indicators, buy_score,
classify_setup, score_and_assign) is plain Python with no tkinter coupling
(confirmed in Step 1 recon).
"""
import sys, os, time, json, datetime as dt

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)  # so `import swing_trader` / `data_layer` resolve regardless of cwd

import numpy as np
import pandas as pd
import yfinance as yf

from swing_trader import (
    UNIVERSE, CFG, Events, fetch_ohlcv, fetch_quote, add_indicators,
    buy_score, sell_score, Arb, classify_setup, score_and_assign,
    passes_gates, Regime, Cache, backtest,
)
from data_layer import DataManager

MODE = "normal"  # GUI touchpoint replaced: was self._mode.get() (tk.StringVar, unavailable headless)

RESULTS_JSON = os.path.expanduser("~/Desktop/swing_headless_results.json")
LOG_PATH = os.path.expanduser("~/Desktop/swing_headless_scan.log")

_logf = None  # opened lazily in main() so a bare `import` has zero filesystem side effects


def log(msg):
    ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"{ts} {msg}"
    print(line, flush=True)
    _logf.write(line + "\n")
    _logf.flush()


SIGNAL_TIER = {
    "🔥 ACT NOW": "ACT_NOW", "⚡ ARB BUY": "ARB_BUY", "▲ BUY": "BUY",
    "▼ SELL": "SELL", "◌ WATCH": "WATCH", "⊘ SUPPRESSED": "SUPPRESSED",
}


def tier_of(signal):
    return SIGNAL_TIER.get(signal.replace(" ⭐", ""), signal)


def _json_default(o):
    if isinstance(o, set):
        return list(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)):
        return str(o)
    return str(o)


def main():
    global _logf
    _logf = open(LOG_PATH, "a", buffering=1)
    t0 = time.time()
    log(f"=== swing_headless_scan starting — mode={MODE} ===")

    scan_universe = sorted(set(UNIVERSE))
    total = len(scan_universe)
    log(f"Universe: {total} tickers")

    vix = 20.0
    try:
        vh = yf.Ticker("^VIX").history(period="5d", interval="1d", auto_adjust=True)
        if vh is not None and len(vh) > 0:
            vix = vh["Close"].iloc[-1]
    except Exception as e:
        log(f"WARN VIX fetch failed: {e}")
    log(f"VIX: {vix}")

    try:
        Arb.fetch_spy()
    except Exception as e:
        log(f"WARN Arb.fetch_spy() failed: {e}")

    regime = Regime()
    try:
        regime.assess()
    except Exception as e:
        log(f"WARN regime.assess() failed: {e} — defaulting state={regime.state}")
    mult = regime.mult(MODE)
    log(f"Regime: state={regime.state} mult={mult} (mode={MODE})")

    cache = Cache()
    dm = DataManager()

    cands = []
    ticker_meta = {}  # symbol -> {"fetch_time":.., "status_type":.., "detail":..}
    fetched_ok = 0
    skipped = 0
    errors = 0

    # ─── Phase 1: per-ticker fetch + raw scoring (faithful to _scan_bg L3087-3124) ───
    for i, sym in enumerate(scan_universe):
        n = i + 1
        t_fetch0 = time.time()
        try:
            evt = Events.check(sym, fast=True)
            if evt["status"] != "OK":
                skipped += 1
                ft = time.time() - t_fetch0
                ticker_meta[sym] = {"fetch_time": ft, "status_type": "SKIP", "detail": evt["status"]}
                log(f"[{n}/{total}] {sym}  fetch={ft:.2f}s  status=SKIP({evt['status']})")
                continue
            df = fetch_ohlcv(sym, "6mo")
            if df is None or len(df) < 50:
                skipped += 1
                ft = time.time() - t_fetch0
                ticker_meta[sym] = {"fetch_time": ft, "status_type": "SKIP", "detail": "no_data"}
                log(f"[{n}/{total}] {sym}  fetch={ft:.2f}s  status=SKIP(no_data)")
                continue
            add_indicators(df)
            last, prev = df.iloc[-1], df.iloc[-2]
            price, atr, rsi = last["Close"], last.get("ATR", 0), last.get("RSI", 50)
            if atr <= 0 or price <= 0:
                skipped += 1
                ft = time.time() - t_fetch0
                ticker_meta[sym] = {"fetch_time": ft, "status_type": "SKIP", "detail": "bad_atr_price"}
                log(f"[{n}/{total}] {sym}  fetch={ft:.2f}s  status=SKIP(bad_atr_price)")
                continue
            sw = CFG.stop_m * atr / price * 100
            bs, bn, bc = buy_score(last, prev)
            ss, sn = sell_score(last, prev)
            arb = Arb.detect(df)
            evt_pen = evt.get("penalty", 0)
            bsa_filter = int(bs * mult) + evt_pen
            ssa_filter = int(ss * mult)
            q = fetch_quote(sym)
            if q.get("extended") and q.get("change_pct", 0) < -3:
                bsa_filter += 20
                evt_pen += 20
            ind = {k: last.get(k, 0) for k in
                   ["RSI", "RSI3", "MACD_hist", "EMA9", "EMA21", "EMA50", "BB_pct", "StochRSI",
                    "VolRatio", "VWAP", "OBV_slope", "WillR", "ROC5", "ROC20"]}
            ind["RSI_div"] = last.get("RSI_div", False)
            ind["CMF"] = last.get("CMF", 0)
            ind["OBV_div_bull"] = last.get("OBV_div_bull", False)
            ind["Fib_near_382"] = last.get("Fib_near_382", False)
            ind["Fib_near_50"] = last.get("Fib_near_50", False)
            ind["Fib_382"] = last.get("Fib_382", 0)
            ind["Fib_50"] = last.get("Fib_50", 0)
            ind["MACD_hist_prev"] = prev.get("MACD_hist", 0)
            ind["EMA9_prev"] = prev.get("EMA9", 0)
            ind["EMA21_prev"] = prev.get("EMA21", 0)
            ind["swing_low_20"] = last.get("swing_low_20", 0)
            ind["swing_high_20"] = last.get("swing_high_20", 0)
            ind["EMA200"] = last.get("EMA200", 0)
            ind["BB_upper"] = last.get("BB_upper", 0)
            ind["BB_lower"] = last.get("BB_lower", 0)
            ind["Open"] = last.get("Open", 0)
            ind["High"] = last["High"]
            ind["Low"] = last["Low"]
            ind["Close"] = last["Close"]
            try:
                sp = classify_setup(df, len(df) - 1, bc, arb)
            except Exception:
                sp = None
            e = {"symbol": sym, "price": price, "rsi": rsi,
                 "buy_score": bs + evt_pen, "sell_score": ss,
                 "raw_buy": bs, "raw_sell": ss,
                 "atr_swing": sw, "conditions": bc, "n_conditions": bn, "event": evt, "atr": atr,
                 "extra_bonus": evt_pen, "arb": arb, "indicators": ind, "setup_profile": sp}
            fetched_ok += 1
            ft = time.time() - t_fetch0
            if bsa_filter >= 45 or ssa_filter >= CFG.min_sell:
                cands.append(e)
                ticker_meta[sym] = {"fetch_time": ft, "status_type": "CANDIDATE", "detail": ""}
                log(f"[{n}/{total}] {sym}  fetch={ft:.2f}s  status=CANDIDATE  raw_buy={bs}  raw_sell={ss}")
            elif bs >= 35:
                e["_w"] = True
                cands.append(e)
                ticker_meta[sym] = {"fetch_time": ft, "status_type": "CANDIDATE_WEAK", "detail": ""}
                log(f"[{n}/{total}] {sym}  fetch={ft:.2f}s  status=CANDIDATE_WEAK  raw_buy={bs}  raw_sell={ss}")
            else:
                ticker_meta[sym] = {"fetch_time": ft, "status_type": "BELOW_THRESHOLD", "detail": ""}
                log(f"[{n}/{total}] {sym}  fetch={ft:.2f}s  status=BELOW_THRESHOLD  raw_buy={bs}  raw_sell={ss}")
        except Exception as ex:
            errors += 1
            ft = time.time() - t_fetch0
            ticker_meta[sym] = {"fetch_time": ft, "status_type": "ERROR", "detail": str(ex)}
            log(f"[{n}/{total}] {sym}  fetch={ft:.2f}s  ERROR: {ex}")
        time.sleep(0.05)

    log(f"--- Phase 1 done: fetched_ok={fetched_ok}  skipped={skipped}  errors={errors}  candidates={len(cands)} ---")

    # ─── Phase 2: backtest adjustment via the real Cache (faithful to _scan_bg L3126-3138) ───
    bt_eligible = [c for c in cands if c.get("buy_score", 0) >= 45]
    log(f"--- Phase 2: backtest adj for {len(bt_eligible)} candidates ---")
    for j, c in enumerate(bt_eligible):
        sym = c["symbol"]
        t_bt0 = time.time()
        try:
            a = cache.adj(sym)
            if a == 0 and not cache.fresh():
                d2 = fetch_ohlcv(sym)
                if d2 is not None and len(d2) >= 50:
                    add_indicators(d2)
                    cache.put(sym, backtest(d2)["stats"])
                    a = cache.adj(sym)
            a = min(max(a, -20), 10)
            c["bt_adj"] = a
            log(f"[{j + 1}/{len(bt_eligible)}] BTADJ {sym}  bt_adj={a}  ({time.time() - t_bt0:.2f}s)")
        except Exception as ex:
            c["bt_adj"] = 0
            log(f"[{j + 1}/{len(bt_eligible)}] BTADJ {sym}  ERROR: {ex}")
        time.sleep(0.05)
    cache.save()
    for c in cands:
        if "bt_adj" not in c:
            c["bt_adj"] = 0

    # ─── score_and_assign — the real, pure batch scoring call ───
    log(f"--- Calling score_and_assign() on {len(cands)} candidates ---")
    results, bp, buy_count = score_and_assign(cands, mult, dm)
    log(f"--- score_and_assign done: breadth_mult={bp}  buy_count={buy_count} ---")

    # ─── Final per-ticker RESULT lines (exact format requested) ───
    results_by_sym = {r["symbol"]: r for r in results}
    tier_counts = {}
    for i, sym in enumerate(scan_universe):
        n = i + 1
        meta = ticker_meta.get(sym, {"fetch_time": 0.0, "status_type": "UNKNOWN", "detail": ""})
        r = results_by_sym.get(sym)
        if r is not None:
            tier = tier_of(r["signal"])
            gate = "PASS" if passes_gates(r["final_score"], r["n_conditions"], r["conditions"], r["atr"], r["price"]) else "FAIL"
            score = r["final_score"]
        else:
            tier = meta["status_type"]
            gate = "N/A"
            score = "N/A"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        log(f"[{n}/{total}] {sym}  fetch={meta['fetch_time']:.2f}s  score={score}  tier={tier}  gate={gate}")

    elapsed = time.time() - t0
    log("=== SUMMARY ===")
    log(f"Wall clock: {elapsed:.1f}s ({elapsed / 60:.1f} min)")
    log(f"Universe: {total}  fetched_ok: {fetched_ok}  skipped: {skipped}  errors: {errors}  candidates: {len(cands)}")
    for tier, c in sorted(tier_counts.items(), key=lambda kv: -kv[1]):
        log(f"  {tier}: {c}")

    out = {
        "timestamp": dt.datetime.now().isoformat(),
        "elapsed_sec": elapsed,
        "mode": MODE,
        "regime_state": regime.state,
        "regime_mult": mult,
        "vix": vix,
        "breadth_mult": bp,
        "buy_count": buy_count,
        "universe_total": total,
        "fetched_ok": fetched_ok,
        "skipped": skipped,
        "errors": errors,
        "tier_counts": tier_counts,
        "results": results,
    }
    with open(RESULTS_JSON, "w") as f:
        json.dump(out, f, indent=2, default=_json_default)
    log(f"Results written to {RESULTS_JSON}")
    _logf.close()


if __name__ == "__main__":
    main()
