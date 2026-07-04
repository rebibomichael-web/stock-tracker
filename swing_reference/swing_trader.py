#!/usr/bin/env python3
"""
Michael Swing Trader — Algorithmic Swing Trading Platform

Data-driven scoring, zero redundancy, single-file architecture.
Every function earns its place. Every constant is named once.
"""

from __future__ import annotations
import csv, datetime as dt, json, logging, math, os, re, subprocess, threading, time, traceback
import requests
from bs4 import BeautifulSoup
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import pandas as pd
import yfinance as yf
from data_layer import DataManager, STATE_FILE

log = logging.getLogger("mst")
if not log.handlers:
    log.setLevel(logging.INFO)
    _log_path = os.path.expanduser("~/.michael_swing_trader/app.log")
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)
    _h = logging.FileHandler(_log_path)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)

# ═══════════════════════════════════════════════════════════════
# CONFIG — every tunable parameter, one place
# ═══════════════════════════════════════════════════════════════

@dataclass
class Config:
    min_buy: int = 70
    min_sell: int = 72
    min_conds: int = 7
    min_swing: float = 4.0
    fire: int = 90
    stop_m: float = 2.0
    t1_m: float = 2.0
    t2_m: float = 3.0
    time_stop: int = 21
    comm: float = 1.0
    risk: float = 0.02
    capital: float = 10_000.0
    regime_refresh: int = 30
    cache_path: str = os.path.expanduser("~/.michael_swing_bt_cache.json")
    tracker_path: str = os.path.expanduser("~/.michael_swing_signals.json")
    regime_mult: dict = field(default_factory=lambda: {
        "bull": [1.10, 1.10, 1.10], "normal": [1.00, 1.00, 1.00],
        "caution": [0.85, 0.80, 0.70], "danger": [0.75, 0.65, 0.50],
    })

CFG = Config()

# Palette
P = {"bg":"#ffffff","panel":"#f5f6f8","card":"#eef0f4","border":"#d0d4dc",
     "text":"#1a1d24","muted":"#4a5068","accent":"#1a56db","green":"#0d7a3e",
     "red":"#c42b2b","orange":"#cc4400","gold":"#b8860b","yellow":"#e6a800"}

CORE = ["PLTR","SOFI","MU","CRWD","SNOW","LMND","ORCL","NVDA","AMD","TSLA","COIN","MSTR"]
_DEFAULT = sorted(set(CORE + [
    "AAPL","MSFT","GOOGL","AMZN","META","NFLX","CRM","ADBE","INTC","CSCO","IBM",
    "QCOM","TXN","AVGO","AMAT","LRCX","KLAC","MRVL","ON","SWKS","MCHP",
    "XYZ","PYPL","V","MA","GS","JPM","BAC","WFC","MS","SCHW","C","AXP","BLK",
    "HOOD","AFRM","UPST","NOW","DDOG","ZS","NET","PANW","FTNT","MDB","TEAM",
    "HUBS","WDAY","VEEV","TTD","SHOP","ROKU","SPOT","U","RBLX","ARM","SMCI",
    "TSM","ASML","SNPS","CDNS","WOLF","RIVN","LCID","FSLR","ENPH","NEE","CEG",
    "VST","UNH","LLY","JNJ","PFE","ABBV","MRK","TMO","ISRG","DXCM","MRNA",
    "REGN","VRTX","ILMN","COST","WMT","TGT","NKE","SBUX","MCD","DIS","ABNB",
    "BKNG","UBER","LYFT","DASH","CAT","DE","BA","HON","GE","RTX","LMT","NOC",
    "MARA","RIOT","CLSK","NEM","FCX","AA","IONQ","RGTI","PATH","AI",
    "DKNG","PENN","AMGN","GILD","BMY","T","VZ","CMCSA","PEP","KO","PM","MO",
    "CVX","XOM","COP","SLB","OXY","GM","F","LULU","ROST","TJX","HD","LOW",
    "FDX","UPS","DELL","HPE","ZM"]))

_UNI_PATH = os.path.expanduser("~/.michael_swing_universe.json")

def _load_universe():
    """Load universe: default + user additions - user removals."""
    added, removed = [], []
    try:
        if os.path.exists(_UNI_PATH):
            d = json.load(open(_UNI_PATH))
            added = d.get("added", [])
            removed = d.get("removed", [])
    except Exception: pass
    return sorted(set(s for s in _DEFAULT + added if s not in removed))

def _save_universe(added, removed):
    try: json.dump({"added": sorted(added), "removed": sorted(removed)}, open(_UNI_PATH, "w"), indent=2)
    except Exception: pass

UNIVERSE = _load_universe()

# Condition names — defined ONCE
CN = {"rsi_recovery":"RSI recovery","rsi_mid":"RSI mid","macd_cross":"MACD cross",
      "macd_improving":"MACD improving","ema_cross":"EMA 9/21 cross","at_lower_bb":"Lower BB",
      "near_lower_bb":"Near lower BB","stoch_oversold":"StochRSI oversold","stoch_low":"StochRSI low",
      "vol_surge":"Volume surge","vol_above_avg":"Vol above avg","above_vwap":"Above VWAP",
      "obv_rising":"OBV rising","obv_divergence":"OBV bullish divergence (accumulation)",
      "ema50_bounce":"EMA50 bounce","roc5_strong":"ROC5 strong",
      "roc5_positive":"ROC5 positive","willr_oversold":"Williams %R","ema_aligned":"EMA aligned",
      "rsi_divergence":"RSI divergence","fib_382":"Fib 38.2%","fib_50":"Fib 50%",
      "cmf_negative_filter":"CMF outflow (no divergence)","cmf_positive":"CMF inflow confirms"}
PRIMARY = {"macd_cross","ema_cross","rsi_recovery","at_lower_bb","rsi_divergence"}
STRONG_PRIMARY = {"macd_cross","ema_cross","rsi_recovery"}  # primaries worth ≥24 pts; sole qualifier for BUY gate

# Raw TA keys persisted alongside every 75+ signal for future backtesting
_TA_PERSIST_KEYS = {"RSI","RSI3","MACD_hist","EMA9","EMA21","EMA50","EMA200",
                    "BB_pct","StochRSI","VolRatio","WillR","ROC5","ROC20",
                    "CMF","VWAP","OBV_slope","RSI_div","OBV_div_bull",
                    "MACD_hist_prev","EMA9_prev","EMA21_prev"}

# Minimum checked outcomes before flagging user to backtest momentum effectiveness
_MOMENTUM_BACKTEST_GATE = 300

def _conf(n):
    if n>=150: return "high confidence"
    if n>=50: return "moderate confidence"
    if n>=20: return "low confidence"
    return f"very low — {n} signals, do not act yet"

# ═══════════════════════════════════════════════════════════════
# DATA
# ═══════════════════════════════════════════════════════════════

def fetch_ohlcv(sym, period="1y"):
    for attempt in range(2):
        try:
            df = (yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True) if attempt==0
                  else yf.download(sym, period=period, interval="1d", auto_adjust=True, progress=False, multi_level_index=False))
            if df is not None and len(df)>=30 and all(c in df.columns for c in ("Open","High","Low","Close","Volume")):
                out = df[["Open","High","Low","Close","Volume"]].dropna()
                if out.index.tz: out.index = out.index.tz_convert("America/New_York")
                return out
        except Exception: continue
    return None

def fetch_quote(sym):
    r = {"price":None,"prev_close":None,"change_pct":0.0,"extended":False}
    try:
        fi = yf.Ticker(sym).fast_info
        p, pc = getattr(fi,"last_price",None), getattr(fi,"previous_close",None)
        if p and pc:
            r.update(price=p, prev_close=pc, change_pct=(p-pc)/pc*100)
            r["extended"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).hour not in range(10,16)
    except Exception: pass
    return r

# ═══════════════════════════════════════════════════════════════
# INDICATORS — 21 TA indicators computed in-place
# ═══════════════════════════════════════════════════════════════

def add_indicators(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    for s in (9,21,50,200): df[f"EMA{s}"] = c.ewm(span=s, adjust=False).mean()
    # RSI — multiple periods (research: shorter works better on daily stocks)
    for period in (2, 3, 5, 14):
        d = c.diff()
        g = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        lo = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        col = "RSI" if period == 14 else f"RSI{period}"
        df[col] = 100 - 100/(1+g/lo.replace(0,np.nan))
    df["MACD"] = c.ewm(span=12,adjust=False).mean() - c.ewm(span=26,adjust=False).mean()
    df["MACD_sig"] = df["MACD"].ewm(span=9,adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_sig"]
    sma, std = c.rolling(20).mean(), c.rolling(20).std()
    df["BB_upper"], df["BB_lower"] = sma+2*std, sma-2*std
    df["BB_pct"] = (c-df["BB_lower"])/(df["BB_upper"]-df["BB_lower"]).replace(0,np.nan)*100
    tr = pd.concat([h-l,(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
    df["ATR"] = tr.ewm(span=14,adjust=False).mean()
    rm, rx = df["RSI"].rolling(14).min(), df["RSI"].rolling(14).max()
    df["StochRSI"] = (df["RSI"]-rm)/(rx-rm).replace(0,np.nan)*100
    df["OBV"] = (np.sign(c.diff())*v).fillna(0).cumsum()
    df["OBV_slope"] = df["OBV"].diff(5)
    # CMF(10) — Chaikin Money Flow
    mfm = ((c-l)-(h-c))/(h-l).replace(0,np.nan); mfm = mfm.fillna(0)
    df["CMF"] = (mfm*v).rolling(10).sum()/v.rolling(10).sum().replace(0,np.nan)
    # OBV bullish divergence — price near 20-bar low but OBV is not (accumulation signal)
    price_low20 = c.rolling(20).min(); obv_low20 = df["OBV"].rolling(20).min()
    df["OBV_div_bull"] = (c <= price_low20*1.02) & (df["OBV"] > obv_low20*1.10)
    tp = (h+l+c)/3
    df["VWAP"] = (tp*v).rolling(20).sum()/v.rolling(20).sum().replace(0,np.nan)
    hh, ll = h.rolling(14).max(), l.rolling(14).min()
    df["WillR"] = (hh-c)/(hh-ll).replace(0,np.nan)*-100
    df["VolRatio"] = v/v.rolling(20).mean().replace(0,np.nan)
    df["ROC5"], df["ROC20"] = c.pct_change(5)*100, c.pct_change(20)*100
    # RSI divergence — price lower low but RSI(14) higher low over last 14 bars
    df["RSI_div"] = False
    if len(df) >= 14:
        for i in range(14, len(df)):
            window = df.iloc[i-14:i+1]
            if (window["Close"].iloc[-1] < window["Close"].min() * 1.02 and
                window["RSI"].iloc[-1] > window["RSI"].min() + 5 and
                window["Close"].iloc[-1] <= window["Close"].iloc[0] and
                window["RSI"].iloc[-1] > window["RSI"].iloc[0]):
                df.iloc[i, df.columns.get_loc("RSI_div")] = True
    # Fibonacci — find swing high/low in last 60 bars, calc retracement levels
    df["Fib_382"] = np.nan; df["Fib_50"] = np.nan; df["Fib_near_382"] = False; df["Fib_near_50"] = False
    if len(df) >= 20:
        lookback = min(60, len(df))
        window = df.iloc[-lookback:]
        swing_high = window["High"].max()
        swing_low = window["Low"].min()
        diff = swing_high - swing_low
        if diff > 0:
            fib_382 = swing_high - diff * 0.382
            fib_50 = swing_high - diff * 0.5
            df["Fib_382"] = fib_382; df["Fib_50"] = fib_50
            last_price = c.iloc[-1]
            df.iloc[-1, df.columns.get_loc("Fib_near_382")] = abs(last_price - fib_382) / last_price < 0.02
            df.iloc[-1, df.columns.get_loc("Fib_near_50")] = abs(last_price - fib_50) / last_price < 0.02
    # Rolling swing high/low for TA exit reference levels
    df["swing_low_20"]  = l.rolling(20).min()
    df["swing_high_20"] = h.rolling(20).max()
    return df

# ═══════════════════════════════════════════════════════════════
# SCORER — generator-based, no if/elif chains
# ═══════════════════════════════════════════════════════════════

def _buy_rules(r, p):
    rsi, rsi3 = r.get("RSI",50), r.get("RSI3",50)
    mh, mhp = r.get("MACD_hist",0), p.get("MACD_hist",0)
    bb, st, vr = r.get("BB_pct",50), r.get("StochRSI",50), r.get("VolRatio",1)
    e9, e21, e50 = r.get("EMA9",0), r.get("EMA21",0), r.get("EMA50",0)
    pe9, pe21 = p.get("EMA9",0), p.get("EMA21",0)
    roc5, price = r.get("ROC5",0), r["Close"]
    # RSI(3) for oversold — research: short lookback outperforms RSI(14) on daily stocks
    if rsi3<=20:                 yield "rsi_recovery",24
    elif rsi<=45 and rsi>=30:    yield "rsi_mid",12
    if mh>0 and mhp<=0:         yield "macd_cross",28
    elif mh>mhp and mhp<=0:     yield "macd_improving",14
    if e9>e21 and pe9<=pe21:     yield "ema_cross",26
    if bb<20:                    yield "at_lower_bb",22
    elif bb<35:                  yield "near_lower_bb",12
    if st<25:                    yield "stoch_oversold",18
    elif st<45:                  yield "stoch_low",8
    if vr>1.8:                   yield "vol_surge",12
    elif vr>1.3:                 yield "vol_above_avg",7
    if price>r.get("VWAP",price): yield "above_vwap",6
    if r.get("OBV_div_bull",False):   yield "obv_divergence",12   # accumulation despite price weakness
    elif r.get("OBV_slope",0)>0:      yield "obv_rising",3        # weaker: OBV rising but no divergence
    if e50>0 and abs(price-e50)/e50<.015 and price>=e50: yield "ema50_bounce",14
    if roc5>3:                   yield "roc5_strong",10
    elif roc5>0:                 yield "roc5_positive",7
    if r.get("WillR",-50)<-80:  yield "willr_oversold",8
    if e9>e21>e50>0:             yield "ema_aligned",5
    if r.get("ROC20",0)<-5:     yield "roc20_penalty",-8
    # RSI divergence — price lower low, RSI higher low (strong reversal signal)
    if r.get("RSI_div",False):   yield "rsi_divergence",22
    # CMF filter — penalty blocks oversold-but-distributing entries; small bonus when flow confirms
    if r.get("CMF",0)>0.10:      yield "cmf_positive",4
    elif r.get("CMF",0)<-0.10 and not r.get("RSI_div",False): yield "cmf_negative_filter",-8
    # Fibonacci retracement — price near key fib levels
    if r.get("Fib_near_382",False): yield "fib_382",10
    elif r.get("Fib_near_50",False): yield "fib_50",6

def buy_score(r, p):
    conds = dict(_buy_rules(r, p))
    return min(max(sum(conds.values()),0),100), len(conds), set(conds)

def sell_score(r, p):
    s = n = 0
    def a(pts): nonlocal s,n; s+=pts; n+=1
    rsi = r.get("RSI",50)
    if rsi>75: a(22)
    elif rsi>65: a(12)
    mh, mhp = r.get("MACD_hist",0), p.get("MACD_hist",0)
    if mh<0 and mhp>=0: a(26)
    elif mh<mhp and mh>0: a(14)
    if r.get("BB_pct",50)>85: a(20)
    elif r.get("BB_pct",50)>70: a(10)
    st = r.get("StochRSI",50)
    if st>80: a(16)
    elif st>65: a(8)
    if r.get("WillR",-50)>-20: a(10)
    if r.get("EMA9",1)<r.get("EMA21",0): a(8)
    return max(s,0),n

def passes_gates(sc, nc, cn, atr, price):
    # Gate 5: at least one strong primary (≥24 pts) must fire; Lower BB / rsi_divergence alone → WATCH
    return (sc>=CFG.min_buy and nc>=CFG.min_conds and bool(cn&PRIMARY)
            and bool(cn&STRONG_PRIMARY)
            and (CFG.stop_m*atr/price*100>=CFG.min_swing if price>0 else False))

def score_and_assign(candidates, regime_mult, dm):
    """Pure scoring pipeline. 8 stages, strict order.
    Input:  candidates with buy_score (regime-adjusted, no arb bonus), bt_adj, arb dict.
    Output: (candidates_with_signals, breadth_mult)
    """
    # Stages 2-4: cap, bonuses, regime
    for i, c in enumerate(candidates):
        base = min(c["buy_score"], 75)
        c["buy_score"] = base                                              # stage 2: base cap
        bt   = min(max(c.get("bt_adj", 0), -20), 10)
        arb  = min(max(c.get("arb", {}).get("bonus", 0), 0), 10)
        bonus = min(bt + arb, 25)
        pre_regime = min(c["buy_score"] + max(bonus, bt), 100)
        c["final_score"] = pre_regime                                      # stage 3: bonuses
        c["final_score"] = int(c["final_score"] * regime_mult)             # stage 4: regime
        c["buy_score"]   = int(c["buy_score"]   * regime_mult)

    # Stages 5-6: gates + signal assignment
    for c in candidates:
        b = c["final_score"]; s = c["sell_score"]
        ia = c.get("arb", {}).get("is_arb", False)
        cn = c.get("conditions", set())
        has_strong = bool(cn & STRONG_PRIMARY) if isinstance(cn, set) else False
        supp = dm.is_combo_suppressed(cn) if isinstance(cn, set) and cn else False
        flag = dm.is_combo_flagged(cn)    if isinstance(cn, set) and cn else False
        c["suppressed"] = supp; c["flagged"] = flag
        gates = passes_gates(b, c["n_conditions"], cn, c["atr"], c["price"])
        if supp:                         c["signal"] = "⊘ SUPPRESSED"
        elif gates and b >= CFG.fire:    c["signal"] = "🔥 ACT NOW"
        elif gates and ia and has_strong:c["signal"] = "⚡ ARB BUY"
        elif gates and has_strong:       c["signal"] = "▲ BUY"
        elif s >= CFG.min_sell:          c["signal"] = "▼ SELL"
        else:                            c["signal"] = "◌ WATCH"
        if flag and "WATCH" not in c["signal"]: c["signal"] += " ⭐"

    # Stage 7: breadth penalty (count actual BUY signals only)
    buy_count = sum(1 for c in candidates if any(k in c["signal"] for k in ("BUY","ACT NOW","ARB")))
    bp = 0.75 if buy_count > 10 else 0.88 if buy_count > 6 else 1.0

    if bp < 1.0:
        for c in candidates:
            c["final_score"] = int(c["final_score"] * bp)
            c["buy_score"]   = int(c["buy_score"]   * bp)
        # Stage 8: re-gate after breadth scaling
        for c in candidates:
            if c.get("signal","").startswith("⊘"): continue
            b = c["final_score"]; s = c["sell_score"]
            ia = c.get("arb", {}).get("is_arb", False)
            cn = c.get("conditions", set())
            has_strong = bool(cn & STRONG_PRIMARY) if isinstance(cn, set) else False
            flag = "⭐" in c.get("signal", "")
            gates = passes_gates(b, c["n_conditions"], cn, c["atr"], c["price"])
            if gates and b >= CFG.fire:    c["signal"] = "🔥 ACT NOW"
            elif gates and ia and has_strong:c["signal"] = "⚡ ARB BUY"
            elif gates and has_strong:     c["signal"] = "▲ BUY"
            elif s >= CFG.min_sell:        c["signal"] = "▼ SELL"
            else:                          c["signal"] = "◌ WATCH"
            if flag and "WATCH" not in c["signal"]: c["signal"] += " ⭐"

    return candidates, bp, buy_count

# ═══════════════════════════════════════════════════════════════
# BACKTEST HELPERS  (used by nightly auto-backtest + Analytics tab)
# ═══════════════════════════════════════════════════════════════

_OHLCV_CACHE_PATH  = os.path.expanduser("~/.michael_swing_ohlcv_cache.json")
_BT_RESULTS_PATH   = os.path.expanduser("~/.michael_swing_trader/backtest_results.json")
_BT_LOOKBACK       = 60
_BT_TIME_STOP      = 21

class _BtDM:
    """Backtest stub — no suppression, no flagging."""
    def is_combo_suppressed(self, _): return False
    def is_combo_flagged(self, _):    return False

_BT_DM = _BtDM()

def _bt_load_ohlcv():
    """Load OHLCV cache. Returns (cache_date_str, {sym: raw_df}) or (None, {})."""
    try:
        if os.path.exists(_OHLCV_CACHE_PATH):
            raw = json.load(open(_OHLCV_CACHE_PATH))
            out = {}
            for sym, rows in raw.get("stocks", {}).items():
                if len(rows) < _BT_LOOKBACK + 20:
                    continue
                df = pd.DataFrame(rows, columns=["date","Open","High","Low","Close","Volume"])
                df["date"] = pd.to_datetime(df["date"])
                df.set_index("date", inplace=True)
                out[sym] = df
            return raw.get("date"), out
    except Exception:
        pass
    return None, {}

def _bt_save_ohlcv(stock_data):
    """Persist raw OHLCV for all stocks (indicators excluded — re-computed on load)."""
    try:
        stocks = {}
        for sym, df in stock_data.items():
            cols = [c for c in ("Open","High","Low","Close","Volume") if c in df.columns]
            rows = []
            for idx, row in df[cols].iterrows():
                rows.append([str(idx.date() if hasattr(idx,"date") else idx),
                              float(row["Open"]), float(row["High"]),
                              float(row["Low"]),  float(row["Close"]),
                              int(row["Volume"])])
            stocks[sym] = rows
        json.dump({"date": dt.date.today().isoformat(), "stocks": stocks},
                  open(_OHLCV_CACHE_PATH, "w"), indent=2)
    except Exception:
        pass

class _E0BacktestSink:
    """
    Phase E, Item E0 — exit-telemetry sink for walk-forward backtest trades.

    Stores trade records with exit-side fields not captured by the regular
    backtest stats path. Records are accumulated in memory and flushed to
    disk on close(). Schema documented in CHANGE_LIST_CONSOLIDATED.md
    under Item E0.

    NOT shared with the live Tracker — backtest trades and live trades are
    different epistemic objects (Scope-C decision per phase_E_drafts.md).
    """
    SINK_PATH = os.path.expanduser("~/.michael_swing_e0_backtest_sink.json")
    SCHEMA_VERSION = 1

    def __init__(self):
        self.records = []

    def emit(self, record):
        # Defensive: ensure schema version is on every record so analyses
        # can filter on e0_schema_version when the format eventually evolves.
        record["e0_schema_version"] = self.SCHEMA_VERSION
        self.records.append(record)

    def close(self):
        try:
            with open(self.SINK_PATH, "w") as f:
                json.dump(self.records, f, indent=2, default=str)
        except Exception as e:
            log.error(f"_E0BacktestSink.close: {e}")

class _E0LiveSink:
    """
    Phase E, Item E0-live — exit-telemetry sink for live closed trades.

    Mirrors _E0BacktestSink schema but for actual user-executed live trades
    (data_source="live"). Persists incrementally to disk because live trades
    close sporadically over weeks; in-memory buffer would lose data on
    app restart or crash.

    NOT shared with the backtest sink — backtest trades and live trades are
    different epistemic objects (Scope-C decision per phase_E_drafts.md).
    """
    SINK_PATH = os.path.expanduser("~/.michael_swing_e0_live_sink.json")
    SCHEMA_VERSION = 1

    def emit(self, record):
        record["e0_schema_version"] = self.SCHEMA_VERSION
        try:
            existing = []
            if os.path.exists(self.SINK_PATH):
                try:
                    with open(self.SINK_PATH, "r") as f:
                        existing = json.load(f)
                    if not isinstance(existing, list):
                        existing = []
                except (json.JSONDecodeError, OSError):
                    existing = []
            existing.append(record)
            tmp = self.SINK_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(existing, f, indent=2, default=str)
            os.replace(tmp, self.SINK_PATH)
        except Exception as e:
            log.error(f"_E0LiveSink.emit: {e}")

def _bt_fetch_all(universe, progress_cb=None):
    """Fetch 1y OHLCV for all symbols; returns {sym: raw_df} (no indicators)."""
    out = {}
    n = len(universe)
    for i, sym in enumerate(universe, 1):
        if progress_cb:
            progress_cb(i, n, sym)
        try:
            df = fetch_ohlcv(sym, period="1y")
            if df is not None and len(df) >= _BT_LOOKBACK + 20:
                out[sym] = df
        except Exception:
            pass
        time.sleep(0.05)
    return out

def _bt_walk(sym, df_with_indicators, exit_mode='single', telemetry_sink=None):
    """Walk-forward one stock. df must already have indicators. Returns trade list.

    exit_mode:
      'single' — default, current behavior. Exit 100% at TP1 (CFG.t1_m × ATR).
      'scaled' — Mode 3 scaled exit. Exit 50% at T1 (CFG.t1_m × ATR), then 50% at
                 T2 (CFG.t2_m × ATR) or time stop, whichever comes first.
                 Trade record gains leg1_*/leg2_* fields. Combined pnl_pct is
                 the average of leg1_pnl and leg2_pnl.

    Exit decisions are made on close-only (row['Close']). Same-day SL+TP straddles
    cannot be detected; this matches the existing single-mode behavior.
    """
    if exit_mode not in ('single', 'scaled'):
        raise ValueError(f"_bt_walk: unknown exit_mode {exit_mode!r}, expected 'single' or 'scaled'")
    trades, in_trade = [], False
    leg1_done = False; leg1_exit_px = 0.0; leg1_exit_reason = None; leg1_exit_day = 0
    trade_day = 0; entry_px = 0.0; entry_atr = 0.0; entry_conds: set = set()
    n = len(df_with_indicators)
    for day in range(_BT_LOOKBACK, n):
        row  = df_with_indicators.iloc[day].to_dict()
        prev = df_with_indicators.iloc[day - 1].to_dict()
        fc   = float(row["Close"])
        if in_trade:
            hold   = day - trade_day
            # E0 telemetry — per-bar excursion tracking
            if telemetry_sink is not None:
                bar_high = float(row.get("High", fc))
                bar_low  = float(row.get("Low",  fc))
                bar_mfe_pct = (bar_high - entry_px) / entry_px * 100
                bar_mae_pct = (bar_low  - entry_px) / entry_px * 100
                mfe_updated = bar_mfe_pct > _e0_state["mfe_pct"]
                mae_updated = bar_mae_pct < _e0_state["mae_pct"]
                if mfe_updated:
                    _e0_state["mfe_pct"] = bar_mfe_pct
                    _e0_state["mfe_bar"] = day
                if mae_updated:
                    _e0_state["mae_pct"] = bar_mae_pct
                    _e0_state["mae_bar"] = day
                if mfe_updated and mae_updated:
                    _e0_state["same_bar_extreme_count"] += 1
                    _e0_state["same_bar_extreme_bars"].append({
                        "bar": day,
                        "bar_mfe_pct": round(bar_mfe_pct, 4),
                        "bar_mae_pct": round(bar_mae_pct, 4),
                    })
            sl_px  = entry_px - CFG.stop_m * entry_atr
            tp1_px = entry_px + CFG.t1_m   * entry_atr
            tp2_px = entry_px + CFG.t2_m   * entry_atr
            if exit_mode == 'single':
                reason = None
                if   fc <= sl_px:           reason = "SL"
                elif fc >= tp1_px:          reason = "TP1"
                elif hold >= _BT_TIME_STOP: reason = "TIME"
                if reason:
                    pnl = (fc - entry_px) / entry_px * 100
                    trades.append({"symbol": sym, "pnl_pct": round(pnl,4),
                                    "hold_days": hold, "reason": reason,
                                    "conditions": list(entry_conds)})
                    if telemetry_sink is not None and _e0_state is not None:
                        exit_conds_now = dict(_buy_rules(row, prev))
                        condition_decay = {
                            cond: (cond in exit_conds_now)
                            for cond in _e0_state["entry_conds"]
                        }
                        exit_ta_snapshot = {
                            k: row.get(k) for k in _TA_PERSIST_KEYS if row.get(k) is not None
                        }
                        ss_exit, _ = sell_score(row, prev)
                        telemetry_sink.emit({
                            "symbol": sym,
                            "data_source": "backtest",
                            "entry_bar": _e0_state["entry_bar"],
                            "entry_px": _e0_state["entry_px"],
                            "entry_atr": _e0_state["entry_atr"],
                            "entry_conds": _e0_state["entry_conds"],
                            "entry_ta_snapshot": _e0_state["entry_row_indicators"],
                            "exit_bar": day,
                            "exit_price": fc,
                            "exit_reason": reason,
                            "exit_hold_bars": hold,
                            "exit_sell_score": int(ss_exit),
                            "exit_regime": None,
                            "exit_ta_snapshot": exit_ta_snapshot,
                            "pnl_pct": round(pnl, 4),
                            "mfe_pct": round(_e0_state["mfe_pct"], 4),
                            "mae_pct": round(_e0_state["mae_pct"], 4),
                            "mfe_bar": _e0_state["mfe_bar"],
                            "mae_bar": _e0_state["mae_bar"],
                            "same_bar_extreme_count": _e0_state["same_bar_extreme_count"],
                            "same_bar_extreme_bars": _e0_state["same_bar_extreme_bars"],
                            "condition_decay": condition_decay,
                        })
                        _e0_state = None
                    in_trade = False
                continue
            # exit_mode == 'scaled' — Mode 3 (50% T1, 50% T2 or time-stop)
            if not leg1_done:
                # Pre-T1 phase: full position still active
                exit_reason = None; exit_px = fc
                if   fc <= sl_px:           exit_reason = "SL";   exit_px = fc
                elif fc >= tp1_px:          exit_reason = "TP1";  exit_px = tp1_px
                elif hold >= _BT_TIME_STOP: exit_reason = "TIME"; exit_px = fc
                if exit_reason == "TP1":
                    # Leg 1 exits at T1, leg 2 continues
                    leg1_done = True
                    leg1_exit_px = tp1_px
                    leg1_exit_reason = "TP1"
                    leg1_exit_day = day
                    continue
                elif exit_reason in ("SL", "TIME"):
                    # Both legs exit together at this price
                    leg1_pnl = (exit_px - entry_px) / entry_px * 100
                    leg2_pnl = leg1_pnl
                    combined_pnl = (leg1_pnl + leg2_pnl) / 2  # = leg1_pnl, but explicit
                    trades.append({"symbol": sym,
                                   "pnl_pct": round(combined_pnl, 4),
                                   "hold_days": hold, "reason": "SCALED",
                                   "conditions": list(entry_conds),
                                   "leg1_exit": round(exit_px, 4), "leg1_pnl": round(leg1_pnl, 4),
                                   "leg1_reason": exit_reason, "leg1_hold_days": hold,
                                   "leg2_exit": round(exit_px, 4), "leg2_pnl": round(leg2_pnl, 4),
                                   "leg2_reason": exit_reason, "leg2_hold_days": hold})
                    in_trade = False
                continue
            else:
                # Post-T1 phase: leg 2 only
                exit_reason = None; exit_px = fc
                if   fc <= sl_px:           exit_reason = "SL";   exit_px = fc
                elif fc >= tp2_px:          exit_reason = "TP2";  exit_px = tp2_px
                elif hold >= _BT_TIME_STOP: exit_reason = "TIME"; exit_px = fc
                if exit_reason:
                    leg1_pnl = (leg1_exit_px - entry_px) / entry_px * 100
                    leg2_pnl = (exit_px - entry_px) / entry_px * 100
                    combined_pnl = (leg1_pnl + leg2_pnl) / 2
                    trades.append({"symbol": sym,
                                   "pnl_pct": round(combined_pnl, 4),
                                   "hold_days": hold, "reason": "SCALED",
                                   "conditions": list(entry_conds),
                                   "leg1_exit": round(leg1_exit_px, 4), "leg1_pnl": round(leg1_pnl, 4),
                                   "leg1_reason": leg1_exit_reason, "leg1_hold_days": leg1_exit_day - trade_day,
                                   "leg2_exit": round(exit_px, 4), "leg2_pnl": round(leg2_pnl, 4),
                                   "leg2_reason": exit_reason, "leg2_hold_days": hold})
                    in_trade = False
                continue
        atr = float(row.get("ATR") or 0); price = fc
        if atr <= 0 or price <= 0:
            continue
        bs, bn, bc = buy_score(row, prev)
        ss, _      = sell_score(row, prev)
        cand = {"symbol": sym, "price": price, "atr": atr,
                "atr_swing": CFG.stop_m * atr / price * 100,
                "buy_score": bs, "sell_score": ss, "n_conditions": bn,
                "conditions": bc, "bt_adj": 0,
                "arb": {"is_arb": False, "z": 0.0, "bonus": 0},
                "raw_buy": bs, "rsi": float(row.get("RSI") or 50)}
        results, *_ = score_and_assign([cand], 1.0, _BT_DM)
        sig = results[0].get("signal", "")
        if any(k in sig for k in ("BUY", "ACT NOW", "ARB")):
            in_trade = True; trade_day = day
            entry_px = price; entry_atr = atr; entry_conds = bc
            # Reset scaled-mode leg state at every entry — structural invariant
            leg1_done = False; leg1_exit_px = 0.0; leg1_exit_reason = None; leg1_exit_day = 0
            if telemetry_sink is not None:
                _e0_state = {
                    "entry_bar": day,
                    "entry_px": price,
                    "entry_atr": atr,
                    "entry_conds": list(bc),
                    "entry_row_indicators": {k: row.get(k) for k in _TA_PERSIST_KEYS if row.get(k) is not None},
                    "mfe_pct": 0.0,
                    "mae_pct": 0.0,
                    "mfe_bar": None,
                    "mae_bar": None,
                    "same_bar_extreme_count": 0,
                    "same_bar_extreme_bars": [],
                }
            else:
                _e0_state = None
    if in_trade and trade_day < n - 1:
        last = float(df_with_indicators.iloc[-1]["Close"])
        pnl  = (last - entry_px) / entry_px * 100
        if exit_mode == 'single':
            trades.append({"symbol": sym, "pnl_pct": round(pnl,4),
                            "hold_days": n - 1 - trade_day, "reason": "EOD",
                            "conditions": list(entry_conds)})
            if telemetry_sink is not None and _e0_state is not None:
                telemetry_sink.emit({
                    "symbol": sym,
                    "data_source": "backtest",
                    "entry_bar": _e0_state["entry_bar"],
                    "entry_px": _e0_state["entry_px"],
                    "entry_atr": _e0_state["entry_atr"],
                    "entry_conds": _e0_state["entry_conds"],
                    "entry_ta_snapshot": _e0_state["entry_row_indicators"],
                    "exit_bar": n - 1,
                    "exit_price": last,
                    "exit_reason": "EOD",
                    "exit_hold_bars": n - 1 - trade_day,
                    "exit_sell_score": None,
                    "exit_regime": None,
                    "exit_ta_snapshot": None,
                    "pnl_pct": round(pnl, 4),
                    "mfe_pct": round(_e0_state["mfe_pct"], 4),
                    "mae_pct": round(_e0_state["mae_pct"], 4),
                    "mfe_bar": _e0_state["mfe_bar"],
                    "mae_bar": _e0_state["mae_bar"],
                    "same_bar_extreme_count": _e0_state["same_bar_extreme_count"],
                    "same_bar_extreme_bars": _e0_state["same_bar_extreme_bars"],
                    "condition_decay": None,
                })
                _e0_state = None
        else:
            # Scaled mode EOD
            if leg1_done:
                leg1_pnl = (leg1_exit_px - entry_px) / entry_px * 100
                leg2_pnl = pnl
                combined = (leg1_pnl + leg2_pnl) / 2
                trades.append({"symbol": sym, "pnl_pct": round(combined, 4),
                               "hold_days": n - 1 - trade_day, "reason": "SCALED",
                               "conditions": list(entry_conds),
                               "leg1_exit": round(leg1_exit_px, 4), "leg1_pnl": round(leg1_pnl, 4),
                               "leg1_reason": leg1_exit_reason, "leg1_hold_days": leg1_exit_day - trade_day,
                               "leg2_exit": round(last, 4), "leg2_pnl": round(leg2_pnl, 4),
                               "leg2_reason": "EOD", "leg2_hold_days": n - 1 - trade_day})
            else:
                trades.append({"symbol": sym, "pnl_pct": round(pnl, 4),
                               "hold_days": n - 1 - trade_day, "reason": "SCALED",
                               "conditions": list(entry_conds),
                               "leg1_exit": round(last, 4), "leg1_pnl": round(pnl, 4),
                               "leg1_reason": "EOD", "leg1_hold_days": n - 1 - trade_day,
                               "leg2_exit": round(last, 4), "leg2_pnl": round(pnl, 4),
                               "leg2_reason": "EOD", "leg2_hold_days": n - 1 - trade_day})
    return trades

def _bt_compute_stats(trades):
    if not trades:
        return {"signals":0,"win_rate":0.0,"profit_factor":0.0,
                "avg_return":0.0,"avg_hold_days":0.0}
    pcts   = [t["pnl_pct"] for t in trades]
    wins   = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p <= 0]
    gp     = sum(wins);  gl = abs(sum(losses)) or 1e-9
    hold   = float(np.mean([t["hold_days"] for t in trades]))
    return {
        "signals":      len(trades),
        "win_rate":     round(len(wins) / len(trades) * 100, 1),
        "profit_factor":round(gp / gl, 2),
        "avg_return":   round(float(np.mean(pcts)), 2),
        "avg_hold_days":round(hold, 1),
    }

def _bt_run_default(stock_data_raw):
    """Run walk-forward with current default CFG params. Returns stats dict."""
    all_trades = []
    for sym, df in stock_data_raw.items():
        try:
            df_ind = add_indicators(df.copy())
            all_trades.extend(_bt_walk(sym, df_ind))
        except Exception:
            pass
    return _bt_compute_stats(all_trades)

def _bt_run_e0_backfill(stock_data_raw):
    """
    Phase E, Item E0 — run walk-forward with telemetry enabled and write
    parallel sink to ~/.michael_swing_e0_backtest_sink.json. Does NOT
    modify or replace _bt_run_default; this is an opt-in path called
    only by an explicit user trigger.

    Returns the same stats dict as _bt_run_default (so it can be used
    interchangeably for the behavioral identity test in E0 acceptance
    criterion 4), AND emits the telemetry sink as a side effect.
    """
    sink = _E0BacktestSink()
    all_trades = []
    try:
        for sym, df in stock_data_raw.items():
            try:
                df_ind = add_indicators(df.copy())
                all_trades.extend(_bt_walk(sym, df_ind, exit_mode='single', telemetry_sink=sink))
            except Exception as e:
                log.error(f"_bt_run_e0_backfill: {sym}: {e}")
        return _bt_compute_stats(all_trades), len(sink.records)
    finally:
        sink.close()

def _bt_run_sweep(stock_data_raw, progress_cb=None):
    """
    Full parameter sweep (mirrors backtest_pipeline.SWEEP_PARAMS with corrected ranges).
    Returns sweep_results dict compatible with backtest_results.json schema.
    Saves results file; does NOT modify CFG permanently.
    """
    orig = {"min_buy": CFG.min_buy, "min_conds": CFG.min_conds, "min_swing": CFG.min_swing}

    SWEEP = {
        "min_buy_score": {
            "values": [60, 64, 68, 70, 72, 74, 75],
            "kwarg":  "min_buy",
            "fixed":  {"min_conds": 7, "min_swing": 4.0},
        },
        "min_conditions": {
            "values": [4, 5, 6, 7, 8],
            "kwarg":  "min_conds",
            "fixed":  {"min_buy": 70, "min_swing": 4.0},
        },
        "min_atr_swing_pct": {
            "values": [3.0, 4.0, 4.5, 5.0, 6.0],
            "kwarg":  "min_swing",
            "fixed":  {"min_buy": 70, "min_conds": 7},
        },
    }

    # Pre-compute indicators once per stock
    stock_data = {}
    for sym, df in stock_data_raw.items():
        try:
            stock_data[sym] = add_indicators(df.copy())
        except Exception:
            pass

    sweep_results = {}
    combo_idx = 0
    total_combos = sum(len(s["values"]) for s in SWEEP.values())
    default_trades = []

    try:
        for param_name, spec in SWEEP.items():
            rows = []
            for val in spec["values"]:
                kwargs = dict(spec["fixed"]); kwargs[spec["kwarg"]] = val
                # Apply CFG overrides
                for k, v in kwargs.items():
                    if k == "min_buy":   CFG.min_buy   = v
                    if k == "min_conds": CFG.min_conds = v
                    if k == "min_swing": CFG.min_swing = v
                combo_idx += 1
                if progress_cb:
                    progress_cb(combo_idx, total_combos,
                                f"{param_name}={val}")
                all_trades = []
                for sym, df in stock_data.items():
                    try:
                        all_trades.extend(_bt_walk(sym, df))
                    except Exception:
                        pass
                stats = _bt_compute_stats(all_trades)
                rows.append({"value": val, "stats": stats})
                # Capture default-param trades
                if (not default_trades
                        and kwargs.get("min_buy", 70)   == 70
                        and kwargs.get("min_conds", 7)  == 7
                        and kwargs.get("min_swing", 4.0)== 4.0):
                    default_trades = all_trades
            sweep_results[param_name] = rows
    finally:
        CFG.min_buy   = orig["min_buy"]
        CFG.min_conds = orig["min_conds"]
        CFG.min_swing = orig["min_swing"]

    # Find optimal (best PF, ≥10 signals)
    optimal = None
    for dim, rows in sweep_results.items():
        for r in rows:
            s = r["stats"]
            if s["signals"] < 10: continue
            if optimal is None or s["profit_factor"] > optimal["stats"]["profit_factor"]:
                optimal = {"dimension": dim, "value": r["value"], "stats": s}

    # Condition breakdown for default params
    from collections import defaultdict as _dd
    by_cond = _dd(lambda: {"w": 0, "t": 0})
    for t in default_trades:
        win = t["pnl_pct"] > 0
        for c in t.get("conditions", []):
            by_cond[c]["t"] += 1
            by_cond[c]["w"] += int(win)
    cond_bd = {c: {"wr": round(v["w"]/v["t"]*100,1), "n": v["t"]}
               for c, v in by_cond.items() if v["t"] >= 5}

    output = {
        "generated_at":   dt.datetime.now().isoformat(timespec="seconds"),
        "universe_size":  len(UNIVERSE),
        "stocks_fetched": len(stock_data),
        "config": {
            "lookback_bars":  _BT_LOOKBACK, "time_stop_days": _BT_TIME_STOP,
            "stop_m": CFG.stop_m, "t1_m": CFG.t1_m, "regime_mult": 1.0,
        },
        "sweep_results":  {k: [{"value": r["value"], "stats": r["stats"]} for r in v]
                           for k, v in sweep_results.items()},
        "optimal":        optimal,
        "condition_breakdown_default": cond_bd,
    }
    try:
        os.makedirs(os.path.dirname(_BT_RESULTS_PATH), exist_ok=True)
        with open(_BT_RESULTS_PATH, "w") as fh:
            json.dump(output, fh, indent=2, default=str)
    except Exception:
        pass

    default_stats = _bt_compute_stats(default_trades)
    default_stats["date"] = dt.date.today().isoformat()
    return default_stats, output

# ═══════════════════════════════════════════════════════════════
# HEALTH MONITOR
# ═══════════════════════════════════════════════════════════════

class HealthMonitor:
    """6-probe system health checker. Each probe returns (status, detail).
    Status: 'green' (OK), 'yellow' (degraded), 'red' (failed).
    Probes are pure functions of state — no network calls."""

    # Threshold constants (named, tunable)
    YF_YELLOW_AGE_S = 300       # 5 min
    YF_RED_AGE_S = 600          # 10 min
    TRACKER_YELLOW_BYTES = 5_000_000   # 5 MB
    BARCHART_YELLOW_AGE_S = 7200       # 2 hours
    BARCHART_RED_AGE_S = 28800         # 8 hours
    BARCHART_YELLOW_MIN_SYMS = 50
    LOG_TAIL_MAX_BYTES = 50_000        # only read last 50 KB
    LOG_YELLOW_ERRORS = 1
    LOG_RED_ERRORS = 5
    LOG_WINDOW_S = 300                  # last 5 min

    def __init__(self, app):
        self._app = app
        self.last_check = None
        self.results = {}   # probe_name -> (status, detail)

    def _is_market_hours(self):
        """ET market hours check, weekdays only. Returns True if now is 9:30am-4pm ET on a weekday."""
        et = dt.datetime.now(dt.timezone(dt.timedelta(hours=-4)))
        if et.weekday() >= 5: return False
        m = et.hour*60 + et.minute
        return 9*60+30 <= m < 16*60

    def _color_from_age(self, age_s, yellow_s, red_s):
        if age_s < yellow_s: return "green"
        if age_s < red_s: return "yellow"
        return "red"

    def probe_yfinance(self):
        """Passive probe — inspects last successful regime fetch timestamp.
        No network call. yfinance health is inferred from whether the regime
        detector has been getting data."""
        last_ok = getattr(self._app._regime, "last_ok_update", None)
        if last_ok is None:
            return ("yellow", "no successful regime fetch yet this session")
        age = (dt.datetime.now() - last_ok).total_seconds()
        c = self._color_from_age(age, self.YF_YELLOW_AGE_S, self.YF_RED_AGE_S)
        return (c, f"last regime fetch {int(age)}s ago")

    def probe_tracker_io(self):
        path = CFG.tracker_path
        if not os.path.exists(path):
            return ("yellow", f"file not yet created: {path}")
        try:
            sz = os.path.getsize(path)
            with open(path) as f: json.load(f)
        except Exception as e:
            return ("red", f"unreadable/unparseable: {e}")
        if sz >= self.TRACKER_YELLOW_BYTES:
            return ("yellow", f"file size {sz:,} bytes (≥5MB threshold)")
        return ("green", f"OK ({sz:,} bytes, parseable)")

    def probe_state_json(self):
        if not os.path.exists(STATE_FILE):
            return ("red", f"missing: {STATE_FILE}")
        try:
            with open(STATE_FILE) as f: s = json.load(f)
            for required_key in ("config", "active_trades", "last_scan_metadata"):
                if required_key not in s:
                    return ("red", f"missing required key: {required_key}")
        except Exception as e:
            return ("red", f"unreadable/unparseable: {e}")
        return ("green", "OK (all required keys present)")

    def probe_barchart_cache(self):
        path = _BC_LIVE_PATH
        if not os.path.exists(path):
            if self._is_market_hours():
                return ("red", "cache missing during market hours")
            return ("yellow", "cache missing (not in market hours)")
        try:
            mtime = os.path.getmtime(path)
            age = time.time() - mtime
            with open(path) as f: data = json.load(f)
            n_syms = len(data) if isinstance(data, dict) else 0
        except Exception as e:
            return ("red", f"unreadable/unparseable: {e}")
        if not self._is_market_hours():
            return ("green", f"OK ({n_syms} symbols, {int(age/60)}m old, out-of-hours)")
        # Market hours: check both age and symbol count
        if age >= self.BARCHART_RED_AGE_S:
            return ("red", f"cache {int(age/60)}m old in market hours")
        if age >= self.BARCHART_YELLOW_AGE_S or n_syms < self.BARCHART_YELLOW_MIN_SYMS:
            return ("yellow", f"{n_syms} syms, {int(age/60)}m old")
        return ("green", f"OK ({n_syms} syms, {int(age/60)}m old)")

    def probe_monitor_thread(self):
        try:
            mon_cfg = self._app._dm.get_monitor_config()
            enabled = mon_cfg.get("enabled", False)
        except Exception as e:
            return ("red", f"cannot read monitor config: {e}")
        if not enabled:
            return ("green", "monitor disabled (by config)")
        thr = getattr(self._app, "_monitor_thread", None)
        if thr is None:
            return ("red", "monitor enabled but thread never started")
        if not thr.is_alive():
            return ("red", "monitor enabled but thread is dead")
        return ("green", "monitor thread alive")

    def probe_log_errors(self):
        log_path = os.path.expanduser("~/.michael_swing_trader/app.log")
        if not os.path.exists(log_path):
            return ("yellow", "log file does not exist yet")
        try:
            sz = os.path.getsize(log_path)
            with open(log_path, "rb") as f:
                if sz > self.LOG_TAIL_MAX_BYTES:
                    f.seek(-self.LOG_TAIL_MAX_BYTES, 2)
                tail = f.read().decode("utf-8", errors="replace")
        except Exception as e:
            return ("red", f"cannot read log: {e}")
        cutoff = dt.datetime.now() - dt.timedelta(seconds=self.LOG_WINDOW_S)
        n_errors = 0
        for line in tail.splitlines():
            if " ERROR " not in line: continue
            try:
                ts_str = line.split(" ", 1)[0] + " " + line.split(" ", 2)[1]
                ts = dt.datetime.strptime(ts_str.split(",")[0], "%Y-%m-%d %H:%M:%S")
                if ts >= cutoff: n_errors += 1
            except Exception: continue
        if n_errors >= self.LOG_RED_ERRORS:
            return ("red", f"{n_errors} ERROR lines in last 5 min")
        if n_errors >= self.LOG_YELLOW_ERRORS:
            return ("yellow", f"{n_errors} ERROR lines in last 5 min")
        return ("green", "0 ERROR lines in last 5 min")

    def run_all(self):
        """Run all 6 probes, store results, return worst status."""
        probes = [
            ("yfinance", self.probe_yfinance),
            ("tracker", self.probe_tracker_io),
            ("state.json", self.probe_state_json),
            ("barchart", self.probe_barchart_cache),
            ("monitor", self.probe_monitor_thread),
            ("log_errors", self.probe_log_errors),
        ]
        self.results = {}
        for name, fn in probes:
            try:
                self.results[name] = fn()
            except Exception as e:
                self.results[name] = ("red", f"probe crashed: {e}")
        self.last_check = dt.datetime.now()
        # Worst-of: red > yellow > green
        statuses = [v[0] for v in self.results.values()]
        if "red" in statuses: return "red"
        if "yellow" in statuses: return "yellow"
        return "green"


# ═══════════════════════════════════════════════════════════════
# REGIME
# ═══════════════════════════════════════════════════════════════

class Regime:
    LABELS = {"bull":("🟢 BULL",P["green"]),"normal":("⚪ NORMAL",P["muted"]),
              "caution":("🟡 CAUTION","#cc8800"),"danger":("🔴 DANGER",P["red"])}
    def __init__(self):
        self.state="normal"; self.spy_above=None; self.spy_pct=0.0
        self.vix=0.0; self.vix_rising=False; self.last_update=None; self.last_ok_update=None
    def assess(self):
        try:
            sh=yf.Ticker("SPY").history(period="3mo",interval="1d",auto_adjust=True)
            if sh is not None and len(sh)>=50:
                c,ma=sh["Close"].iloc[-1],sh["Close"].rolling(50).mean().iloc[-1]
                self.spy_above=c>ma; self.spy_pct=(c-ma)/ma*100
                self.last_ok_update=dt.datetime.now()
        except Exception: pass
        try:
            vh=yf.Ticker("^VIX").history(period="1mo",interval="1d",auto_adjust=True)
            if vh is not None and len(vh)>=5:
                self.vix=vh["Close"].iloc[-1]; self.vix_rising=self.vix>vh["Close"].iloc[-5]*1.05
        except Exception: pass
        self.state=("danger" if self.spy_above is False and self.vix_rising else
                    "caution" if self.spy_above is False else
                    "bull" if self.spy_above and not self.vix_rising and self.vix<18 else "normal")
        self.last_update=dt.datetime.now()
    def mult(self, mode="normal"):
        return CFG.regime_mult.get(self.state,[1,1,1])[{"aggressive":0,"normal":1,"conservative":2}.get(mode,1)]
    def summary(self):
        l=self.LABELS.get(self.state,("⚪",))[0]
        return f"{l} | SPY {'above' if self.spy_above else 'below'} 50MA ({self.spy_pct:+.1f}%) | VIX {self.vix:.1f} {'↑' if self.vix_rising else '↓'}"

# ═══════════════════════════════════════════════════════════════
# ARB DETECTOR
# ═══════════════════════════════════════════════════════════════

class Arb:
    _spy = None
    @classmethod
    def fetch_spy(cls):
        try:
            sh=yf.Ticker("SPY").history(period="6mo",interval="1d",auto_adjust=True)
            if sh is not None and len(sh)>=60: cls._spy=sh["Close"].pct_change().dropna()
        except Exception: pass
    @classmethod
    def detect(cls, df):
        r={"is_arb":False,"z":0.0,"bonus":0}
        if cls._spy is None or len(cls._spy)<60: return r
        try:
            sr=df["Close"].pct_change().dropna()
            if len(sr)<60: return r
            sr.index=sr.index.tz_localize(None) if sr.index.tz else sr.index
            spy=cls._spy.copy()
            spy.index=spy.index.tz_localize(None) if spy.index.tz else spy.index
            cm=sr.index.intersection(spy.index)
            if len(cm)<60: return r
            s,y=sr.loc[cm],spy.loc[cm]
            beta=(s.rolling(60).cov(y)/y.rolling(60).var().replace(0,np.nan)).iloc[-1]
            if pd.isna(beta): return r
            resid=s.iloc[-1]-beta*y.iloc[-1]
            std=(s-beta*y).rolling(60).std().iloc[-1]
            if std>0:
                z=round(resid/std,2); r["z"]=z
                if z<-1.8: r["is_arb"]=True; r["bonus"]=30
        except Exception: pass
        return r

# ═══════════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════════

class Events:
    _cache={}
    @classmethod
    def check(cls, sym, fast=True):
        if sym in cls._cache and (fast or "earn_date" in cls._cache[sym]): return cls._cache[sym]
        r=cls._cache.get(sym) or {"status":"OK","earn_flag":"","penalty":0}
        if sym not in cls._cache:
            try:
                fi=yf.Ticker(sym).fast_info
                p,mc=getattr(fi,"last_price",None),getattr(fi,"market_cap",None)
                if not p or p<=0 or (mc and mc<5e8): r["status"]="NO_DATA"
            except Exception: r["status"]="NO_DATA"
        if not fast and r["status"]=="OK":
            try:
                t=yf.Ticker(sym); info=t.info or {}
                if any(k in (info.get("longName","") or "").lower() for k in ("acquisition","merger","pending")):
                    r["status"]="MERGER"
            except Exception: pass
            try:
                cal=t.calendar
                if cal and isinstance(cal,dict):
                    ed=cal.get("Earnings Date")
                    if isinstance(ed,list) and ed: ed=ed[0]
                    if isinstance(ed,(dt.date,dt.datetime)):
                        r["earn_date"]=ed if not isinstance(ed,dt.datetime) else ed.date()
                        d=((ed if not isinstance(ed,dt.datetime) else ed.date())-dt.date.today()).days
                        if -3<=d<=0: r["earn_flag"]="📋 REPORTED"
                        elif 0<d<=2: r["earn_flag"]=f"⚠️ EARN {ed.strftime('%b %d')}"; r["penalty"]=-30
                        elif 2<d<=5: r["earn_flag"]=f"📅 EARN {ed.strftime('%b %d')}"; r["penalty"]=-15
            except Exception: pass
        cls._cache[sym]=r; return r

# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

def backtest(df):
    trades, pos, cap = [], None, CFG.capital
    for i in range(1,len(df)):
        r, p = df.iloc[i], df.iloc[i-1]
        price, atr = r["Close"], r.get("ATR",0)
        if atr<=0: continue
        if pos:
            days=(r.name-pos["date"]).total_seconds()/86400
            exit_now = price<=pos["stop"] or price>=pos["t2"] or days>=CFG.time_stop or sell_score(r,p)[0]>=CFG.min_sell
            if exit_now:
                pnl=(price-pos["entry"])*pos["sh"]-CFG.comm; cap+=pnl
                trades.append({"entry_date":pos["date"],"entry_price":pos["entry"],
                    "exit_date":r.name,"exit_price":price,"shares":pos["sh"],"pnl":pnl,
                    "pnl_pct":(price-pos["entry"])/pos["entry"]*100,"days":days,
                    "conditions":pos["conds"],
                    "reason":"STOP" if price<=pos["stop"] else "TGT" if price>=pos["t2"] else "TIME" if days>=CFG.time_stop else "SIG"})
                pos=None
        if not pos:
            bs,bn,bc=buy_score(r,p)
            if passes_gates(bs,bn,bc,atr,price):
                sh=max(1,int(cap*CFG.risk/(CFG.stop_m*atr)))
                if sh*price+CFG.comm<=cap:
                    pos={"date":r.name,"entry":price,"sh":sh,"stop":price-CFG.stop_m*atr,
                         "t2":price+CFG.t2_m*atr,"conds":bc}; cap-=sh*price+CFG.comm
    if pos and len(df)>0:
        last=df.iloc[-1]; pnl=(last["Close"]-pos["entry"])*pos["sh"]-CFG.comm; cap+=pnl
        trades.append({"entry_date":pos["date"],"entry_price":pos["entry"],"exit_date":last.name,
            "exit_price":last["Close"],"shares":pos["sh"],"pnl":pnl,
            "pnl_pct":(last["Close"]-pos["entry"])/pos["entry"]*100,
            "days":(last.name-pos["date"]).total_seconds()/86400,"conditions":pos["conds"],"reason":"OPEN"})
    return {"trades":trades,"stats":_stats(trades,cap)}

def _stats(trades,end):
    if not trades: return dict.fromkeys(["total","wr","ret","avg","pf","sharpe","dd","avg_d","best","worst"],0)
    w=[t for t in trades if t["pnl"]>0]; l=[t for t in trades if t["pnl"]<=0]
    pnls=[t["pnl_pct"] for t in trades]
    gp=sum(t["pnl"] for t in w) if w else 0; gl=abs(sum(t["pnl"] for t in l)) or 1
    ad=np.mean([t["days"] for t in trades]); mn,sd=np.mean(pnls),np.std(pnls) or 1
    eq=[CFG.capital]; pk=CFG.capital; dd=0
    for t in trades: eq.append(eq[-1]+t["pnl"])
    for e in eq: pk=max(pk,e); dd=max(dd,(pk-e)/pk*100)
    return {"total":len(trades),"wr":len(w)/len(trades)*100,"ret":(end-CFG.capital)/CFG.capital*100,
            "avg":mn,"pf":gp/gl,"sharpe":mn/sd*math.sqrt(252/max(ad,1)),"dd":dd,"avg_d":ad,
            "best":max(pnls),"worst":min(pnls)}

def cond_accuracy(trades):
    cs=defaultdict(lambda:{"w":0,"t":0})
    for t in trades:
        win=t["pnl"]>0
        for c in t.get("conditions",set()): cs[c]["t"]+=1; cs[c]["w"]+=win
    out={}
    for c,s in cs.items():
        if s["t"]>=2: s["wr"]=s["w"]/s["t"]*100; out[c]=s
    return dict(sorted(out.items(),key=lambda x:-x[1]["wr"]))

# ═══════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════

class Cache:
    def __init__(self):
        self.data,self.date={},""
        try:
            if os.path.exists(CFG.cache_path):
                raw=json.load(open(CFG.cache_path)); self.data,self.date=raw.get("data",{}),raw.get("date","")
        except Exception: pass
    def fresh(self): return self.date==dt.date.today().isoformat()
    def get(self,sym): return self.data.get(sym)
    def put(self,sym,s): self.data[sym]=s
    def save(self):
        try: json.dump({"date":dt.date.today().isoformat(),"data":self.data},open(CFG.cache_path,"w"),indent=2,default=str)
        except Exception: pass
    def adj(self, sym):
        s=self.data.get(sym)
        if not s or s.get("total",0)<5: return 0
        a=0; wr,pf,sh,ret,ad=s.get("wr",50),s.get("pf",1),s.get("sharpe",0),s.get("ret",0),s.get("avg_d",14)
        if wr>=60: a+=10
        elif wr>=50: a+=5
        elif wr<40: a-=15
        if pf>=2: a+=8
        elif pf<1: a-=20
        if sh>=1.5: a+=8
        if ret>20: a+=10
        elif ret<0: a-=10
        if ad<=14: a+=5
        return min(max(a,-20),10)

# ═══════════════════════════════════════════════════════════════
# TRACKER
# ═══════════════════════════════════════════════════════════════

class Tracker:
    def __init__(self):
        self.signals=[]
        try:
            if os.path.exists(CFG.tracker_path): self.signals=json.load(open(CFG.tracker_path))
        except Exception: pass
    def _save(self):
        try: json.dump(self.signals,open(CFG.tracker_path,"w"),indent=2,default=str)
        except Exception: pass
    def log(self,sym,price,score,regime,vix,conds,swing,setup=None,indicators=None,bp=1.0,buy_count=0):
        sig={"date":dt.datetime.now().isoformat(),"symbol":sym,"price":price,
             "score":score,"regime":regime,"vix":vix,"atr_swing":round(swing,1),
             "conditions":sorted(conds),"outcomes":{}}
        if setup:
            sig["setup_type"]            = setup.get("type","").lower().replace(" ","_")
            sig["setup_arb"]             = "+ arb" in setup.get("type","").lower()
            sig["momentum_speed_pct_bar"]= round(setup.get("move_speed",0),3)
            sig["volume_ratio"]          = round(setup.get("vol_ratio",1),2)
            sig["candle_quality_pct"]    = int(setup.get("body_pct",0))
            sig["confidence_score"]      = setup.get("confidence_score",0)
            sig["confidence_checks"]     = {k:v[0] for k,v in setup.get("checks",{}).items()}
        if indicators:
            sig["ta_snapshot"] = {k: round(float(v),4) if isinstance(v,(int,float)) else v
                                  for k,v in indicators.items()
                                  if k in _TA_PERSIST_KEYS}
        sig["raw_score"]           = score
        sig["adjusted_score"]      = score  # placeholder; will diverge in Phase 4
        sig["breadth_multiplier"]  = bp
        sig["buy_count_at_scan"]   = buy_count
        self.signals.append(sig)
        self._save()
    def check_outcomes(self):
        updated,now=0,dt.datetime.now()
        for sig in self.signals:
            elapsed=(now-dt.datetime.fromisoformat(sig["date"])).days
            for label,days in [("7d",7),("14d",14),("21d",21)]:
                if sig.get("outcomes",{}).get(label,{}).get("checked") or elapsed<days: continue
                try:
                    p=getattr(yf.Ticker(sig["symbol"]).fast_info,"last_price",None)
                    if p:
                        sig.setdefault("outcomes",{})[label]={"price":p,
                            "change_pct":round((p-sig["price"])/sig["price"]*100,2),"checked":True}
                        updated+=1
                except Exception: continue
        if updated: self._save()
        return updated
    def _checked(self):
        return [{**s,"_chg":s["outcomes"]["7d"]["change_pct"]}
                for s in self.signals if s.get("outcomes",{}).get("7d",{}).get("checked")]
    def stats_by(self, fn):
        g=defaultdict(list)
        for c in self._checked(): g[fn(c)].append(c["_chg"])
        return {k:{"total":len(v),"wr":sum(1 for x in v if x>0)/len(v)*100,"avg":np.mean(v)} for k,v in g.items() if v}
    def detect_trends(self):
        ch=self._checked(); trends=[]; n=len(ch)
        if n<5: trends.append({"text":f"Need {5-n} more outcomes.","type":"neutral"}); return trends
        wins=[c for c in ch if c["_chg"]>0]; wr=len(wins)/n*100; avg=np.mean([c["_chg"] for c in ch])
        conf=_conf(n)
        trends.append({"text":f"Overall: {wr:.0f}% win ({avg:+.1f}%, {n} sig) [{conf}]",
                       "type":"positive" if wr>=60 else "negative" if wr<=40 else "neutral"})
        if n>=15:
            rw=sum(1 for c in ch[-10:] if c["_chg"]>0)/10*100
            ew=sum(1 for c in ch[:-10] if c["_chg"]>0)/len(ch[:-10])*100
            d=rw-ew
            if abs(d)>15: trends.append({"text":f"{'Improving' if d>0 else 'Declining'}: last 10={rw:.0f}% vs {ew:.0f}% [{conf}]",
                                         "type":"positive" if d>0 else "negative"})
        if n>=15:
            med=np.median([c.get("score",72) for c in ch])
            hi=[c["_chg"] for c in ch if c.get("score",72)>=med]
            lo=[c["_chg"] for c in ch if c.get("score",72)<med]
            if hi and lo and abs(np.mean(hi)-np.mean(lo))>1:
                trends.append({"text":f"Scores {'predictive' if np.mean(hi)>np.mean(lo) else 'NOT predictive'} [{conf}]",
                               "type":"positive" if np.mean(hi)>np.mean(lo) else "negative"})
        rs=self.stats_by(lambda c:c.get("regime","normal"))
        if len(rs)>=2:
            b=max(rs.items(),key=lambda x:x[1]["wr"]); w=min(rs.items(),key=lambda x:x[1]["wr"])
            if b[1]["wr"]-w[1]["wr"]>15 and w[1]["total"]>=3:
                trends.append({"text":f"Best: {Regime.LABELS.get(b[0],(b[0],))[0]} ({b[1]['wr']:.0f}%). "
                                      f"Worst: {Regime.LABELS.get(w[0],(w[0],))[0]} ({w[1]['wr']:.0f}%) [{conf}]","type":"neutral"})
        for label,fn,msg,t in [("Losers",lambda c:c["_chg"]<0,"may not work","negative"),
                                 ("Winners",lambda c:c["_chg"]>0,"larger size","positive")]:
            ctr=Counter(c["symbol"] for c in ch if fn(c))
            reps=[(s,n) for s,n in ctr.most_common(3) if n>=3]
            if reps: trends.append({"text":f"{label}: {', '.join(f'{s}({n}×)' for s,n in reps)} — {msg}","type":t})
        if n>=25:
            cp=defaultdict(lambda:{"w":0,"t":0})
            for c in ch:
                for cond in c.get("conditions",[]): cp[cond]["t"]+=1; cp[cond]["w"]+=(c["_chg"]>0)
            rated={c:s for c,s in cp.items() if s["t"]>=8}
            for c in rated: rated[c]["wr"]=rated[c]["w"]/rated[c]["t"]*100
            if rated:
                b=max(rated.items(),key=lambda x:x[1]["wr"]); w=min(rated.items(),key=lambda x:x[1]["wr"])
                if b[1]["wr"]-w[1]["wr"]>20:
                    trends.append({"text":f"Best: {CN.get(b[0],b[0])} ({b[1]['wr']:.0f}%). Worst: {CN.get(w[0],w[0])} ({w[1]['wr']:.0f}%). [{conf}]","type":"neutral"})
        return trends

# ═══════════════════════════════════════════════════════════════
# CHART — 5 panels
# ═══════════════════════════════════════════════════════════════

def render_chart(df, sym, sigs, fig, ca=None):
    fig.clear(); fig.patch.set_facecolor("white")
    gs=fig.add_gridspec(5,1,height_ratios=[3,1,.8,.8,.7],hspace=.08)
    ax=[fig.add_subplot(gs[0])]+[fig.add_subplot(gs[i],sharex=fig.axes[0]) for i in range(1,5)]
    ap,am,ar,ast,av=ax; pdf=df.tail(120).copy(); x=np.arange(len(pdf)); dates=pdf.index
    # Price
    ap.set_facecolor("white")
    for j in range(len(pdf)):
        o,cl,hi,lo=pdf["Open"].iloc[j],pdf["Close"].iloc[j],pdf["High"].iloc[j],pdf["Low"].iloc[j]
        clr=P["green"] if cl>=o else P["red"]
        ap.plot([j,j],[lo,hi],color=clr,lw=.8)
        ap.bar(j,abs(cl-o) or pdf["Close"].mean()*.001,bottom=min(o,cl),width=.6,color=clr)
    for ema,c,w in [("EMA9","#2196F3",1),("EMA21","#FF9800",1),("EMA50","#9C27B0",1.2),("EMA200","#607D8B",1.5)]:
        if ema in pdf: ap.plot(x,pdf[ema],color=c,lw=w,alpha=.7,label=ema)
    if "BB_upper" in pdf: ap.fill_between(x,pdf["BB_lower"],pdf["BB_upper"],alpha=.08,color="#90CAF9")
    for s in sigs:
        if s["date"] in dates:
            idx=dates.get_loc(s["date"])
            if isinstance(idx,slice): idx=idx.start
            if s["type"]=="BUY": ap.annotate("▲\nBUY",(idx,s["price"]),fontsize=9,fontweight="bold",color=P["green"],ha="center",va="top",xytext=(0,-18),textcoords="offset points")
            elif s["type"]=="ARB_BUY": ap.annotate("▲\nARB",(idx,s["price"]),fontsize=8,fontweight="bold",color=P["gold"],ha="center",va="top",xytext=(0,-18),textcoords="offset points")
            elif s["type"]=="SELL": ap.annotate("▼\nSELL",(idx,s["price"]),fontsize=9,fontweight="bold",color=P["red"],ha="center",va="bottom",xytext=(0,14),textcoords="offset points")
    ap.set_title(f"Michael Swing Trader™ — {sym}",fontsize=14,fontweight="bold",loc="left")
    ap.legend(fontsize=8,loc="upper left",framealpha=.7)
    if ca:
        top5=list(ca.items())[:5]
        if top5:
            lines=["Best indicators (backtest):"]+[f"  {CN.get(c,c)}: {s['wr']:.0f}% ({s['t']} trades)" for c,s in top5]
            ap.text(.99,.98,"\n".join(lines),transform=ap.transAxes,fontsize=7.5,fontfamily="monospace",
                   va="top",ha="right",bbox=dict(boxstyle="round,pad=.4",fc="white",ec="#ccc",alpha=.85))
    ap.grid(True,alpha=.15); ap.tick_params(labelbottom=False)
    # MACD
    am.set_facecolor("white")
    if "MACD" in pdf:
        am.plot(x,pdf["MACD"],color="#2196F3",lw=1); am.plot(x,pdf["MACD_sig"],color="#FF9800",lw=1)
        h=pdf["MACD_hist"].values; am.bar(x,h,color=[P["green"] if v>=0 else P["red"] for v in h],alpha=.6,width=.6)
    am.axhline(0,color="#aaa",lw=.5); am.set_ylabel("MACD",fontsize=9); am.grid(True,alpha=.15); am.tick_params(labelbottom=False)
    # RSI
    ar.set_facecolor("white")
    if "RSI" in pdf: ar.plot(x,pdf["RSI"],color="#9C27B0",lw=1.2); ar.axhline(70,color=P["red"],lw=.7,ls="--",alpha=.5); ar.axhline(30,color=P["green"],lw=.7,ls="--",alpha=.5)
    ar.set_ylabel("RSI",fontsize=9); ar.set_ylim(0,100); ar.grid(True,alpha=.15); ar.tick_params(labelbottom=False)
    # StochRSI + WillR
    ast.set_facecolor("white")
    if "StochRSI" in pdf: ast.plot(x,pdf["StochRSI"],color="#E91E63",lw=1.2,label="StochRSI"); ast.axhline(80,color=P["red"],lw=.6,ls="--",alpha=.4); ast.axhline(20,color=P["green"],lw=.6,ls="--",alpha=.4)
    if "WillR" in pdf: ast.plot(x,pdf["WillR"]+100,color="#00BCD4",lw=1,alpha=.7,label="Will%R")
    ast.set_ylabel("Stoch",fontsize=9); ast.set_ylim(0,100); ast.legend(fontsize=7,loc="upper left",framealpha=.7); ast.grid(True,alpha=.15); ast.tick_params(labelbottom=False)
    # Volume
    av.set_facecolor("white")
    av.bar(x,pdf["Volume"],color=[P["green"] if pdf["Close"].iloc[j]>=pdf["Open"].iloc[j] else P["red"] for j in range(len(pdf))],alpha=.5,width=.6)
    av.set_ylabel("Vol",fontsize=9); av.grid(True,alpha=.15)
    step=max(1,len(dates)//12)
    av.set_xticks(range(0,len(dates),step)); av.set_xticklabels([dates[i].strftime("%m/%d") for i in range(0,len(dates),step)],fontsize=8,rotation=45)
    fig.tight_layout()

# ═══════════════════════════════════════════════════════════════
# TA EXIT LEVELS — display helper (no new API calls, uses ind snapshot)
# ═══════════════════════════════════════════════════════════════

def _ta_exit_levels(price, atr, ind):
    """
    Compute TA-based support/resistance reference levels from an indicator snapshot.
    Returns dict with support[], resistance[], suggested_stop, suggested_target.
    Display-only — does not affect scoring or backtest.
    """
    if not price or not atr or price <= 0 or atr <= 0:
        return None
    atr_stop = round(price - CFG.stop_m * atr, 2)
    atr_t1   = round(price + CFG.t1_m  * atr, 2)

    # Support candidates: below current price
    support = []
    for key, label in [
        ("EMA50",        "EMA50"),
        ("Fib_382",      "Fib 38.2%"),
        ("Fib_50",       "Fib 50%"),
        ("BB_lower",     "Lower BB"),
        ("swing_low_20", "20-bar swing low"),
    ]:
        v = ind.get(key, 0)
        if v and v > 0 and v < price:
            support.append((round(v, 2), label))
    support.sort(key=lambda x: -x[0])   # nearest first (highest below price)

    # Resistance candidates: above current price
    resistance = []
    for key, label in [
        ("BB_upper",      "Upper BB"),
        ("EMA200",        "EMA200"),
        ("swing_high_20", "20-bar swing high"),
    ]:
        v = ind.get(key, 0)
        if v and v > price:
            resistance.append((round(v, 2), label))
    resistance.sort(key=lambda x: x[0])  # nearest first (lowest above price)

    # Suggested stop: nearest support ABOVE the ATR stop (gives TA context but not wider than ATR)
    sug_stop, sug_stop_lbl = atr_stop, f"{CFG.stop_m}×ATR"
    for val, lbl in support:
        if val > atr_stop:
            sug_stop, sug_stop_lbl = val, lbl
            break

    # Suggested target: nearest resistance above price
    sug_tgt, sug_tgt_lbl = atr_t1, f"{CFG.t1_m}×ATR"
    if resistance:
        sug_tgt, sug_tgt_lbl = resistance[0]

    return {
        "atr_stop":          atr_stop,
        "atr_t1":            atr_t1,
        "support":           support,
        "resistance":        resistance,
        "suggested_stop":    sug_stop,
        "suggested_stop_lbl": sug_stop_lbl,
        "suggested_target":  sug_tgt,
        "suggested_target_lbl": sug_tgt_lbl,
    }


# ═══════════════════════════════════════════════════════════════
# TRADE CARD
# ═══════════════════════════════════════════════════════════════

def trade_card(parent, price, atr, score, stype, sym="", quote=None, evt=None):
    for w in parent.winfo_children(): w.destroy()
    f=tk.Frame(parent,bg=P["card"],bd=1,relief="solid",padx=12,pady=10); f.pack(fill="x",pady=6)
    hr=tk.Frame(f,bg=P["card"]); hr.pack(fill="x")
    isbuy=stype in ("BUY","ARB_BUY")
    hc=P["green"] if isbuy else P["red"] if stype=="SELL" else P["muted"]
    ht=f"▲ {'ARB ' if stype=='ARB_BUY' else ''}BUY — {sym}" if isbuy else f"▼ SELL — {sym}" if stype=="SELL" else f"◌ WATCHING — {sym}"
    tk.Label(hr,text=ht,font=("Helvetica",16,"bold"),fg=hc,bg=P["card"]).pack(side="left")
    if evt and evt.get("earn_flag"):
        tk.Label(hr,text=evt["earn_flag"],font=("Helvetica",11,"bold"),fg=P["orange"],bg=P["card"]).pack(side="left",padx=(8,0))
    if evt and evt.get("earn_date"):
        import datetime as _dt
        _d=(evt["earn_date"]-_dt.date.today()).days
        if 5<=_d<=28:
            eb=tk.Label(hr,text="E",font=("Helvetica",11,"bold"),fg=P["gold"],bg=P["card"],cursor="hand2")
            eb.pack(side="left",padx=(6,0))
    def _do_refresh():
        q=fetch_quote(sym); trade_card(parent,q.get("price") or price,atr,score,stype,sym,q,evt=evt)
    tk.Button(hr,text="🔄",font=("Helvetica",10,"bold"),fg=P["accent"],bg=P["card"],bd=0,cursor="hand2",
             command=_do_refresh).pack(side="right")
    if quote and quote.get("extended") and quote.get("price"):
        pc=quote["change_pct"]; price=quote["price"]
        tk.Label(f,text=f"⚠️ EXTENDED: {'↑' if pc>=0 else '↓'} {abs(pc):.1f}%",font=("Helvetica",11,"bold"),
                fg=P["green"] if pc>=0 else P["red"],bg=P["card"]).pack(anchor="w")
    elif quote and quote.get("price"): price=quote["price"]
    if atr<=0: tk.Label(f,text="No data.",font=("Helvetica",12),fg=P["muted"],bg=P["card"]).pack(); return
    stop=price-CFG.stop_m*atr; t1=price+CFG.t1_m*atr; t2=price+CFG.t2_m*atr; risk=price-stop
    rr1=(t1-price)/risk if risk>0 else 0; rr2=(t2-price)/risk if risk>0 else 0
    tk.Label(f,text=f"As of {dt.datetime.now().strftime('%H:%M:%S')}",font=("Helvetica",9),fg=P["muted"],bg=P["card"]).pack(anchor="w",pady=(2,4))
    def row(lbl,val,clr=P["text"]):
        r=tk.Frame(f,bg=P["card"]); r.pack(fill="x",pady=1)
        tk.Label(r,text=lbl,font=("Helvetica",12),fg=P["muted"],bg=P["card"],width=24,anchor="w").pack(side="left")
        tk.Label(r,text=val,font=("Helvetica",12,"bold"),fg=clr,bg=P["card"]).pack(side="left")
    if isbuy:
        row("Entry:",f"${price:.2f}"); row("Max chase:",f"${price*1.005:.2f}",P["muted"])
        row("Stop:",f"${stop:.2f} ({(stop-price)/price*100:.1f}%) −${risk:.2f}/sh",P["red"])
        ttk.Separator(f).pack(fill="x",pady=4)
        row("Target 1:",f"${t1:.2f} (+{(t1-price)/price*100:.1f}%) R/R {rr1:.1f}:1",P["green"])
        row("Target 2:",f"${t2:.2f} (+{(t2-price)/price*100:.1f}%) R/R {rr2:.1f}:1",P["green"])
        ttk.Separator(f).pack(fill="x",pady=4)
        row("Hold:",f"7-{CFG.time_stop}d"); row("Hard stop:",(dt.date.today()+dt.timedelta(days=CFG.time_stop)).strftime("%b %d, %Y"),P["orange"])
        row("Score:",f"{score}/100",P["green"] if score>=85 else P["text"])
    elif stype=="SELL": row("Exit:",f"${price:.2f}",P["red"]); row("Floor:",f"${price*.99:.2f} (−1%)",P["muted"])
    else: row("Entry:",f"${price:.2f}",P["muted"]); row("Stop:",f"${stop:.2f}",P["muted"]); row("T1:",f"${t1:.2f}",P["muted"])

# ═══════════════════════════════════════════════════════════════
# SETUP PROFILE — classify and render a human-readable entry annotation
# ═══════════════════════════════════════════════════════════════

def classify_setup(df, idx, buy_conditions, arb_info):
    """
    Classify entry setup type at df.iloc[idx]. Pure computation — no UI, no API calls.
    Returns a dict consumed by _render_setup_profile().
    """
    def _f(val, default=0.0):
        try:
            v = float(val)
            import math
            return default if math.isnan(v) or math.isinf(v) else v
        except (TypeError, ValueError):
            return default

    row   = df.iloc[idx]
    row1  = df.iloc[max(0, idx - 1)]
    row3  = df.iloc[max(0, idx - 3)]

    rsi      = _f(row.get("RSI",      50), 50)
    bb_pct   = _f(row.get("BB_pct",   50), 50)
    stochrsi = _f(row.get("StochRSI", 50), 50)
    willr    = _f(row.get("WillR",   -50), -50)
    macd_h   = _f(row.get("MACD_hist",  0))
    macd_h1  = _f(row1.get("MACD_hist", 0))
    ema9     = _f(row.get("EMA9",  0))
    ema21    = _f(row.get("EMA21", 0))
    ema50    = _f(row.get("EMA50", 0))
    roc5     = _f(row.get("ROC5",  0))
    vol_r    = _f(row.get("VolRatio", 1), 1)
    close    = _f(row["Close"],              1)
    open_    = _f(row.get("Open", close),  close)
    high     = _f(row["High"],             close)
    low      = _f(row["Low"],              close)
    close3   = _f(row3["Close"],           close)
    rsi3_bar = _f(row3.get("RSI", rsi),    rsi)

    arb_is = (arb_info or {}).get("is_arb", False)
    arb_z  = _f((arb_info or {}).get("z",   0))

    # ── Setup type (first match) ──────────────────────────────────────
    if rsi < 30 and bb_pct < 20 and stochrsi < 20:
        stype = "Deeply Oversold"
        sdesc = "Nearly every indicator is firing because the stock is beaten down hard, not because of individual signal strength."
        sclr  = "#cc4400"
    elif (30 <= rsi <= 45 and (15 <= bb_pct <= 35 or stochrsi < 40)
          and ema50 > 0 and abs(close - ema50) / ema50 < 0.05):
        stype = "Oversold Bounce"
        sdesc = "Stock is oversold with some structure remaining — bending, not breaking."
        sclr  = "#cc8800"
    elif 45 <= rsi <= 60 and roc5 > 0 and vol_r > 1.3 and ema9 > ema21:
        stype = "Momentum Acceleration"
        sdesc = "Not oversold — catching a move that's already building speed."
        sclr  = "#0d7a3e"
    else:
        stype = "Technical Convergence"
        sdesc = "Multiple weak-to-moderate signals adding up rather than one strong theme."
        sclr  = "#4a5068"

    if arb_is:
        stype += " + ARB Dislocation"
        sdesc += f" Stock is disconnected from its expected move relative to SPY (Z-score: {arb_z:.1f})."

    # ── Label helpers ────────────────────────────────────────────────
    def _rsi_lbl(v):
        if v<30: return "extreme oversold"
        if v<40: return "oversold"
        if v<50: return "below midline"
        if v<60: return "neutral-bullish"
        if v<70: return "bullish"
        return "overbought"
    def _bb_lbl(v):
        if v<15:  return "pressed against lower band"
        if v<25:  return "near lower band"
        if v<50:  return "lower half"
        if v<75:  return "upper half"
        if v<85:  return "near upper band"
        return "pressed against upper band"
    def _st_lbl(v):
        if v<15: return "washed out"
        if v<30: return "oversold"
        if v<50: return "low"
        if v<70: return "neutral"
        return "overbought"
    def _wr_lbl(v):
        if v<-80: return "deep oversold"
        if v<-50: return "oversold"
        if v<-20: return "neutral"
        return "overbought"
    def _r5_lbl(v):
        if v<-5:  return "sharp decline"
        if v<-2:  return "falling"
        if v<0:   return "drifting down"
        if v<2:   return "turning positive"
        if v<5:   return "solid upward"
        return "strong rally"

    # ── MACD label ───────────────────────────────────────────────────
    macd_expanding = macd_h > macd_h1
    cross_fired = False
    for k in range(1, 4):
        ki  = max(0, idx - k);     kp = max(0, idx - k - 1)
        if _f(df.iloc[ki].get("MACD_hist", 0)) > 0 and _f(df.iloc[kp].get("MACD_hist", 0)) <= 0:
            cross_fired = True; break
    if cross_fired:                           macd_lbl = "bullish cross just fired"
    elif macd_h > 0 and macd_expanding:       macd_lbl = "bullish and expanding"
    elif macd_h > 0:                          macd_lbl = "bullish but losing steam"
    elif macd_h < 0 and macd_expanding:       macd_lbl = "bearish but improving"
    else:                                     macd_lbl = "bearish and falling"

    # ── Momentum ─────────────────────────────────────────────────────
    if vol_r < 0.8:   vol_lbl = "low, no conviction"
    elif vol_r < 1.3: vol_lbl = "average"
    elif vol_r < 1.8: vol_lbl = "above average"
    elif vol_r < 2.5: vol_lbl = "strong buying interest"
    else:             vol_lbl = "extreme volume spike"

    move_speed = (close - close3) / close3 * 100 / 3 if close3 > 0 else 0
    if move_speed < -0.5:   spd_lbl = "falling fast"
    elif move_speed < 0:    spd_lbl = "drifting down"
    elif move_speed < 0.3:  spd_lbl = "slow grind up"
    elif move_speed < 0.7:  spd_lbl = "steady recovery"
    else:                   spd_lbl = "fast recovery"

    rng      = high - low
    body_pct = abs(close - open_) / rng * 100 if rng > 0 else 0
    if body_pct < 30:   cndl_lbl = "mostly wicks (indecision)"
    elif body_pct < 60: cndl_lbl = "mixed (some conviction)"
    else:               cndl_lbl = "strong conviction, clean candles"

    rsi_diff = rsi - rsi3_bar
    rsi_rising = rsi_diff > 1
    if rsi_diff > 1:    rsi_dir = f"rising from {rsi3_bar:.0f} → {rsi:.0f} (turning up)"
    elif rsi_diff < -1: rsi_dir = f"falling from {rsi3_bar:.0f} → {rsi:.0f} (fading)"
    else:               rsi_dir = f"flat around {rsi:.0f}"

    # ── Confidence (5 checks) ────────────────────────────────────────
    conds_set = buy_conditions if isinstance(buy_conditions, set) else set()
    n_prim = len(conds_set & PRIMARY)

    c1 = n_prim >= 2
    c1d = f"{n_prim} of {len(PRIMARY)} primary conditions firing" if c1 else f"only {n_prim} primary condition(s)"

    c2 = vol_r > 1.3
    c2d = f"{vol_r:.1f}× average volume" if c2 else f"volume only {vol_r:.1f}× average"

    c3 = rsi_rising and macd_expanding
    if c3:                              c3d = "RSI and MACD both improving"
    elif rsi_rising:                    c3d = "mixed — RSI improving but MACD not"
    elif macd_expanding:                c3d = "mixed — MACD improving but RSI not"
    else:                               c3d = "neither RSI nor MACD improving"

    if ema50 > 0:
        e50_pct = (close - ema50) / ema50 * 100
        c4 = e50_pct >= -3
        c4d = f"price {e50_pct:+.1f}% from EMA50"
    else:
        c4 = False; c4d = "EMA50 not available"; e50_pct = 0

    c5 = roc5 > 0
    c5d = f"5-day momentum positive ({roc5:+.1f}%)" if c5 else f"5-day momentum negative ({roc5:+.1f}%)"

    checks = [c1, c2, c3, c4, c5]
    conf   = sum(checks)
    bar    = "".join("██" if ok else "░░" for ok in checks)

    return {
        "type": stype, "description": sdesc, "color": sclr,
        "rsi": rsi,      "rsi_lbl":  _rsi_lbl(rsi),
        "bb_pct": bb_pct,"bb_lbl":   _bb_lbl(bb_pct),
        "stochrsi": stochrsi, "stochrsi_lbl": _st_lbl(stochrsi),
        "willr": willr,  "willr_lbl": _wr_lbl(willr),
        "macd_lbl": macd_lbl,
        "roc5": roc5,    "roc5_lbl": _r5_lbl(roc5),
        "vol_ratio": vol_r, "vol_lbl": vol_lbl,
        "move_speed": move_speed, "speed_lbl": spd_lbl,
        "body_pct": body_pct, "candle_lbl": cndl_lbl,
        "rsi_dir_lbl": rsi_dir,
        "macd_hist_lbl": macd_lbl,
        "confidence_score": conf,
        "bar_visual": bar,
        "checks": {
            "multi_primary":      (c1, c1d),
            "volume_confirm":     (c2, c2d),
            "momentum_turning":   (c3, c3d),
            "structural_support": (c4, c4d),
            "trend_positive":     (c5, c5d),
        },
    }


def _render_setup_profile(parent, profile, bg="#ffffff"):
    """Render a classify_setup() dict into a tkinter parent frame."""
    if not profile:
        tk.Label(parent, text="No setup profile available.", font=("Helvetica",11),
                 fg=P["muted"], bg=bg).pack(pady=16)
        return
    FM  = ("Courier", 10)
    FMB = ("Courier", 10, "bold")
    FHB = ("Helvetica", 10, "bold")
    # Type + description
    tk.Label(parent, text=profile["type"], font=("Helvetica",13,"bold"),
             fg=profile["color"], bg=bg).pack(anchor="w", padx=8, pady=(8,0))
    tk.Label(parent, text=profile["description"], font=("Helvetica",9),
             fg=P["muted"], bg=bg, wraplength=460, justify="left").pack(anchor="w", padx=8, pady=(2,8))
    ttk.Separator(parent).pack(fill="x", padx=8)
    # CONDITIONS
    cf = tk.Frame(parent, bg=bg, padx=8); cf.pack(fill="x", pady=(6,0))
    tk.Label(cf, text="CONDITIONS", font=FHB, fg=P["text"], bg=bg).pack(anchor="w")
    for line in [
        f"  RSI: {profile['rsi']:.1f} — {profile['rsi_lbl']}",
        f"  BB%: {profile['bb_pct']:.1f} — {profile['bb_lbl']}",
        f"  StochRSI: {profile['stochrsi']:.1f} — {profile['stochrsi_lbl']}",
        f"  Williams %R: {profile['willr']:.0f} — {profile['willr_lbl']}",
        f"  MACD: {profile['macd_lbl']}",
        f"  ROC5: {profile['roc5']:+.1f}% — {profile['roc5_lbl']}",
    ]:
        tk.Label(cf, text=line, font=FM, fg=P["text"], bg=bg, anchor="w").pack(anchor="w")
    # MOMENTUM
    mf = tk.Frame(parent, bg=bg, padx=8); mf.pack(fill="x", pady=(6,0))
    tk.Label(mf, text="MOMENTUM", font=FHB, fg=P["text"], bg=bg).pack(anchor="w")
    for line in [
        f"  Volume: {profile['vol_ratio']:.1f}× avg — {profile['vol_lbl']}",
        f"  Move speed: {profile['move_speed']:+.1f}%/bar — {profile['speed_lbl']}",
        f"  Candle quality: {profile['body_pct']:.0f}% body — {profile['candle_lbl']}",
        f"  RSI direction: {profile['rsi_dir_lbl']}",
        f"  MACD histogram: {profile['macd_hist_lbl']}",
    ]:
        tk.Label(mf, text=line, font=FM, fg=P["text"], bg=bg, anchor="w").pack(anchor="w")
    # CONFIDENCE
    xf = tk.Frame(parent, bg=bg, padx=8); xf.pack(fill="x", pady=(6,8))
    sc = profile['confidence_score']
    tk.Label(xf, text=f"CONFIDENCE: {profile['bar_visual']} {sc}/5",
             font=FMB, fg=P["text"], bg=bg).pack(anchor="w")
    check_meta = [
        ("multi_primary",      "Multiple primary conditions"),
        ("volume_confirm",     "Volume confirming"),
        ("momentum_turning",   "Momentum turning up"),
        ("structural_support", "Structural support"),
        ("trend_positive",     "Short-term trend positive"),
    ]
    for key, label in check_meta:
        passed, detail = profile["checks"][key]
        icon = "✓" if passed else "✗"
        iclr = "#22aa22" if passed else "#cc4444"
        rw = tk.Frame(xf, bg=bg); rw.pack(fill="x")
        tk.Label(rw, text=f"  {icon}", font=FMB, fg=iclr, bg=bg, width=3, anchor="w").pack(side="left")
        tk.Label(rw, text=f"{label} — {detail}", font=FM, fg=P["text"], bg=bg, anchor="w").pack(side="left")


# ═══════════════════════════════════════════════════════════════
# BARCHART CONSENSUS MODULE
# ═══════════════════════════════════════════════════════════════

_BC_LIVE_PATH  = os.path.expanduser("~/.michael_swing_barchart_live.json")
_BC_SYNTH_PATH = os.path.expanduser("~/.michael_swing_barchart_synth.json")

def momentum_score(entry_price, current_price, bars_held, indicators):
    """
    Compute in-trade momentum score (0-4) using existing TA.
    Pure computation — no UI, no API calls.

    Checks:
      +1  move speed > 0.50% per bar
      +1  volume ratio > 1.5× (current vs 20-day avg)
      +1  candle body > 60% of range (conviction)
      +1  RSI > 60 and MACD histogram > 0 and expanding

    Returns dict with score, checks, and recommendation.
    """
    def _f(val, default=0.0):
        try:
            v = float(val)
            import math
            return default if math.isnan(v) or math.isinf(v) else v
        except (TypeError, ValueError):
            return default

    checks = {}
    # 1. Speed of move
    speed = ((current_price - entry_price) / entry_price * 100 / max(bars_held, 1)) if entry_price > 0 else 0
    c1 = speed > 0.50
    checks["move_speed"] = (c1, f"{speed:.2f}%/bar {'(strong)' if c1 else '(normal)'}")

    # 2. Volume confirmation
    vol_r = _f(indicators.get("VolRatio", 1), 1)
    c2 = vol_r > 1.5
    checks["volume"] = (c2, f"{vol_r:.1f}× avg {'(confirmed)' if c2 else '(weak)'}")

    # 3. Candle quality
    close = _f(indicators.get("Close", 0))
    open_ = _f(indicators.get("Open", close))
    high = _f(indicators.get("High", close))
    low = _f(indicators.get("Low", close))
    rng = high - low
    body = abs(close - open_)
    body_pct = (body / rng * 100) if rng > 0 else 0
    c3 = body_pct > 60
    checks["candle_quality"] = (c3, f"{body_pct:.0f}% body {'(conviction)' if c3 else '(indecisive)'}")

    # 4. Indicator confirmation
    rsi = _f(indicators.get("RSI", 50), 50)
    macd_h = _f(indicators.get("MACD_hist", 0))
    macd_h_prev = _f(indicators.get("MACD_hist_prev", 0))
    c4 = rsi > 60 and macd_h > 0 and macd_h > macd_h_prev
    checks["indicators"] = (c4, f"RSI {rsi:.0f}, MACD {'expanding' if macd_h > macd_h_prev else 'contracting'}")

    sc = sum(v[0] for v in checks.values())
    strong = sc >= 3

    return {
        "momentum_score": sc,
        "strong": strong,
        "checks": checks,
        "move_speed_pct_bar": round(speed, 3),
        "unrealized_pct": round((current_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0,
    }


class SyntheticConsensus:
    """Backtestable proxy for Barchart opinion from standard TA indicators."""
    @staticmethod
    def compute(closes, highs=None, lows=None, volumes=None):
        if closes is None or len(closes) < 200: return None
        price = float(closes.iloc[-1]); detail = {}
        for period in [10,20,50,100,200]:
            ma = closes.rolling(period).mean()
            if pd.isna(ma.iloc[-1]): continue
            mv = float(ma.iloc[-1]); name = f"MA{period}"
            detail[name] = "bull" if price>mv*1.005 else "bear" if price<mv*0.995 else "neutral"
        for period in [9,21,50]:
            ema = closes.ewm(span=period,adjust=False).mean()
            if pd.isna(ema.iloc[-1]): continue
            ev = float(ema.iloc[-1]); name = f"EMA{period}"
            detail[name] = "bull" if price>ev*1.005 else "bear" if price<ev*0.995 else "neutral"
        delta = closes.diff(); gain = delta.clip(lower=0).rolling(14).mean(); loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi_s = 100-(100/(1+gain/loss)); rv = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else None
        if rv is not None: detail["RSI14"] = "bull" if rv<30 else "bear" if rv>70 else "bear" if rv<50 else "bull"
        ema12=closes.ewm(span=12,adjust=False).mean(); ema26=closes.ewm(span=26,adjust=False).mean()
        macd=ema12-ema26; sig_ln=macd.ewm(span=9,adjust=False).mean()
        if not pd.isna(macd.iloc[-1]) and not pd.isna(sig_ln.iloc[-1]):
            detail["MACD"] = "bull" if float(macd.iloc[-1])>float(sig_ln.iloc[-1]) else "bear"
        if highs is not None and lows is not None and len(highs)>=14:
            low14=lows.rolling(14).min(); high14=highs.rolling(14).max(); denom=high14-low14
            stk=((closes-low14)/denom.replace(0,np.nan))*100; kv=float(stk.iloc[-1]) if not pd.isna(stk.iloc[-1]) else None
            if kv is not None: detail["Stoch"] = "bull" if kv<20 else "bear" if kv>80 else "bull" if kv>50 else "bear"
            willr=((high14-closes)/denom.replace(0,np.nan))*-100; wv=float(willr.iloc[-1]) if not pd.isna(willr.iloc[-1]) else None
            if wv is not None: detail["WillR"] = "bull" if wv<-80 else "bear" if wv>-20 else "bear" if wv<-50 else "bull"
        if highs is not None and lows is not None:
            tp=(highs+lows+closes)/3; sma_tp=tp.rolling(20).mean()
            mad=tp.rolling(20).apply(lambda x:np.abs(x-x.mean()).mean(),raw=True)
            cci=(tp-sma_tp)/(0.015*mad.replace(0,np.nan)); cv=float(cci.iloc[-1]) if not pd.isna(cci.iloc[-1]) else None
            if cv is not None: detail["CCI"] = "bull" if cv<-100 else "bear" if cv>100 else "bull" if cv>0 else "bear"
        if highs is not None and lows is not None and len(highs)>=28:
            pdm=highs.diff().clip(lower=0); mdm=(-lows.diff()).clip(lower=0)
            mask=pdm>mdm; pdm=pdm.where(mask,0); mdm=mdm.where(~mask,0)
            tr=pd.concat([highs-lows,(highs-closes.shift()).abs(),(lows-closes.shift()).abs()],axis=1).max(axis=1)
            atr14=tr.rolling(14).mean()
            pdi=100*(pdm.rolling(14).mean()/atr14.replace(0,np.nan)); mdi=100*(mdm.rolling(14).mean()/atr14.replace(0,np.nan))
            if not pd.isna(pdi.iloc[-1]) and not pd.isna(mdi.iloc[-1]):
                detail["ADX"] = "bull" if float(pdi.iloc[-1])>float(mdi.iloc[-1]) else "bear"
        bull=sum(1 for v in detail.values() if v=="bull"); bear=sum(1 for v in detail.values() if v=="bear")
        neutral=sum(1 for v in detail.values() if v=="neutral"); total=bull+bear+neutral
        if total==0: return None
        pct=round((bull/total)*100)
        signal="Buy" if pct>=67 else "Sell" if pct<=33 else "Hold"
        return {"pct":pct,"signal":signal,"bull":bull,"bear":bear,"neutral":neutral,"detail":detail}

    @staticmethod
    def format_display(result):
        if not result: return "N/A"
        return f"{result['pct']}% {result['signal']}"


class BarchartCollector:
    """Scrapes live Barchart opinions once per day, caches to JSON."""
    def __init__(self):
        self.data = {}
        try:
            if os.path.exists(_BC_LIVE_PATH): self.data=json.load(open(_BC_LIVE_PATH))
        except Exception: pass
    def _save(self):
        try: json.dump(self.data,open(_BC_LIVE_PATH,"w"),indent=2)
        except Exception: pass
    def already_scraped_today(self):
        today=dt.datetime.now().strftime("%Y-%m-%d"); return today in self.data and len(self.data[today])>0
    def get_today(self,symbol):
        today=dt.datetime.now().strftime("%Y-%m-%d"); return self.data.get(today,{}).get(symbol)
    def get_today_all(self):
        today=dt.datetime.now().strftime("%Y-%m-%d"); return self.data.get(today,{})
    def scrape_all(self,tickers,price_map=None,callback=None):
        today=dt.datetime.now().strftime("%Y-%m-%d")
        if today not in self.data: self.data[today]={}
        done=0
        for ticker in tickers:
            if ticker in self.data[today]: continue
            try:
                print(f"Barchart scraping {ticker} ({done+1}/{len(tickers)})…")
                result=self._scrape_one(ticker)
                if result:
                    result["price"]=price_map.get(ticker) if price_map else None
                    self.data[today][ticker]=result
                    print(f"  {ticker}: {result['pct']}% {result['signal']} {result.get('trend','')}")
                else:
                    print(f"  {ticker}: no data returned (page parse failed)")
                done+=1
                time.sleep(1.2)
            except Exception as e: print(f"  {ticker}: ERROR — {e}")
        self._save(); self._trim_old(400)
        print(f"Barchart scrape_all done: {done} fetched, {len(self.data[today])} total cached today")
        if callback: callback()
    def _scrape_one(self,ticker):
        headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
        url=f'https://www.barchart.com/stocks/quotes/{ticker}/opinion'
        r=requests.get(url,headers=headers,timeout=10); soup=BeautifulSoup(r.text,'html.parser')
        pct_tag=(soup.find('span',class_='opinion-percent buy') or soup.find('span',class_='opinion-percent sell') or soup.find('span',class_='opinion-percent hold'))
        sig_tag=(soup.find('span',class_='opinion-signal buy') or soup.find('span',class_='opinion-signal sell') or soup.find('span',class_='opinion-signal hold'))
        pct_str=pct_tag.get_text(strip=True) if pct_tag else None; sig_str=sig_tag.get_text(strip=True) if sig_tag else None
        if not pct_str or not sig_str: return None
        try: pct=int(pct_str.rstrip('%'))
        except (ValueError,AttributeError): return None
        yesterday=last_week=last_month=None
        snap=soup.find('h3',string='Snapshot Opinion')
        if snap:
            txt=snap.find_parent().get_text(strip=True)
            ym=re.search(r'Yesterday(\d+%\s+\w+?)Last',txt); wm=re.search(r'Last Week(\d+%\s+\w+?)Last',txt); mm=re.search(r'Last Month(\d+%\s+\w+?)Snapshot',txt)
            yesterday=ym.group(1).strip() if ym else None; last_week=wm.group(1).strip() if wm else None; last_month=mm.group(1).strip() if mm else None
        trend="→"
        if yesterday and last_week:
            try:
                y_pct=int(yesterday.split('%')[0]); w_pct=int(last_week.split('%')[0]); diff=y_pct-w_pct
                trend="↑" if diff>3 else "↓" if diff<-3 else "→"
            except (ValueError,IndexError): pass
        return {"pct":pct,"signal":sig_str,"trend":trend,"yesterday":yesterday or f"{pct}% {sig_str}","last_week":last_week,"last_month":last_month}
    def _trim_old(self,keep_days):
        dates=sorted(self.data.keys())
        if len(dates)>keep_days:
            for d in dates[:-keep_days]: del self.data[d]
            self._save()


class ConsensusCorrelation:
    """Tracks synthetic vs live Barchart opinion and price outcomes."""
    def __init__(self):
        self.records=[]
        try:
            if os.path.exists(_BC_SYNTH_PATH): self.records=json.load(open(_BC_SYNTH_PATH))
        except Exception: pass
    def _save(self):
        try: json.dump(self.records,open(_BC_SYNTH_PATH,"w"),indent=2,default=str)
        except Exception: pass
    def log(self,symbol,price,synth_pct,synth_signal,live_pct,live_signal,live_trend):
        today=dt.datetime.now().strftime("%Y-%m-%d")
        if any(r.get("symbol")==symbol and r.get("date")==today for r in self.records): return
        self.records.append({"date":today,"symbol":symbol,"price":price,"synth_pct":synth_pct,"synth_signal":synth_signal,"live_pct":live_pct,"live_signal":live_signal,"live_trend":live_trend,"outcomes":{}})
        self._save()
    def check_outcomes(self):
        updated=0; now=dt.datetime.now()
        for rec in self.records:
            elapsed=(now-dt.datetime.fromisoformat(rec["date"])).days
            for label,days in [("7d",7),("14d",14),("21d",21)]:
                if rec.get("outcomes",{}).get(label,{}).get("checked") or elapsed<days: continue
                try:
                    p=getattr(yf.Ticker(rec["symbol"]).fast_info,"last_price",None)
                    if p:
                        rec.setdefault("outcomes",{})[label]={"price":round(p,2),"change_pct":round((p-rec["price"])/rec["price"]*100,2),"checked":True}; updated+=1
                except Exception: continue
        if updated: self._save()
        return updated


# ═══════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════

class App:
    def __init__(self, root):
        self.root=root; root.title("🤖 Michael Swing Trader"); root.attributes('-zoomed', True); root.configure(bg=P["bg"])
        root.protocol("WM_DELETE_WINDOW",self._close)
        self._sym=tk.StringVar(value="PLTR"); self._df=self._bt=self._quote=None
        self._sigs=[]; self._fires=[]; self._scan_res=[]; self._meta={"time":None,"vix":20,"skip":0,"total":0}
        self._scanning=self._closing=False; self._auto_today=set(); self._oc_date=""
        self._monitor_alerted=set()  # (trade_id, alert_type) pairs already shown this session
        self._regime=Regime(); self._mode=tk.StringVar(value="normal"); self._vix_forced=False
        self._cache=Cache(); self._tracker=Tracker(); self._dm=DataManager(); self._fig=plt.figure(figsize=(14,10)); self._cc=None
        self._bc_collector=BarchartCollector(); self._bc_correlation=ConsensusCorrelation()
        self._temp_syms=set()  # one-scan-only symbols
        # Load user universe customizations
        self._user_added, self._user_removed = [], []
        try:
            if os.path.exists(_UNI_PATH):
                d=json.load(open(_UNI_PATH))
                self._user_added=d.get("added",[])
                self._user_removed=d.get("removed",[])
        except Exception: pass
        self._build(); self._tick()

    def _build(self):
        self._build_header()
        body=tk.Frame(self.root,bg=P["bg"]); body.pack(fill="both",expand=True,padx=8,pady=(0,8))
        self._build_left(body); self._build_right(body)

    def _build_header(self):
        h=tk.Frame(self.root,bg=P["panel"],height=64); h.pack(fill="x"); h.pack_propagate(False)
        i=tk.Frame(h,bg=P["panel"]); i.pack(fill="both",expand=True,padx=12)
        tk.Label(i,text="🤖 MICHAEL SWING TRADER",font=("Helvetica",18,"bold"),fg=P["text"],bg=P["panel"]).pack(side="left")
        self._rl=tk.Label(i,text="⚪ NORMAL",font=("Helvetica",12,"bold"),fg=P["muted"],bg=P["panel"],cursor="hand2")
        self._rl.pack(side="left",padx=(20,0)); self._rl.bind("<Button-1>",lambda e:self._regime_popup())
        self._vix_warn=tk.Label(i,text="",font=("Helvetica",11,"bold"),fg=P["red"],bg=P["panel"])
        self._vix_warn.pack(side="left",padx=(8,0))
        self._fb=tk.Label(i,text="🔥",font=("Helvetica",14),fg="#888",bg=P["panel"],cursor="hand2")
        self._fb.pack(side="left",padx=(16,0)); self._fb.bind("<Button-1>",lambda e:self._fire_hist())
        hm=tk.Label(i,text="🏠",font=("Helvetica",14),fg=P["accent"],bg=P["panel"],cursor="hand2")
        hm.pack(side="left",padx=(12,0)); hm.bind("<Button-1>",lambda e:self._home())
        self._health_mon=HealthMonitor(self)
        self._health_dot=tk.Label(i,text="●",font=("Helvetica",14),fg="#888",bg=P["panel"],cursor="hand2")
        self._health_dot.pack(side="left",padx=(12,0))
        self._health_dot.bind("<Button-1>",lambda e:self._health_popup())
        self._back_btn=tk.Button(i,text="← Scan",font=("Helvetica",10,"bold"),fg=P["accent"],bg=P["panel"],
                                 bd=0,cursor="hand2",command=self._back_to_scan)
        # shown only when scan results exist — starts hidden
        rt=tk.Frame(i,bg=P["panel"]); rt.pack(side="right",anchor="e")
        rt_top=tk.Frame(rt,bg=P["panel"]); rt_top.pack(side="top",anchor="e")
        self._pct=tk.Label(rt_top,text="",font=("Helvetica",11,"bold"),fg=P["accent"],bg=P["panel"]); self._pct.pack(side="right",padx=(12,0))
        self._clk=tk.Label(rt_top,text="",font=("Helvetica",11),fg=P["muted"],bg=P["panel"]); self._clk.pack(side="right")
        self._last_scan_lbl=tk.Label(rt,text="",font=("Helvetica",9),fg=P["muted"],bg=P["panel"])
        self._last_scan_lbl.pack(side="top",anchor="e")

    def _build_left(self, parent):
        l=tk.Frame(parent,bg=P["panel"],width=220); l.pack(side="left",fill="y",padx=(0,8)); l.pack_propagate(False)
        sf=tk.Frame(l,bg=P["panel"]); sf.pack(fill="x",padx=8,pady=(10,4))
        tk.Label(sf,text="Symbol",font=("Helvetica",11,"bold"),fg=P["text"],bg=P["panel"]).pack(anchor="w")
        tk.Entry(sf,textvariable=self._sym,font=("Helvetica",14,"bold"),width=10,justify="center").pack(fill="x")
        self._abtn=tk.Button(l,text="▶ ANALYZE + BACKTEST",font=("Helvetica",12,"bold"),fg="white",bg=P["accent"],bd=0,cursor="hand2",pady=8,command=self._analyze)
        self._abtn.pack(fill="x",padx=8,pady=(8,4))
        tk.Label(l,text="Quick select:",font=("Helvetica",10),fg=P["muted"],bg=P["panel"]).pack(anchor="w",padx=8)
        bf=tk.Frame(l,bg=P["panel"]); bf.pack(fill="x",padx=8)
        for i,s in enumerate(CORE):
            tk.Button(bf,text=s,font=("Helvetica",10,"bold"),fg=P["text"],bg=P["card"],bd=0,cursor="hand2",width=5,pady=2,
                     command=lambda s=s:self._pick(s)).grid(row=i//3,column=i%3,padx=2,pady=2,sticky="ew")
        for c in range(3): bf.columnconfigure(c,weight=1)
        self._dl=tk.Label(l,text="",font=("Helvetica",10),fg=P["muted"],bg=P["panel"]); self._dl.pack(anchor="w",padx=8,pady=(6,0))
        self._df2=tk.Frame(l,bg=P["panel"]); self._df2.pack(fill="x",padx=8)
        ttk.Separator(l).pack(fill="x",padx=8,pady=8)
        rf=tk.Frame(l,bg=P["panel"]); rf.pack(fill="x",padx=8)
        tk.Label(rf,text="Regime mode:",font=("Helvetica",10,"bold"),fg=P["text"],bg=P["panel"]).pack(anchor="w")
        for m in ("aggressive","normal","conservative"):
            tk.Radiobutton(rf,text=m.capitalize(),variable=self._mode,value=m,font=("Helvetica",10),bg=P["panel"],fg=P["text"],
                          activebackground=P["panel"],selectcolor=P["panel"],command=self._recalc).pack(anchor="w")
        ttk.Separator(l).pack(fill="x",padx=8,pady=8)
        for txt,cmd,bg in [("⚡  SCAN WATCHLIST",self._scan,P["accent"]),("💾  EXPORT CSV",self._export,P["muted"])]:
            tk.Button(l,text=txt,font=("Helvetica",11,"bold"),fg="white",bg=bg,bd=0,cursor="hand2",pady=6,command=cmd).pack(fill="x",padx=8,pady=2)
        self._auto=tk.BooleanVar(value=False)
        tk.Checkbutton(l,text="Auto-scan (open/close/AH)",variable=self._auto,font=("Helvetica",10),bg=P["panel"],fg=P["text"],selectcolor=P["panel"]).pack(anchor="w",padx=8,pady=(8,0))
        ttk.Separator(l).pack(fill="x",padx=8,pady=8)
        # Temp stock for next scan
        tf=tk.Frame(l,bg=P["panel"]); tf.pack(fill="x",padx=8)
        tk.Label(tf,text="Add to scan:",font=("Helvetica",10,"bold"),fg=P["text"],bg=P["panel"]).pack(anchor="w")
        af=tk.Frame(tf,bg=P["panel"]); af.pack(fill="x")
        self._temp_entry=tk.Entry(af,font=("Helvetica",11),width=8); self._temp_entry.pack(side="left")
        tk.Button(af,text="➕",font=("Helvetica",11),fg=P["accent"],bg=P["panel"],bd=0,cursor="hand2",
                 command=self._add_temp).pack(side="left",padx=4)
        tk.Button(af,text="➕ Perm",font=("Helvetica",9),fg=P["green"],bg=P["panel"],bd=0,cursor="hand2",
                 command=self._add_perm).pack(side="left",padx=2)
        self._temp_lbl=tk.Label(tf,text="",font=("Helvetica",9),fg=P["muted"],bg=P["panel"])
        self._temp_lbl.pack(anchor="w")
        tk.Button(l,text="📋  MANAGE LIST",font=("Helvetica",10,"bold"),fg=P["accent"],bg=P["card"],
                 bd=0,cursor="hand2",pady=4,command=self._manage_universe).pack(fill="x",padx=8,pady=(4,0))
        tk.Label(l,text=f"{len(UNIVERSE)} stocks",font=("Helvetica",9),fg=P["muted"],bg=P["panel"]).pack(anchor="w",padx=8)

    def _build_right(self, parent):
        r=tk.Frame(parent,bg=P["bg"]); r.pack(side="left",fill="both",expand=True)
        self._nb=ttk.Notebook(r); self._nb.pack(fill="both",expand=True)
        self._tc=tk.Frame(self._nb,bg="white"); self._tb=tk.Frame(self._nb,bg="white")
        self._ts=tk.Frame(self._nb,bg="white"); self._tt=tk.Frame(self._nb,bg="white")
        self._ta=tk.Frame(self._nb,bg="white"); self._tres=tk.Frame(self._nb,bg="white"); self._trd=tk.Frame(self._nb,bg="white")
        self._nb.add(self._tc,text="📈 CHART"); self._nb.add(self._tb,text="📊 BACKTEST")
        self._nb.add(self._ts,text="🎯 SIGNALS"); self._nb.add(self._tt,text="📊 TRACKER"); self._nb.add(self._ta,text="🔬 ANALYTICS"); self._nb.add(self._tres,text="🔍 RESEARCH"); self._nb.add(self._trd,text="📋 TRADES")
        self._sc=tk.Frame(self._ts,bg="white"); self._sc.pack(fill="both",expand=True,padx=10,pady=10)
        self._tc_hdr=tk.Frame(self._tc,bg="white"); self._tc_hdr.pack(fill="x")  # stock summary above chart
        self._bc=tk.Frame(self._tb,bg="white"); self._bc.pack(fill="both",expand=True,padx=10,pady=10)
        self._build_tracker(); self._build_analytics(); self._build_research(); self._build_trades()

    def _build_tracker(self):
        f=self._tt; hdr=tk.Frame(f,bg="white"); hdr.pack(fill="x",padx=10,pady=(10,4))
        tk.Label(hdr,text="📊 Signal Tracker",font=("Helvetica",16,"bold"),fg=P["text"],bg="white").pack(side="left")
        tk.Button(hdr,text="🔄 Check Outcomes",font=("Helvetica",11,"bold"),fg="white",bg=P["accent"],bd=0,cursor="hand2",
                 command=self._check_oc).pack(side="right")
        self._trs=tk.Frame(f,bg="white"); self._trs.pack(fill="x",padx=10)
        self._trt=tk.Text(f,font=("Consolas",11),bg="white",fg=P["text"],wrap="word",bd=0)
        scr=tk.Scrollbar(f,command=self._trt.yview); self._trt.configure(yscrollcommand=scr.set)
        scr.pack(side="right",fill="y",padx=(0,10)); self._trt.pack(fill="both",expand=True,padx=10)
        for t,c in [("win",P["green"]),("loss",P["red"]),("muted",P["muted"])]:
            self._trt.tag_configure(t,foreground=c)
        self._trt.tag_configure("hdr",font=("Consolas",12,"bold"))
        self._ref_tracker()

    def _build_analytics(self):
        f=self._ta
        # ── Header ──
        hdr=tk.Frame(f,bg="white"); hdr.pack(fill="x",padx=10,pady=(10,4))
        tk.Label(hdr,text="🔬 Outcome Analytics",font=("Helvetica",16,"bold"),fg=P["text"],bg="white").pack(side="left")
        self._ana_ts=tk.Label(hdr,text="",font=("Helvetica",9),fg=P["muted"],bg="white"); self._ana_ts.pack(side="right",padx=(0,10))
        self._ana_recomp_btn=tk.Button(hdr,text="🔄 Recompute",font=("Helvetica",10,"bold"),fg="white",bg=P["accent"],
                 bd=0,cursor="hand2",padx=8,command=self._recompute_analytics); self._ana_recomp_btn.pack(side="right")
        self._e0_run_btn=tk.Button(hdr,text="🔬 E0 Backfill",font=("Helvetica",10,"bold"),fg="white",bg="#1a4a6b",
                 bd=0,cursor="hand2",padx=8,command=self._run_e0_backfill_manual); self._e0_run_btn.pack(side="right",padx=(0,4))
        self._bt_run_btn=tk.Button(hdr,text="📊 Run Backtest",font=("Helvetica",10,"bold"),fg="white",bg="#1a6b3c",
                 bd=0,cursor="hand2",padx=8,command=self._run_backtest_manual); self._bt_run_btn.pack(side="right",padx=(0,4))
        # ── Backtest Health ──
        self._bt_health_frame=tk.Frame(self._ta,bg=P["card"],padx=10,pady=8)
        self._bt_health_frame.pack(fill="x",padx=10,pady=(0,4))
        self._refresh_bt_health()
        # ── Scrollable outcome panel ──
        oc=tk.Canvas(f,bg="white",highlightthickness=0,height=340)
        osb=tk.Scrollbar(f,orient="vertical",command=oc.yview)
        self._ana_frame=tk.Frame(oc,bg="white")
        self._ana_frame.bind("<Configure>",lambda e:oc.configure(scrollregion=oc.bbox("all")))
        oc.create_window((0,0),window=self._ana_frame,anchor="nw")
        oc.configure(yscrollcommand=osb.set)
        osb.pack(side="right",fill="y",padx=(0,6)); oc.pack(fill="x",padx=10)
        # ── Signal Query (existing) ──
        ttk.Separator(f).pack(fill="x",padx=10,pady=6)
        tk.Label(f,text="📊 Signal Query",font=("Helvetica",13,"bold"),fg=P["text"],bg="white").pack(anchor="w",padx=10,pady=(0,4))
        fl=tk.Frame(f,bg=P["card"],padx=10,pady=8); fl.pack(fill="x",padx=10)
        self._aq={}; r1=tk.Frame(fl,bg=P["card"]); r1.pack(fill="x",pady=2)
        for lbl,var,opts,par in [("Regime:","regime",["All","bull","normal","caution","danger"],r1),
                                   ("Score:","score",["All","72-79","80-84","85-89","90+"],r1),
                                   ("VIX:","vix",["All","< 15","15-20","20-25","25-30","> 30"],r1)]:
            tk.Label(par,text=lbl,font=("Helvetica",11,"bold"),fg=P["text"],bg=P["card"]).pack(side="left")
            self._aq[var]=tk.StringVar(value="All"); tk.OptionMenu(par,self._aq[var],*opts).pack(side="left",padx=(2,12))
        r2=tk.Frame(fl,bg=P["card"]); r2.pack(fill="x",pady=2)
        for lbl,var,opts,par in [("Condition:","cond",["All"]+sorted(CN),r2),("Stock:","stock",["All"],r2),
                                   ("Day:","day",["All","Mon","Tue","Wed","Thu","Fri"],r2)]:
            tk.Label(par,text=lbl,font=("Helvetica",11,"bold"),fg=P["text"],bg=P["card"]).pack(side="left")
            self._aq[var]=tk.StringVar(value="All"); w=tk.OptionMenu(par,self._aq[var],*opts); w.pack(side="left",padx=(2,12))
            if var=="stock": self._aqsm=w
        r3=tk.Frame(fl,bg=P["card"]); r3.pack(fill="x",pady=2)
        for lbl,var,opts,par in [
            ("Setup:","setup_type",["All","Deeply Oversold","Oversold Bounce","Momentum Acceleration","Technical Convergence"],r3),
            ("Confidence:","confidence",["All","0-1 (Low)","2-3 (Moderate)","4-5 (High)"],r3),
        ]:
            tk.Label(par,text=lbl,font=("Helvetica",11,"bold"),fg=P["text"],bg=P["card"]).pack(side="left")
            self._aq[var]=tk.StringVar(value="All"); tk.OptionMenu(par,self._aq[var],*opts).pack(side="left",padx=(2,12))
        tk.Button(fl,text="▶ RUN QUERY",font=("Helvetica",12,"bold"),fg="white",bg=P["accent"],bd=0,cursor="hand2",pady=6,
                 command=self._query).pack(fill="x",pady=(8,0))
        self._aqc=tk.Frame(f,bg="white"); self._aqc.pack(fill="x",padx=10,pady=4)
        self._aqt=tk.Text(f,font=("Consolas",11),bg="white",fg=P["text"],wrap="word",bd=0,height=10)
        qs=tk.Scrollbar(f,command=self._aqt.yview); self._aqt.configure(yscrollcommand=qs.set)
        qs.pack(side="right",fill="y",padx=(0,10)); self._aqt.pack(fill="both",expand=True,padx=10)
        for t,c in [("win",P["green"]),("loss",P["red"]),("muted",P["muted"]),("hdr",P["text"]),("accent",P["accent"])]:
            self._aqt.tag_configure(t,foreground=c,**({"font":("Consolas",12,"bold")} if t=="hdr" else {}))
        self._aqt.insert("end","Set filters and click RUN QUERY.\n","muted"); self._aqt.configure(state="disabled")
        self._refresh_analytics()

    def _refresh_analytics(self):
        for w in self._ana_frame.winfo_children(): w.destroy()
        oc=self._dm.outcome; ps=oc.get("portfolio_summary",{}); last_r=oc.get("last_recomputed")
        self._ana_ts.configure(text=f"Recomputed: {last_r[:16].replace('T',' ') if last_r else 'never'}")
        def stat_card(parent,label,value,color=P["text"]):
            c=tk.Frame(parent,bg=P["card"],padx=10,pady=6); c.pack(side="left",fill="x",expand=True,padx=(0,6))
            tk.Label(c,text=label,font=("Helvetica",9),fg=P["muted"],bg=P["card"]).pack(anchor="w")
            tk.Label(c,text=value,font=("Helvetica",15,"bold"),fg=color,bg=P["card"]).pack(anchor="w")
        has_data=False
        for period,label in [("all_time","All Time"),("last_30_days","Last 30d"),("last_90_days","Last 90d")]:
            st=ps.get(period,{})
            if not st.get("wins") and not st.get("losses"): continue
            has_data=True
            pf=tk.Frame(self._ana_frame,bg="white"); pf.pack(fill="x",pady=(0,6))
            tk.Label(pf,text=label,font=("Helvetica",11,"bold"),fg=P["muted"],bg="white").pack(anchor="w",pady=(4,2))
            cr=tk.Frame(pf,bg="white"); cr.pack(fill="x")
            wr=st.get("win_rate",0); pnl=st.get("total_pnl_usd",0)
            stat_card(cr,f"W / L",f"{st.get('wins',0)}W  {st.get('losses',0)}L",P["green"] if wr>=0.5 else P["red"])
            stat_card(cr,"Win Rate",f"{wr:.0%}",P["green"] if wr>=0.5 else P["red"])
            stat_card(cr,"P&L",f"${pnl:+,.0f}",P["green"] if pnl>=0 else P["red"])
            stat_card(cr,"Profit Factor",f"{st.get('profit_factor',0):.2f}",P["green"] if st.get("profit_factor",0)>=1 else P["red"])
            stat_card(cr,"Avg Hold",f"{st.get('avg_hold_days',0):.1f}d")
        by_stock=oc.get("by_stock",{})
        if by_stock:
            has_data=True
            ttk.Separator(self._ana_frame).pack(fill="x",pady=6)
            tk.Label(self._ana_frame,text="By Stock",font=("Helvetica",12,"bold"),fg=P["text"],bg="white").pack(anchor="w")
            sh=tk.Frame(self._ana_frame,bg=P["card"],padx=8,pady=3); sh.pack(fill="x",pady=(2,0))
            for t,w in [("Stock",10),("Closed",7),("Win Rate",9),("P&L",11),("Tag",8)]:
                tk.Label(sh,text=t,font=("Consolas",10,"bold"),fg=P["muted"],bg=P["card"],width=w,anchor="w").pack(side="left")
            for sym,d in sorted(by_stock.items(),key=lambda x:-x[1].get("win_rate",0)):
                wr=d.get("win_rate",0); pnl=d.get("pnl_usd",0); tag=d.get("tag")
                bg2="#fffde7" if tag=="GOLD" else "#fff0f0" if tag=="WEAK" else "white"
                sr=tk.Frame(self._ana_frame,bg=bg2,padx=8,pady=2); sr.pack(fill="x")
                tag_str,tag_clr=(("⭐ GOLD",P["gold"]) if tag=="GOLD" else ("⚠ WEAK",P["red"]) if tag=="WEAK" else ("",P["muted"]))
                tk.Label(sr,text=sym,font=("Consolas",11,"bold"),fg=P["accent"],bg=bg2,width=10,anchor="w").pack(side="left")
                tk.Label(sr,text=str(d.get("closed",0)),font=("Consolas",11),fg=P["text"],bg=bg2,width=7,anchor="w").pack(side="left")
                tk.Label(sr,text=f"{wr:.0%}",font=("Consolas",11,"bold"),fg=P["green"] if wr>=0.5 else P["red"],bg=bg2,width=9,anchor="w").pack(side="left")
                tk.Label(sr,text=f"${pnl:+,.0f}",font=("Consolas",11,"bold"),fg=P["green"] if pnl>=0 else P["red"],bg=bg2,width=11,anchor="w").pack(side="left")
                tk.Label(sr,text=tag_str,font=("Consolas",10,"bold"),fg=tag_clr,bg=bg2,width=8,anchor="w").pack(side="left")
        by_combo=oc.get("by_combo",{})
        if by_combo:
            has_data=True
            ttk.Separator(self._ana_frame).pack(fill="x",pady=6)
            tk.Label(self._ana_frame,text="Combo Efficacy",font=("Helvetica",12,"bold"),fg=P["text"],bg="white").pack(anchor="w")
            for key,d in sorted(by_combo.items(),key=lambda x:-x[1].get("win_rate",0)):
                n=d.get("signals",0); wr=d.get("win_rate",0); pnl=d.get("pnl_usd",0)
                supp=d.get("suppressed",False); flag=d.get("flagged",False)
                bg2="#fff0f0" if supp else "#fffde7" if flag else "white"
                cr=tk.Frame(self._ana_frame,bg=bg2,padx=8,pady=2); cr.pack(fill="x")
                badge,badge_clr=("⊘",P["red"]) if supp else ("⭐",P["gold"]) if flag else (" ",P["muted"])
                short="+".join(CN.get(c,c) for c in key.split("+"))
                if len(short)>48: short=short[:46]+"…"
                tk.Label(cr,text=badge,font=("Consolas",11,"bold"),fg=badge_clr,bg=bg2,width=2).pack(side="left")
                tk.Label(cr,text=short,font=("Consolas",10),fg=P["muted"] if supp else P["text"],bg=bg2,width=46,anchor="w").pack(side="left")
                tk.Label(cr,text=f"{n}×",font=("Consolas",10),fg=P["muted"],bg=bg2,width=5,anchor="w").pack(side="left")
                tk.Label(cr,text=f"{wr:.0%}",font=("Consolas",11,"bold"),fg=P["green"] if wr>=0.5 else P["red"],bg=bg2,width=6,anchor="w").pack(side="left")
                tk.Label(cr,text=f"${pnl:+,.0f}",font=("Consolas",10,"bold"),fg=P["green"] if pnl>=0 else P["red"],bg=bg2,width=10,anchor="w").pack(side="left")
        insights=oc.get("learning_insights",{})
        if any(insights.get(k) for k in ("top_performers","avoid_patterns","recommendations")):
            ttk.Separator(self._ana_frame).pack(fill="x",pady=6)
            tk.Label(self._ana_frame,text="Insights",font=("Helvetica",12,"bold"),fg=P["text"],bg="white").pack(anchor="w")
            for items,icon,clr in [(insights.get("top_performers",[]),"⭐",P["gold"]),
                                    (insights.get("recommendations",[]),"✓",P["green"]),
                                    (insights.get("avoid_patterns",[]),"✗",P["red"])]:
                for txt in items:
                    ir=tk.Frame(self._ana_frame,bg="white"); ir.pack(fill="x",pady=1)
                    tk.Label(ir,text=icon,font=("Helvetica",11,"bold"),fg=clr,bg="white",width=2).pack(side="left")
                    tk.Label(ir,text=txt,font=("Helvetica",10),fg=P["text"],bg="white",wraplength=700,justify="left").pack(side="left")
        if not has_data:
            tk.Label(self._ana_frame,text="No closed trades yet — log and close trades to see outcome analytics.",
                    font=("Helvetica",12),fg=P["muted"],bg="white").pack(pady=20)

    def _recompute_analytics(self):
        self._ana_recomp_btn.configure(text="⏳ Computing…",state="disabled")
        def bg():
            self._dm.force_recompute()
            self.root.after(0,lambda:(self._refresh_analytics(),self._ana_recomp_btn.configure(text="🔄 Recompute",state="normal")))
        threading.Thread(target=bg,daemon=True).start()

    def _refresh_bt_health(self):
        for w in self._bt_health_frame.winfo_children(): w.destroy()
        bt = self._dm.state.get("last_backtest", {})
        if not bt:
            tk.Label(self._bt_health_frame,text="📊 Backtest Health — no data yet. Click 'Run Backtest' to generate.",
                    font=("Helvetica",10),fg=P["muted"],bg=P["card"]).pack(anchor="w")
            return
        date_str = bt.get("date","?")
        signals  = bt.get("signals",0)
        wr       = bt.get("win_rate",0.0)
        pf       = bt.get("profit_factor",0.0)
        avg_ret  = bt.get("avg_return",0.0)
        hold     = bt.get("avg_hold_days",0.0)
        pf_color = P["green"] if pf >= 1.2 else (P["red"] if pf < 1.0 else "#cc8800")
        # Header row
        hr = tk.Frame(self._bt_health_frame,bg=P["card"]); hr.pack(fill="x")
        tk.Label(hr,text="📊 Backtest Health",font=("Helvetica",11,"bold"),fg=P["text"],bg=P["card"]).pack(side="left")
        tk.Label(hr,text="default params · "+date_str,font=("Helvetica",9),fg=P["muted"],bg=P["card"]).pack(side="left",padx=(8,0))
        if os.path.exists(_BT_RESULTS_PATH):
            tk.Button(hr,text="▶ Full Sweep",font=("Helvetica",9),fg=P["accent"],bg=P["card"],bd=0,
                     cursor="hand2",command=self._show_backtest_results).pack(side="right")
        # Stats row
        sr = tk.Frame(self._bt_health_frame,bg=P["card"]); sr.pack(fill="x",pady=(3,0))
        summary = (f"Last backtest: {date_str}  │  {signals} signals  │  "
                   f"{wr:.1f}% WR  │  PF {pf:.2f}  │  avg {avg_ret:+.2f}%  │  {hold:.1f}d hold")
        tk.Label(sr,text=summary,font=("Consolas",11,"bold"),fg=pf_color,bg=P["card"]).pack(anchor="w")

    def _run_backtest_manual(self):
        self._bt_run_btn.configure(text="⏳ Running…",state="disabled")
        def bg():
            try:
                # Load cache or fetch fresh
                cache_date, raw_data = _bt_load_ohlcv()
                today = dt.date.today().isoformat()
                if not raw_data or cache_date != today:
                    raw_data = _bt_fetch_all(UNIVERSE)
                    _bt_save_ohlcv(raw_data)
                default_stats, _ = _bt_run_sweep(raw_data)
                default_stats["date"] = today
                self._dm.state["last_backtest"] = default_stats
                self._dm.save_state()
                self.root.after(0, self._refresh_bt_health)
                self.root.after(0, lambda: self._bt_run_btn.configure(
                    text="📊 Run Backtest", state="normal"))
                self.root.after(0, lambda: messagebox.showinfo(
                    "Backtest Complete",
                    f"Backtest finished.\n\n"
                    f"Signals: {default_stats['signals']}\n"
                    f"Win Rate: {default_stats['win_rate']:.1f}%\n"
                    f"Profit Factor: {default_stats['profit_factor']:.2f}\n"
                    f"Avg Return: {default_stats['avg_return']:+.2f}%\n\n"
                    f"Full sweep saved to backtest_results.json"))
            except Exception as e:
                self.root.after(0, lambda: self._bt_run_btn.configure(
                    text="📊 Run Backtest", state="normal"))
                self.root.after(0, lambda: messagebox.showerror("Backtest Error", str(e)))
        threading.Thread(target=bg,daemon=True).start()

    def _run_e0_backfill_manual(self):
        if os.path.exists(_E0BacktestSink.SINK_PATH):
            if not messagebox.askyesno(
                    "Overwrite sink?",
                    f"E0 backtest sink already exists:\n{_E0BacktestSink.SINK_PATH}\n\nOverwrite?"):
                return
        self._e0_run_btn.configure(text="⏳ Running…", state="disabled")
        def bg():
            try:
                cache_date, raw_data = _bt_load_ohlcv()
                today = dt.date.today().isoformat()
                if not raw_data or cache_date != today:
                    raw_data = _bt_fetch_all(UNIVERSE)
                    _bt_save_ohlcv(raw_data)
                stats, sink_record_count = _bt_run_e0_backfill(raw_data)
                warn = " ⚠ DIVERGENCE — check sink schema sanity" if stats['signals'] != sink_record_count else ""
                self.root.after(0, lambda: self._e0_run_btn.configure(
                    text="🔬 E0 Backfill", state="normal"))
                self.root.after(0, lambda: messagebox.showinfo(
                    "E0 Backfill Complete",
                    f"E0 backfill finished.\n\n"
                    f"Signals (stats): {stats['signals']}\n"
                    f"Win Rate: {stats['win_rate']:.1f}%\n"
                    f"Profit Factor: {stats['profit_factor']:.2f}\n"
                    f"Avg Return: {stats['avg_return']:+.2f}%\n\n"
                    f"Sink: {_E0BacktestSink.SINK_PATH}\n"
                    f"Records (sink): {sink_record_count}{warn}"))
            except Exception as e:
                self.root.after(0, lambda: self._e0_run_btn.configure(
                    text="🔬 E0 Backfill", state="normal"))
                self.root.after(0, lambda: messagebox.showerror("E0 Backfill Error", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def _show_backtest_results(self):
        try:
            with open(_BT_RESULTS_PATH) as fh:
                data = json.load(fh)
        except Exception as e:
            messagebox.showerror("Error", f"Could not load results: {e}"); return
        win = tk.Toplevel(self.root); win.title("Backtest Sweep Results"); win.configure(bg="white")
        win.geometry("820x620")
        tk.Label(win,text="Backtest Sweep Results",font=("Helvetica",15,"bold"),fg=P["text"],bg="white").pack(pady=(12,4))
        tk.Label(win,text=f"Generated: {data.get('generated_at','?')}  ·  "
                         f"{data.get('stocks_fetched','?')}/{data.get('universe_size','?')} stocks",
                font=("Helvetica",9),fg=P["muted"],bg="white").pack()
        txt = tk.Text(win,font=("Consolas",10),bg="white",fg=P["text"],wrap="none",bd=0)
        sb  = tk.Scrollbar(win,command=txt.yview); txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right",fill="y"); txt.pack(fill="both",expand=True,padx=10,pady=8)
        for param, rows in data.get("sweep_results",{}).items():
            txt.insert("end",f"\n── {param} {'─'*50}\n")
            txt.insert("end",f"  {'Value':>8}  {'Signals':>8}  {'Win%':>6}  {'AvgRet%':>8}  {'PF':>6}  {'Hold':>6}\n")
            eligible = [r for r in rows if r["stats"]["signals"] >= 10]
            best_pf  = max((r["stats"]["profit_factor"] for r in eligible), default=0)
            for r in rows:
                s = r["stats"]; v = r["value"]
                vs = f"{v:.1f}" if isinstance(v,float) else str(v)
                mark = "  ◀" if eligible and s["signals"]>=10 and abs(s["profit_factor"]-best_pf)<0.01 else ""
                txt.insert("end",
                    f"  {vs:>8}  {s['signals']:>8}  {s['win_rate']:>5.1f}%  "
                    f"{s['avg_return']:>+7.2f}%  {s['profit_factor']:>6.2f}  "
                    f"{s['avg_hold_days']:>5.1f}d{mark}\n")
        opt = data.get("optimal")
        if opt:
            s = opt["stats"]
            txt.insert("end",f"\n── Optimal: {opt['dimension']}={opt['value']}  "
                             f"WR={s['win_rate']:.1f}%  PF={s['profit_factor']:.2f}  "
                             f"avg={s['avg_return']:+.2f}%\n")
        txt.configure(state="disabled")
        tk.Button(win,text="Close",command=win.destroy,bg=P["accent"],fg="white",bd=0,padx=12,pady=4).pack(pady=8)

    # ─── ACTIVE TRADES TAB ───────────────────────────────

    def _build_research(self):
        f=self._tres
        hdr=tk.Frame(f,bg="white"); hdr.pack(fill="x",padx=10,pady=(10,4))
        tk.Label(hdr,text="🔍 Research",font=("Helvetica",16,"bold"),fg=P["text"],bg="white").pack(side="left")
        # Quick Score bar (moved from Active Trades)
        qlf=tk.Frame(f,bg=P["panel"],padx=8,pady=5); qlf.pack(fill="x",padx=10,pady=(6,0))
        tk.Label(qlf,text="Quick Score:",font=("Helvetica",10,"bold"),fg=P["text"],bg=P["panel"]).pack(side="left",padx=(0,6))
        self._qs_sym=tk.StringVar()
        qe=tk.Entry(qlf,textvariable=self._qs_sym,font=("Helvetica",11,"bold"),width=8); qe.pack(side="left",padx=(0,6))
        qe.bind("<Return>",lambda e:self._quick_score())
        tk.Button(qlf,text="⚡ Score",font=("Helvetica",10,"bold"),fg="white",bg=P["accent"],bd=0,cursor="hand2",
                 command=self._quick_score).pack(side="left")
        self._qs_result=tk.Frame(f,bg="white"); self._qs_result.pack(fill="x",padx=10)

    def _build_trades(self):
        f=self._trd
        hdr=tk.Frame(f,bg="white"); hdr.pack(fill="x",padx=10,pady=(10,4))
        tk.Label(hdr,text="📋 Active Trades",font=("Helvetica",16,"bold"),fg=P["text"],bg="white").pack(side="left")
        tk.Button(hdr,text="🔄 Refresh",font=("Helvetica",11,"bold"),fg="white",bg=P["accent"],
                 bd=0,cursor="hand2",command=self._refresh_trades).pack(side="right")
        # SL/TP monitor toggle
        mon_cfg=self._dm.get_monitor_config()
        self._monitor_var=tk.BooleanVar(value=mon_cfg.get("enabled",False))
        def _toggle_monitor():
            enabled=self._monitor_var.get()
            self._dm.set_monitor_enabled(enabled)
            if enabled:
                self._monitor_thread = threading.Thread(target=self._monitor_loop,daemon=True)
                self._monitor_thread.start()
        tk.Checkbutton(hdr,text="Monitor SL/TP",font=("Helvetica",10),fg=P["text"],bg="white",
                      variable=self._monitor_var,command=_toggle_monitor).pack(side="right",padx=(0,8))
        if mon_cfg.get("enabled",False):
            self._monitor_thread = threading.Thread(target=self._monitor_loop,daemon=True)
            self._monitor_thread.start()
        self._trade_summary=tk.Label(f,text="",font=("Helvetica",11),fg=P["muted"],bg="white")
        self._trade_summary.pack(anchor="w",padx=10)
        # Log Trade entry bar
        ltf=tk.Frame(f,bg=P["panel"],padx=8,pady=5); ltf.pack(fill="x",padx=10,pady=(6,0))
        tk.Label(ltf,text="📝 Log Trade:",font=("Helvetica",10,"bold"),fg=P["text"],bg=P["panel"]).pack(side="left",padx=(0,6))
        self._lt_sym=tk.StringVar()
        lte=tk.Entry(ltf,textvariable=self._lt_sym,font=("Helvetica",11,"bold"),width=8); lte.pack(side="left",padx=(0,6))
        self._lt_status=tk.Label(ltf,text="",font=("Helvetica",10),fg=P["muted"],bg=P["panel"]); self._lt_status.pack(side="left",padx=(0,6))
        lte.bind("<Return>",lambda e:self._log_trade_lookup())
        tk.Button(ltf,text="📝 Log Trade",font=("Helvetica",10,"bold"),fg="white",bg=P["green"],bd=0,cursor="hand2",
                 command=self._log_trade_lookup).pack(side="left")
        self._lt_result=tk.Frame(f,bg="white"); self._lt_result.pack(fill="x",padx=10)
        self._trade_canvas=tk.Canvas(f,bg="white",highlightthickness=0)
        self._trade_sb=tk.Scrollbar(f,orient="vertical",command=self._trade_canvas.yview)
        self._trade_frame=tk.Frame(self._trade_canvas,bg="white")
        self._trade_frame.bind("<Configure>",lambda e:self._trade_canvas.configure(scrollregion=self._trade_canvas.bbox("all")))
        self._trade_canvas.create_window((0,0),window=self._trade_frame,anchor="nw")
        self._trade_canvas.configure(yscrollcommand=self._trade_sb.set)
        self._trade_sb.pack(side="right",fill="y",padx=(0,10)); self._trade_canvas.pack(fill="both",expand=True,padx=10)
        self._refresh_trades()

    def _refresh_trades(self):
        for w in self._trade_frame.winfo_children(): w.destroy()
        trades=self._dm.get_active_trades()
        n=len(trades); total_usd=sum(t["total_amount_usd"] for t in trades)
        self._trade_summary.configure(text=f"{n} open trade{'s' if n!=1 else ''} | ${total_usd:,.0f} deployed")
        if not trades:
            tk.Label(self._trade_frame,text="No active trades. Click 📝 on a BUY signal to log one.",
                    font=("Helvetica",12),fg=P["muted"],bg="white").pack(pady=20); return
        tk.Label(self._trade_frame,text="Fetching live quotes…",font=("Helvetica",11),fg=P["muted"],bg="white").pack(pady=10)
        def bg():
            live={}
            for t in trades:
                sym=t["stock"]
                cur=next((r["price"] for r in self._scan_res if r["symbol"]==sym),None)
                if cur is None:
                    q=fetch_quote(sym); cur=q.get("price")
                live[sym]=cur
            self.root.after(0,lambda:self._render_trade_rows(trades,live))
        threading.Thread(target=bg,daemon=True).start()

    def _render_trade_rows(self,trades,live):
        for w in self._trade_frame.winfo_children(): w.destroy()
        ch=tk.Frame(self._trade_frame,bg=P["card"],padx=8,pady=4); ch.pack(fill="x",pady=(0,2))
        for t,w in [("Stock",8),("Avg Entry",10),("Shares",7),("Amount",10),("P&L $",11),("P&L %",8),("Stop",9),("T1",9),("T2",9),("",8)]:
            tk.Label(ch,text=t,font=("Consolas",10,"bold"),fg=P["muted"],bg=P["card"],width=w,anchor="w").pack(side="left")
        for trade in trades:
            sym=trade["stock"]; avg=trade["avg_entry_price"]; sh=trade["total_shares"]
            amt=trade["total_amount_usd"]; exits=trade.get("suggested_exits",{})
            sl=exits.get("stop_loss_atr",{}).get("price"); t1=exits.get("take_profit_1r",{}).get("price"); t2=exits.get("take_profit_2r",{}).get("price")
            cur=live.get(sym)
            if cur:
                pnl_usd=round((cur-avg)*sh,2); pnl_pct=round((cur-avg)/avg*100,2)
                pnl_clr=P["green"] if pnl_usd>=0 else P["red"]
            else:
                pnl_usd=pnl_pct=None; pnl_clr=P["muted"]
            rbg="#f0faf0" if pnl_usd and pnl_usd>=0 else "#faf0f0" if pnl_usd and pnl_usd<0 else "white"
            rf=tk.Frame(self._trade_frame,bg=rbg,padx=8,pady=5); rf.pack(fill="x",pady=1)
            tk.Label(rf,text=sym,font=("Consolas",11,"bold"),fg=P["accent"],bg=rbg,width=8,anchor="w").pack(side="left")
            tk.Label(rf,text=f"${avg:.2f}",font=("Consolas",11),fg=P["text"],bg=rbg,width=10,anchor="w").pack(side="left")
            tk.Label(rf,text=str(sh),font=("Consolas",11),fg=P["text"],bg=rbg,width=7,anchor="w").pack(side="left")
            tk.Label(rf,text=f"${amt:,.0f}",font=("Consolas",11),fg=P["text"],bg=rbg,width=10,anchor="w").pack(side="left")
            tk.Label(rf,text=f"${pnl_usd:+,.2f}" if pnl_usd is not None else "—",font=("Consolas",11,"bold"),fg=pnl_clr,bg=rbg,width=11,anchor="w").pack(side="left")
            tk.Label(rf,text=f"{pnl_pct:+.1f}%" if pnl_pct is not None else "—",font=("Consolas",11,"bold"),fg=pnl_clr,bg=rbg,width=8,anchor="w").pack(side="left")
            tk.Label(rf,text=f"${sl:.2f}" if sl else "—",font=("Consolas",10),fg=P["red"],bg=rbg,width=9,anchor="w").pack(side="left")
            tk.Label(rf,text=f"${t1:.2f}" if t1 else "—",font=("Consolas",10),fg=P["green"],bg=rbg,width=9,anchor="w").pack(side="left")
            tk.Label(rf,text=f"${t2:.2f}" if t2 else "—",font=("Consolas",10),fg=P["green"],bg=rbg,width=9,anchor="w").pack(side="left")
            tk.Button(rf,text="Close",font=("Helvetica",10,"bold"),fg="white",bg=P["red"],bd=0,cursor="hand2",padx=6,pady=1,
                     command=lambda t=trade:self._close_trade_modal(t)).pack(side="right",padx=4)

    def _log_trade_modal(self,data):
        sym=data["symbol"]; price=data["price"]; atr=data.get("atr",0); score=data.get("final_score",0)
        stop=round(price-CFG.stop_m*atr,2); t1=round(price+CFG.t1_m*atr,2); t2=round(price+CFG.t2_m*atr,2)
        w=tk.Toplevel(self.root); w.title(f"📝 Log Trade: {sym}"); w.geometry("530x800")
        def _close_log(): w.grab_release(); w.destroy()
        w.configure(bg="white"); w.protocol("WM_DELETE_WINDOW",_close_log); w.grab_set()
        # Header
        hdr=tk.Frame(w,bg=P["card"],padx=12,pady=8); hdr.pack(fill="x")
        tk.Label(hdr,text=f"📝 LOG TRADE: {sym}",font=("Helvetica",15,"bold"),fg=P["text"],bg=P["card"]).pack(side="left")
        tk.Label(hdr,text=f"Score: {score}",font=("Helvetica",12),fg=P["muted"],bg=P["card"]).pack(side="right")
        # Signal info
        si=tk.Frame(w,bg=P["panel"],padx=10,pady=6); si.pack(fill="x")
        sig=data.get("signal","BUY"); sc=P["green"] if "BUY" in sig or "ACT NOW" in sig else P["red"]
        tk.Label(si,text=f"Signal: {sig}",font=("Helvetica",11,"bold"),fg=sc,bg=P["panel"]).pack(anchor="w")
        conds=data.get("conditions",set())
        if conds:
            cn_names=[CN.get(c,c) for c in sorted(conds)]
            tk.Label(si,text=", ".join(cn_names),font=("Helvetica",9),fg=P["muted"],bg=P["panel"],wraplength=490,justify="left").pack(anchor="w")
        # Fill rows
        ef=tk.Frame(w,bg="white",padx=12,pady=10); ef.pack(fill="x")
        # Input mode toggle
        mode_var=tk.StringVar(value="Shares")
        tgl=tk.Frame(ef,bg="white"); tgl.pack(anchor="w",pady=(0,6))
        tk.Label(tgl,text="Input mode:",font=("Helvetica",10),fg=P["muted"],bg="white").pack(side="left",padx=(0,8))
        for lbl,val in [("Shares","Shares"),("Dollar Amount","Dollar Amount")]:
            tk.Radiobutton(tgl,text=lbl,variable=mode_var,value=val,font=("Helvetica",10),
                           bg="white",fg=P["text"],activebackground="white",
                           command=lambda:_on_mode_change()).pack(side="left",padx=4)
        fills_frame=tk.Frame(ef,bg="white"); fills_frame.pack(fill="x")
        fills=[]; fill_widgets=[]
        amt_var=tk.StringVar(value="Amount: —")
        def _upd_amt(*_):
            try:
                total=sum(float(pv.get() or 0)*float(sv.get() or 0) for pv,sv in fills)
                if total==0 and mode_var.get()!="Shares" and any(float(pv.get() or 0)>0 for pv,sv in fills):
                    amt_var.set("Need at least 1 share")
                else:
                    amt_var.set(f"Amount: ${total:,.2f}")
            except (ValueError,TypeError): amt_var.set("Amount: —")
        _syncing=[False]
        def _sync_row(pv,sv,dv,se,de,*_):
            if _syncing[0]: return
            _syncing[0]=True
            try:
                p=float(pv.get() or 0)
                if mode_var.get()=="Shares":
                    de.config(state="normal"); dv.set(f"{float(sv.get() or 0)*p:.2f}"); de.config(state="readonly")
                else:
                    se.config(state="normal"); sv.set(str(round(float(dv.get() or 0)/p,4) if p else 0)); se.config(state="readonly")
            except (ValueError,TypeError): pass
            finally: _syncing[0]=False
            _upd_amt()
        def _on_mode_change():
            for pv,sv,dv,se,de in fill_widgets:
                if mode_var.get()=="Shares":
                    se.config(state="normal"); de.config(state="readonly")
                else:
                    de.config(state="normal"); se.config(state="readonly")
                _sync_row(pv,sv,dv,se,de)
        def add_fill_row(init_p="",init_s=""):
            row=tk.Frame(fills_frame,bg="white"); row.pack(fill="x",pady=2)
            tk.Label(row,text="Entry Price:",font=("Helvetica",11),fg=P["text"],bg="white",width=12,anchor="w").pack(side="left")
            pv=tk.StringVar(value=init_p); tk.Entry(row,textvariable=pv,font=("Helvetica",12,"bold"),width=8).pack(side="left",padx=(0,8))
            tk.Label(row,text="Shares:",font=("Helvetica",11),fg=P["text"],bg="white",width=7,anchor="w").pack(side="left")
            sv=tk.StringVar(value=init_s)
            se=tk.Entry(row,textvariable=sv,font=("Helvetica",12,"bold"),width=7); se.pack(side="left",padx=(0,8))
            tk.Label(row,text="$ Amt:",font=("Helvetica",11),fg=P["text"],bg="white",width=6,anchor="w").pack(side="left")
            dv=tk.StringVar()
            de=tk.Entry(row,textvariable=dv,font=("Helvetica",12,"bold"),width=9,readonlybackground="#f0f0f0"); de.pack(side="left")
            fills.append((pv,sv)); fill_widgets.append((pv,sv,dv,se,de))
            # Set initial state
            if mode_var.get()=="Shares":
                de.config(state="readonly")
                try: dv.set(f"{float(init_p or 0)*int(init_s or 0):.2f}")
                except: dv.set("0.00")
            else:
                se.config(state="readonly")
            def _on_sv(*_):
                if _syncing[0]: return
                if mode_var.get()=="Shares": _sync_row(pv,sv,dv,se,de)
                else: _upd_amt()
            def _on_dv(*_):
                if _syncing[0]: return
                if mode_var.get()!="Shares": _sync_row(pv,sv,dv,se,de)
                else: _upd_amt()
            def _on_pv(*_):
                if _syncing[0]: return
                _sync_row(pv,sv,dv,se,de)
            pv.trace_add("write",_on_pv)
            sv.trace_add("write",_on_sv); dv.trace_add("write",_on_dv)
            if len(fills)==1:
                def _on_price_exit(*_):
                    if _syncing[0]: return
                    try:
                        ep=float(pv.get())
                        if ep>0 and atr>0:
                            _syncing[0]=True
                            try:
                                ns=round(ep-CFG.stop_m*atr,2); nt1=round(ep+CFG.t1_m*atr,2); nt2=round(ep+CFG.t2_m*atr,2)
                                exits.update(stop=ns,t1=nt1,t2=nt2)
                                exit_vars["stop"].set(f"${ns:.2f}")
                                exit_vars["t1"].set(f"${nt1:.2f}")
                                exit_vars["t2"].set(f"${nt2:.2f}")
                            finally: _syncing[0]=False
                    except (ValueError,TypeError): pass
                pv.trace_add("write",_on_price_exit)
        add_fill_row(f"{price:.2f}")
        tk.Label(ef,textvariable=amt_var,font=("Helvetica",12,"bold"),fg=P["accent"],bg="white").pack(anchor="w",pady=(4,0))
        def add_another():
            q=fetch_quote(sym); add_fill_row(f"{q.get('price',price):.2f}")
        tk.Button(ef,text="+ Add Another Fill",font=("Helvetica",10),fg=P["accent"],bg="white",bd=0,cursor="hand2",command=add_another).pack(anchor="w",pady=(4,0))
        # Suggested exits
        exits={"stop":stop,"t1":t1,"t2":t2}
        exit_vars={"stop":tk.StringVar(value=f"${stop:.2f}"),
                   "t1":tk.StringVar(value=f"${t1:.2f}"),
                   "t2":tk.StringVar(value=f"${t2:.2f}")}
        xe=tk.Frame(w,bg=P["panel"],padx=10,pady=6); xe.pack(fill="x")
        xhdr=tk.Frame(xe,bg=P["panel"]); xhdr.pack(fill="x")
        tk.Label(xhdr,text="Suggested Exits (auto-updates with price):",font=("Helvetica",10,"bold"),fg=P["text"],bg=P["panel"]).pack(side="left")
        def _refresh_exits():
            q=fetch_quote(sym); np=q.get("price")
            if not np: return
            _syncing[0]=True
            try:
                ns=round(np-CFG.stop_m*atr,2); nt1=round(np+CFG.t1_m*atr,2); nt2=round(np+CFG.t2_m*atr,2)
                exits.update(stop=ns,t1=nt1,t2=nt2)
                exit_vars["stop"].set(f"${ns:.2f}")
                exit_vars["t1"].set(f"${nt1:.2f}")
                exit_vars["t2"].set(f"${nt2:.2f}")
                if fills: fills[0][0].set(f"{np:.2f}")
            finally: _syncing[0]=False
            _upd_amt()
        tk.Button(xhdr,text="🔄 Refresh",font=("Helvetica",9),fg=P["accent"],bg=P["panel"],bd=0,
                  cursor="hand2",command=_refresh_exits).pack(side="right")
        for key,lbl,clr in [("stop","Stop Loss (2×ATR):",P["red"]),
                             ("t1","Take Profit 1R:",P["green"]),
                             ("t2","Take Profit 2R:",P["green"])]:
            xr=tk.Frame(xe,bg=P["panel"]); xr.pack(fill="x")
            tk.Label(xr,text=lbl,font=("Helvetica",10),fg=P["muted"],bg=P["panel"],width=20,anchor="w").pack(side="left")
            tk.Label(xr,textvariable=exit_vars[key],font=("Helvetica",10,"bold"),fg=clr,bg=P["panel"]).pack(side="left")
        # ── TA reference levels (display-only) ──────────────────────────────
        ta=_ta_exit_levels(price,atr,data.get("indicators",{}))
        if ta:
            taf=tk.Frame(w,bg=P["card"],padx=10,pady=6); taf.pack(fill="x")
            def _lta(txt,clr=None,bold=False):
                fnt=("Consolas",9,"bold") if bold else ("Consolas",9)
                tk.Label(taf,text=txt,font=fnt,fg=clr or P["muted"],bg=P["card"],anchor="w").pack(fill="x")
            _lta("── TA Reference Levels ──")
            for val,lbl in ta["support"]:
                _lta(f"  ${val:>8.2f}   {lbl:<28}  (support)",P["green"])
            for val,lbl in ta["resistance"]:
                _lta(f"  ${val:>8.2f}   {lbl:<28}  (resistance)",P["red"])
            _lta("── Suggested (TA-aware) ──")
            _lta(f"  Stop:    ${ta['suggested_stop']:.2f}  ({ta['suggested_stop_lbl']})",P["red"],bold=True)
            _lta(f"  Target:  ${ta['suggested_target']:.2f}  ({ta['suggested_target_lbl']})",P["green"],bold=True)
        # Buttons
        bf=tk.Frame(w,bg="white",padx=12,pady=10); bf.pack(fill="x")
        def do_log():
            entries=[]
            for pv,sv in fills:
                try:
                    p=float(pv.get()); s=round(float(sv.get()),4)
                    if p*s>0: entries.append({"price":p,"shares":s,"timestamp":dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(timespec="seconds")+"Z"})
                except (ValueError,TypeError): pass
            if not entries: messagebox.showwarning("Invalid","Enter price and share count.",parent=w); return
            total_sh=sum(e["shares"] for e in entries)
            rec={"signal_date":dt.date.today().isoformat(),"signal_time":dt.datetime.now().strftime("%H:%M:%S"),
                 "score":score,"score_breakdown":{"base":data.get("raw_buy",score),"bt_adj":data.get("bt_adj",0),"final":score},
                 "primary_condition":next(iter(conds&PRIMARY),"") if isinstance(conds,set) else "",
                 "conditions_met":sorted(conds) if isinstance(conds,set) else [],
                 "combo_key":sorted(conds) if isinstance(conds,set) else []}
            rec["raw_score"]           = score
            rec["adjusted_score"]      = score  # placeholder; will diverge in Phase 4
            rec["breadth_multiplier"]  = self._meta.get("bp")
            rec["buy_count_at_scan"]   = self._meta.get("buy_count")
            # Persist setup profile + raw TA at entry for future backtesting
            sp=data.get("setup_profile")
            if sp:
                rec["setup_type"]=sp.get("type","").lower().replace(" ","_")
                rec["confidence_score"]=sp.get("confidence_score",0)
                rec["volume_ratio"]=round(sp.get("vol_ratio",1),2)
                rec["candle_quality_pct"]=int(sp.get("body_pct",0))
                rec["momentum_speed_pct_bar"]=round(sp.get("move_speed",0),3)
            ind=data.get("indicators",{})
            if ind:
                rec["ta_snapshot"]={k: round(float(v),4) if isinstance(v,(int,float)) else v
                                    for k,v in ind.items() if k in _TA_PERSIST_KEYS}
            suggested_exits={"stop_loss_atr":{"price":exits["stop"],"risk_usd":round((entries[0]["price"]-exits["stop"])*total_sh,2)},
                             "take_profit_1r":{"price":exits["t1"],"reward_usd":round((exits["t1"]-entries[0]["price"])*total_sh,2)},
                             "take_profit_2r":{"price":exits["t2"],"reward_usd":round((exits["t2"]-entries[0]["price"])*total_sh,2)}}
            tid=self._dm.log_trade(sym,rec,entries,suggested_exits=suggested_exits)
            w.destroy(); self._refresh_trades(); self._nb.select(self._trd)
            messagebox.showinfo("Trade Logged",f"{sym} logged.\nID: {tid}")
        tk.Button(bf,text="📝 LOG TRADE",font=("Helvetica",13,"bold"),fg="white",bg=P["green"],bd=0,cursor="hand2",pady=8,command=do_log).pack(side="left",fill="x",expand=True,padx=(0,6))
        tk.Button(bf,text="Cancel",font=("Helvetica",12),fg=P["muted"],bg=P["card"],bd=0,cursor="hand2",pady=8,command=_close_log).pack(side="right")

    def _close_trade_modal(self,trade):
        sym=trade["stock"]; avg=trade["avg_entry_price"]; sh=trade["total_shares"]; tid=trade["trade_id"]
        cur=next((r["price"] for r in self._scan_res if r["symbol"]==sym),None)
        if not cur: cur=(fetch_quote(sym).get("price") or avg)
        unreal=round((cur-avg)*sh,2); unreal_pct=round((cur-avg)/avg*100,2)
        w=tk.Toplevel(self.root); w.title(f"Close Trade: {sym}"); w.geometry("400,390".replace(",","x"))
        def _close_dlg(): w.grab_release(); w.destroy()
        w.configure(bg="white"); w.protocol("WM_DELETE_WINDOW",_close_dlg); w.grab_set()
        hdr=tk.Frame(w,bg=P["card"],padx=12,pady=8); hdr.pack(fill="x")
        tk.Label(hdr,text=f"✖ CLOSE TRADE: {sym}",font=("Helvetica",15,"bold"),fg=P["text"],bg=P["card"]).pack(anchor="w")
        ti=tk.Frame(w,bg=P["panel"],padx=10,pady=8); ti.pack(fill="x")
        pnl_clr=P["green"] if unreal>=0 else P["red"]
        for lbl,val,clr in [("Entry:",f"${avg:.2f} avg ({sh} shares)",P["text"]),
                             ("Current:",f"${cur:.2f}",P["text"]),
                             ("Unrealized P&L:",f"{'+' if unreal>=0 else ''}{unreal:,.2f} ({unreal_pct:+.1f}%)",pnl_clr)]:
            r=tk.Frame(ti,bg=P["panel"]); r.pack(fill="x",pady=1)
            tk.Label(r,text=lbl,font=("Helvetica",11),fg=P["muted"],bg=P["panel"],width=18,anchor="w").pack(side="left")
            tk.Label(r,text=val,font=("Helvetica",11,"bold"),fg=clr,bg=P["panel"]).pack(side="left")
        ef=tk.Frame(w,bg="white",padx=12,pady=10); ef.pack(fill="x")
        shares_var=tk.StringVar(value=str(sh)); price_var=tk.StringVar(value=f"{cur:.2f}")
        for lbl,var,note in [("Close Shares:",shares_var,f"(max {sh})"),("Exit Price:",price_var,"← auto-filled")]:
            r=tk.Frame(ef,bg="white"); r.pack(fill="x",pady=3)
            tk.Label(r,text=lbl,font=("Helvetica",11),fg=P["text"],bg="white",width=14,anchor="w").pack(side="left")
            tk.Entry(r,textvariable=var,font=("Helvetica",12,"bold"),width=10).pack(side="left")
            tk.Label(r,text=note,font=("Helvetica",9),fg=P["muted"],bg="white").pack(side="left",padx=6)
        reason_var=tk.StringVar(value="Took profit")
        r=tk.Frame(ef,bg="white"); r.pack(fill="x",pady=3)
        tk.Label(r,text="Reason:",font=("Helvetica",11),fg=P["text"],bg="white",width=14,anchor="w").pack(side="left")
        tk.OptionMenu(r,reason_var,"Took profit","Stop loss hit","Time stop","Manual exit","Other").pack(side="left")
        bf=tk.Frame(w,bg="white",padx=12,pady=10); bf.pack(fill="x")
        def do_close():
            try: exit_sh=float(shares_var.get()); exit_p=float(price_var.get())
            except (ValueError,TypeError): messagebox.showwarning("Invalid","Enter valid shares and price.",parent=w); return
            if exit_sh<=0.0 or exit_sh>sh: messagebox.showwarning("Invalid",f"Shares must be 1–{sh}.",parent=w); return
            ts_now = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(timespec="seconds")+"Z"
            exits=[{"price":exit_p,"shares":exit_sh,"timestamp":ts_now,"reason":reason_var.get()}]

            # E0-live: build telemetry only on full close
            telemetry = None
            if exit_sh == sh:
                reason_map = {
                    "Took profit": "TP",
                    "Stop loss hit": "SL",
                    "Time stop": "TIME",
                    "Manual exit": "MANUAL",
                    "Other": "OTHER",
                }
                exit_reason = reason_map.get(reason_var.get(), "OTHER")
                exit_sell_score = None
                exit_ta_snapshot = {}
                condition_decay = {}
                try:
                    df_e = fetch_ohlcv(sym)
                    if df_e is not None and len(df_e) >= 2:
                        add_indicators(df_e)
                        row_e, prev_e = df_e.iloc[-1], df_e.iloc[-2]
                        ss_e, _ = sell_score(row_e, prev_e)
                        exit_sell_score = int(ss_e)
                        exit_ta_snapshot = {
                            k: (round(float(row_e.get(k)), 4) if isinstance(row_e.get(k), (int, float)) else row_e.get(k))
                            for k in _TA_PERSIST_KEYS
                            if row_e.get(k) is not None
                        }
                        active = self._dm._find_active_trade(tid)
                        entry_conds = (active.get("recommendation", {}).get("conditions_met", []) or []) if active else []
                        if entry_conds:
                            now_conds = dict(_buy_rules(row_e, prev_e))
                            condition_decay = {c: (c in now_conds) for c in entry_conds}
                except Exception as e:
                    log.error(f"E0-live exit TA fetch: {e}")
                telemetry = {
                    "exit_reason": exit_reason,
                    "exit_sell_score": exit_sell_score,
                    "exit_regime": None,
                    "exit_ta_snapshot": exit_ta_snapshot,
                    "condition_decay": condition_decay,
                }

            result=self._dm.close_trade(tid,exits,telemetry=telemetry)
            w.destroy(); self._refresh_trades()
            if result=="full": messagebox.showinfo("Closed",f"{sym} fully closed.\nP&L: ${(exit_p-avg)*exit_sh:+,.2f}")
            elif result=="partial": messagebox.showinfo("Partial",f"{sym}: {exit_sh} shares closed. {sh-exit_sh} remaining.")
        tk.Button(bf,text="CLOSE TRADE",font=("Helvetica",13,"bold"),fg="white",bg=P["red"],bd=0,cursor="hand2",pady=8,command=do_close).pack(side="left",fill="x",expand=True,padx=(0,6))
        tk.Button(bf,text="Cancel",font=("Helvetica",12),fg=P["muted"],bg=P["card"],bd=0,cursor="hand2",pady=8,command=_close_dlg).pack(side="right")

    # ─── QUICK SCORE LOOKUP ──────────────────────────────

    def _quick_score(self, on_success=None):
        sym=self._qs_sym.get().upper().strip()
        if not sym: return
        for w in self._qs_result.winfo_children(): w.destroy()
        tk.Label(self._qs_result,text=f"Fetching {sym}…",font=("Helvetica",10),fg=P["muted"],bg="white").pack(anchor="w",pady=2)
        def bg():
            try:
                df=fetch_ohlcv(sym)
                if df is None or len(df)<50:
                    msg=f"No data for {sym}"
                    if on_success is None:
                        self.root.after(0,lambda:self._qs_show_err(msg))
                    else:
                        def _show_err():
                            self._lt_status.configure(text="")
                            for w in self._lt_result.winfo_children(): w.destroy()
                            tk.Label(self._lt_result,text=f"Error: {msg}",font=("Helvetica",10),fg=P["red"],bg="white").pack(anchor="w",pady=2)
                        self.root.after(0,_show_err)
                    return
                add_indicators(df); last,prev=df.iloc[-1],df.iloc[-2]
                atr=last.get("ATR",0); price=last["Close"]
                bs,bn,bc=buy_score(last,prev)
                cpts=dict(_buy_rules(last,prev))
                prim_fired={k:cpts[k] for k in cpts if k in PRIMARY}
                strong_fired={k:v for k,v in prim_fired.items() if v>=24}
                if strong_fired:
                    pk=max(strong_fired,key=strong_fired.get)
                    primary_str=f"{CN.get(pk,pk)} (+{strong_fired[pk]})"
                elif prim_fired:
                    pk=max(prim_fired,key=prim_fired.get)
                    primary_str=f"{CN.get(pk,pk)} (+{prim_fired[pk]}) — weak, BUY blocked"
                else:
                    primary_str="None"
                stop=round(price-CFG.stop_m*atr,2); t1r=round(price+CFG.t1_m*atr,2); t2r=round(price+CFG.t2_m*atr,2)
                if passes_gates(bs,bn,bc,atr,price): sig="▲ BUY"
                elif bs>=CFG.min_buy: sig="◌ WATCH (score ok, weak primary)"
                else: sig="◌ WATCH"
                ind={k:last.get(k,0) for k in ["RSI","RSI3","MACD_hist","EMA9","EMA21","EMA50","BB_pct","StochRSI","VolRatio","VWAP","WillR","ROC5"]}
                ind["MACD_hist_prev"]=prev.get("MACD_hist",0); ind["EMA9_prev"]=prev.get("EMA9",0); ind["EMA21_prev"]=prev.get("EMA21",0)
                ind["CMF"]=last.get("CMF",0); ind["OBV_div_bull"]=last.get("OBV_div_bull",False); ind["RSI_div"]=last.get("RSI_div",False)
                ind["Fib_382"]=last.get("Fib_382",0); ind["Fib_50"]=last.get("Fib_50",0)
                ind["swing_low_20"]=last.get("swing_low_20",0); ind["swing_high_20"]=last.get("swing_high_20",0)
                ind["EMA200"]=last.get("EMA200",0); ind["BB_upper"]=last.get("BB_upper",0); ind["BB_lower"]=last.get("BB_lower",0)
                data={"symbol":sym,"price":price,"atr":atr,"final_score":bs,"signal":sig,
                      "conditions":bc,"n_conditions":bn,"primary_str":primary_str,
                      "exits":{"stop":stop,"t1":t1r,"t2":t2r},"raw_buy":bs,"bt_adj":0,
                      "atr_swing":CFG.stop_m*atr/price*100 if price>0 else 0,
                      "indicators":ind,"arb":{"is_arb":False,"z":0,"bonus":0},"event":{"status":"OK"}}
                fn=on_success if on_success is not None else self._qs_show
                self.root.after(0,lambda:fn(data))
            except Exception as e:
                err=str(e)
                if on_success is None:
                    self.root.after(0,lambda:self._qs_show_err(err))
                else:
                    def _show_err():
                        self._lt_status.configure(text="")
                        for w in self._lt_result.winfo_children(): w.destroy()
                        tk.Label(self._lt_result,text=f"Error: {err}",font=("Helvetica",10),fg=P["red"],bg="white").pack(anchor="w",pady=2)
                    self.root.after(0,_show_err)
        threading.Thread(target=bg,daemon=True).start()

    def _qs_show_err(self,msg):
        for w in self._qs_result.winfo_children(): w.destroy()
        tk.Label(self._qs_result,text=f"Error: {msg}",font=("Helvetica",10),fg=P["red"],bg="white").pack(anchor="w",pady=2)

    def _log_trade_lookup(self):
        sym=self._lt_sym.get().upper().strip()
        if not sym: return
        for w in self._lt_result.winfo_children(): w.destroy()
        self._lt_status.configure(text=f"Fetching {sym}…")
        def _on_success(data):
            self._lt_status.configure(text="")
            for w in self._lt_result.winfo_children(): w.destroy()
            self._log_trade_modal(data)
        def _on_err(msg):
            self._lt_status.configure(text="")
            for w in self._lt_result.winfo_children(): w.destroy()
            tk.Label(self._lt_result,text=f"Error: {msg}",font=("Helvetica",10),fg=P["red"],bg="white").pack(anchor="w",pady=2)
        self._qs_sym.set(sym)
        self._quick_score(on_success=_on_success)

    def _qs_show(self,data):
        for w in self._qs_result.winfo_children(): w.destroy()
        sym=data["symbol"]; bs=data["final_score"]; sig=data["signal"]
        ex=data["exits"]; conds=data.get("conditions",set())
        sc=P["green"] if "BUY" in sig else P["muted"]
        pf=tk.Frame(self._qs_result,bg=P["panel"],padx=8,pady=6); pf.pack(fill="x",pady=(4,0))
        r1=tk.Frame(pf,bg=P["panel"]); r1.pack(fill="x")
        tk.Label(r1,text=sym,font=("Helvetica",12,"bold"),fg=P["accent"],bg=P["panel"]).pack(side="left")
        tk.Label(r1,text=f"  Score: {bs}",font=("Helvetica",12,"bold"),fg=P["text"],bg=P["panel"]).pack(side="left")
        tk.Label(r1,text=f"  {sig}",font=("Helvetica",12,"bold"),fg=sc,bg=P["panel"]).pack(side="left")
        tk.Button(r1,text="📝 Log This",font=("Helvetica",10,"bold"),fg="white",bg=P["green"],bd=0,cursor="hand2",padx=6,
                 command=lambda:self._log_trade_modal(data)).pack(side="right")
        r2=tk.Frame(pf,bg=P["panel"]); r2.pack(fill="x",pady=(2,0))
        tk.Label(r2,text=f"Primary: {data.get('primary_str','—')}",font=("Helvetica",10),fg=P["text"],bg=P["panel"]).pack(side="left",padx=(0,12))
        tk.Label(r2,text=f"SL: ${ex['stop']:.2f}",font=("Consolas",10),fg=P["red"],bg=P["panel"]).pack(side="left",padx=(0,8))
        tk.Label(r2,text=f"T1: ${ex['t1']:.2f}",font=("Consolas",10),fg=P["green"],bg=P["panel"]).pack(side="left",padx=(0,8))
        tk.Label(r2,text=f"T2: ${ex['t2']:.2f}",font=("Consolas",10),fg=P["green"],bg=P["panel"]).pack(side="left")
        cmf_val=data.get("indicators",{}).get("CMF",None)
        if cmf_val is not None:
            r_cmf=tk.Frame(pf,bg=P["panel"]); r_cmf.pack(fill="x",pady=(2,0))
            cmf_clr=P["green"] if cmf_val>0.10 else P["red"] if cmf_val<-0.10 else P["muted"]
            cmf_label=("inflow ✓" if cmf_val>0.10 else "⚠ outflow" if cmf_val<-0.10 else "neutral")
            tk.Label(r_cmf,text=f"CMF: {cmf_val:+.3f}  {cmf_label}",font=("Consolas",10,"bold"),fg=cmf_clr,bg=P["panel"]).pack(side="left")
        if conds:
            r3=tk.Frame(pf,bg=P["panel"]); r3.pack(fill="x",pady=(2,0))
            cn_names=[CN.get(c,c) for c in sorted(conds)]
            tk.Label(r3,text=", ".join(cn_names),font=("Helvetica",9),fg=P["muted"],bg=P["panel"],wraplength=600,justify="left").pack(anchor="w")
        # ── TA exit reference levels ──────────────────────────────────────────
        ta=_ta_exit_levels(data["price"],data.get("atr",0),data.get("indicators",{}))
        if ta:
            tf=tk.Frame(self._qs_result,bg=P["card"],padx=8,pady=6); tf.pack(fill="x",pady=(4,0))
            def _ta_row(parent,txt,clr=None):
                tk.Label(parent,text=txt,font=("Consolas",9),fg=clr or P["text"],bg=P["card"],anchor="w").pack(fill="x")
            _ta_row(tf,f"── ATR Exits ──",P["muted"])
            _ta_row(tf,f"  Stop ({CFG.stop_m}×ATR):  ${ta['atr_stop']:.2f}   T1 ({CFG.t1_m}×ATR):  ${ta['atr_t1']:.2f}")
            if ta["support"] or ta["resistance"]:
                _ta_row(tf,"── TA Reference ──",P["muted"])
                for val,lbl in ta["support"]:
                    _ta_row(tf,f"  ${val:>8.2f}   {lbl:<26}  (support)",P["green"])
                for val,lbl in ta["resistance"]:
                    _ta_row(tf,f"  ${val:>8.2f}   {lbl:<26}  (resistance)",P["red"])
            _ta_row(tf,"── Suggested ──",P["muted"])
            _ta_row(tf,f"  Stop:    ${ta['suggested_stop']:.2f}  ({ta['suggested_stop_lbl']})",P["red"])
            _ta_row(tf,f"  Target:  ${ta['suggested_target']:.2f}  ({ta['suggested_target_lbl']})",P["green"])

    # ─── UNIVERSE MANAGEMENT ─────────────────────────────

    def _add_temp(self):
        sym=self._temp_entry.get().upper().strip()
        if sym:
            self._temp_syms.add(sym); self._temp_entry.delete(0,"end")
            self._temp_lbl.configure(text=f"Temp: {', '.join(sorted(self._temp_syms))}")

    def _add_perm(self):
        sym=self._temp_entry.get().upper().strip()
        if not sym: return
        self._temp_entry.delete(0,"end")
        if sym not in self._user_added: self._user_added.append(sym)
        if sym in self._user_removed: self._user_removed.remove(sym)
        _save_universe(self._user_added, self._user_removed)
        global UNIVERSE; UNIVERSE=_load_universe()
        messagebox.showinfo("Added",f"{sym} added permanently. Universe: {len(UNIVERSE)} stocks.")

    def _manage_universe(self):
        global UNIVERSE
        w=tk.Toplevel(self.root); w.title("📋 Manage Universe"); w.geometry("500x700"); w.configure(bg="white")
        w.protocol("WM_DELETE_WINDOW",w.destroy)
        tk.Label(w,text="📋 Stock Universe",font=("Helvetica",16,"bold"),fg=P["text"],bg="white").pack(pady=(10,4))
        tk.Label(w,text=f"{len(UNIVERSE)} stocks ({len(self._user_added)} added, {len(self._user_removed)} removed)",
                font=("Helvetica",10),fg=P["muted"],bg="white").pack()
        # Add new
        af=tk.Frame(w,bg=P["card"],padx=10,pady=8); af.pack(fill="x",padx=10,pady=8)
        tk.Label(af,text="Add symbol:",font=("Helvetica",11,"bold"),fg=P["text"],bg=P["card"]).pack(side="left")
        add_e=tk.Entry(af,font=("Helvetica",12),width=10); add_e.pack(side="left",padx=8)
        def do_add():
            s=add_e.get().upper().strip()
            if not s: return
            add_e.delete(0,"end")
            if s not in self._user_added: self._user_added.append(s)
            if s in self._user_removed: self._user_removed.remove(s)
            _save_universe(self._user_added,self._user_removed)
            global UNIVERSE; UNIVERSE=_load_universe()
            w.destroy(); self._manage_universe()
        tk.Button(af,text="➕ Add",font=("Helvetica",11,"bold"),fg="white",bg=P["green"],bd=0,cursor="hand2",
                 command=do_add).pack(side="left")
        # Scrollable list
        cv=tk.Canvas(w,bg="white",highlightthickness=0); sb=tk.Scrollbar(w,orient="vertical",command=cv.yview)
        sf=tk.Frame(cv,bg="white"); sf.bind("<Configure>",lambda e:cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0,0),window=sf,anchor="nw"); cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right",fill="y",padx=(0,10)); cv.pack(fill="both",expand=True,padx=10)
        for sym in UNIVERSE:
            is_core=sym in CORE; is_added=sym in self._user_added
            rf=tk.Frame(sf,bg="white"); rf.pack(fill="x",pady=1)
            tag=" [CORE]" if is_core else " [added]" if is_added else ""
            clr=P["accent"] if is_core else P["green"] if is_added else P["text"]
            tk.Label(rf,text=f"{sym}{tag}",font=("Consolas",11),fg=clr,bg="white",width=20,anchor="w").pack(side="left")
            if not is_core:
                def do_rm(s=sym):
                    if s in self._user_added: self._user_added.remove(s)
                    elif s not in self._user_removed: self._user_removed.append(s)
                    _save_universe(self._user_added,self._user_removed)
                    global UNIVERSE; UNIVERSE=_load_universe()
                    w.destroy(); self._manage_universe()
                tk.Button(rf,text="✕",font=("Helvetica",10,"bold"),fg=P["red"],bg="white",bd=0,cursor="hand2",
                         command=do_rm).pack(side="right",padx=8)

    # ─── PICK + ANALYZE ─────────────────────────────────

    def _pick(self,sym): self._sym.set(sym); self._analyze()

    def _analyze(self):
        self._abtn.configure(text="⏳ LOADING…",state="disabled",bg="#888")
        threading.Thread(target=self._analyze_bg,daemon=True).start()

    def _analyze_bg(self):
        sym=self._sym.get().upper().strip()
        try:
            df=fetch_ohlcv(sym)
            if df is None or len(df)<50:
                self.root.after(0,lambda:messagebox.showwarning("No Data",f"No data for {sym}")); return
            add_indicators(df); self._df=df; self._quote=fetch_quote(sym)
            sigs=[]
            for i in range(1,len(df)):
                r,p=df.iloc[i],df.iloc[i-1]; atr,pr=r.get("ATR",0),r["Close"]
                bs,bn,bc=buy_score(r,p)
                if passes_gates(bs,bn,bc,atr,pr): sigs.append({"date":r.name,"type":"BUY","price":pr})
                ss,sn=sell_score(r,p)
                if ss>=CFG.min_sell and sn>=3: sigs.append({"date":r.name,"type":"SELL","price":pr})
            self._sigs=sigs; last,prev=df.iloc[-1],df.iloc[-2]
            bs,bn,bc=buy_score(last,prev); ss,sn=sell_score(last,prev)
            atr,price=last.get("ATR",0),last["Close"]
            swing=CFG.stop_m*atr/price*100 if price>0 else 0
            evt=Events.check(sym,fast=False); bs+=evt.get("penalty",0)
            bt=backtest(df); self._bt=bt
            if Arb._spy is None: Arb.fetch_spy()
            arb=Arb.detect(df)
            if arb["is_arb"]: bs+=arb["bonus"]
            try: _sp=classify_setup(df,len(df)-1,bc,arb)
            except Exception: _sp=None
            ib=passes_gates(bs,bn,bc,atr,price) or bs>=CFG.min_buy
            st=("ARB_BUY" if arb["is_arb"] else "BUY") if ib else "SELL" if ss>=CFG.min_sell else "WATCH"
            if bs>=CFG.fire:
                self._fires.append({"symbol":sym,"score":bs,"price":price,"rsi":last.get("RSI",0),"atr":atr,"time":dt.datetime.now().strftime("%H:%M"),"setup_profile":_sp})
                self.root.after(0,lambda sp=_sp:self._fire_alert(sym,bs,price,last.get("RSI",0),atr,sp))
            ca=cond_accuracy(bt["trades"])
            self.root.after(0,lambda:self._render_az(sym,df,sigs,bt,bs,ss,st,atr,price,last.get("RSI",0),evt,arb,swing,bc,ca))
        except Exception as e: log.error(f"Analysis: {e}\n{traceback.format_exc()}")
        finally: self.root.after(0,lambda:self._abtn.configure(text="▶ ANALYZE + BACKTEST",state="normal",bg=P["accent"]))

    def _render_az(self,sym,df,sigs,bt,bs,ss,st,atr,price,rsi,evt,arb,swing,conds,ca):
        # ── Chart canvas ──────────────────────────────────────────────────────
        if self._cc: self._cc.get_tk_widget().destroy()
        render_chart(df,sym,sigs,self._fig,ca)
        self._cc=FigureCanvasTkAgg(self._fig,self._tc); self._cc.draw(); self._cc.get_tk_widget().pack(fill="both",expand=True)
        # ── Chart-tab header: stock summary + back button (self._sc untouched) ─
        for w in self._tc_hdr.winfo_children(): w.destroy()
        sc=P["green"] if "BUY" in st else P["red"] if st=="SELL" else P["muted"]
        hf=tk.Frame(self._tc_hdr,bg=P["card"],padx=10,pady=5); hf.pack(fill="x")
        tk.Label(hf,text=f"{sym} | RSI {rsi:.1f} | Buy:{bs} Sell:{ss} | Swing:{swing:.1f}% | {st}",
                font=("Helvetica",12,"bold"),fg=sc,bg=P["card"]).pack(side="left")
        ebdays=self._earnings_badge_days(sym)
        if ebdays is not None:
            eb=tk.Label(hf,text="E",font=("Helvetica",11,"bold"),fg=P["gold"],bg=P["card"],cursor="hand2")
            eb.pack(side="left",padx=(6,0))
            eb.bind("<Button-1>",lambda e:self._show_earnings_popup(sym))
        if evt.get("earn_flag"):
            tk.Label(self._tc_hdr,text=evt["earn_flag"],font=("Helvetica",11,"bold"),fg=P["orange"],bg="white").pack(anchor="w",padx=10,pady=(0,2))
        if self._quote and self._quote.get("extended") and self._quote.get("price"):
            pc=self._quote["change_pct"]
            tk.Label(self._tc_hdr,text=f"⚠️ EXTENDED: {'↑' if pc>=0 else '↓'} {abs(pc):.1f}%",font=("Helvetica",11,"bold"),
                    fg=P["green"] if pc>=0 else P["red"],bg="white").pack(anchor="w",padx=10,pady=(0,2))
        cf=tk.Frame(self._tc_hdr,bg="white"); cf.pack(fill="x")
        trade_card(cf,price,atr,bs,st,sym,self._quote,evt=evt)
        # ── Backtest tab ──────────────────────────────────────────────────────
        for w in self._bc.winfo_children(): w.destroy()
        s=bt["stats"]; bf=tk.Frame(self._bc,bg=P["card"],padx=10,pady=8); bf.pack(fill="x")
        tk.Label(bf,text=f"Backtest — {sym}",font=("Helvetica",14,"bold"),fg=P["text"],bg=P["card"]).pack(anchor="w")
        tk.Label(bf,text=f"Trades:{s['total']} Win:{s['wr']:.0f}% Ret:{s['ret']:+.1f}% Sharpe:{s['sharpe']:.2f} PF:{s['pf']:.2f} DD:{s['dd']:.1f}%",
                font=("Consolas",11),fg=P["text"],bg=P["card"]).pack(anchor="w")
        if ca:
            caf=tk.Frame(self._bc,bg=P["card"],padx=10,pady=6); caf.pack(fill="x")
            tk.Label(caf,text="Indicator accuracy:",font=("Helvetica",11,"bold"),fg=P["text"],bg=P["card"]).pack(anchor="w")
            for cn2,st2 in ca.items():
                clr=P["green"] if st2["wr"]>=60 else P["red"] if st2["wr"]<40 else P["text"]
                tk.Label(caf,text=f"  {CN.get(cn2,cn2):<20} {st2['wr']:5.0f}% ({st2['w']}/{st2['t']})",font=("Consolas",10),fg=clr,bg=P["card"]).pack(anchor="w")
        tl=tk.Text(self._bc,font=("Consolas",11),bg="white",fg=P["text"],wrap="none",bd=0,height=15)
        ts2=tk.Scrollbar(self._bc,command=tl.yview); tl.configure(yscrollcommand=ts2.set)
        ts2.pack(side="right",fill="y"); tl.pack(fill="both",expand=True)
        tl.tag_configure("win",foreground=P["green"]); tl.tag_configure("loss",foreground=P["red"])
        for t in bt["trades"]:
            ed=t["entry_date"].strftime("%Y-%m-%d") if hasattr(t["entry_date"],"strftime") else str(t["entry_date"])[:10]
            xd=t["exit_date"].strftime("%Y-%m-%d") if hasattr(t["exit_date"],"strftime") else str(t["exit_date"])[:10]
            tl.insert("end",f"{ed} {t['entry_price']:>8.2f} {xd} {t['exit_price']:>8.2f} {t['pnl_pct']:>+6.1f}% {t['days']:>4.0f}d {t['reason']}\n",
                     "win" if t["pnl"]>0 else "loss")
        tl.configure(state="disabled"); self._nb.select(self._tc)

    def _back_to_scan(self):
        """Switch to Signals tab. Header button visibility tracks self._scan_res."""
        self._nb.select(self._ts)

    def _upd_back_btn(self):
        """Show or hide the header ← Scan button depending on whether results exist."""
        if self._scan_res:
            self._back_btn.pack(side="left",padx=(12,0))
        else:
            self._back_btn.pack_forget()

    # ─── SCAN ──────────────────────────────────────────

    def _scan(self):
        if self._scanning: return
        self._scanning=True; threading.Thread(target=self._scan_bg,daemon=True).start()

    def _scan_bg(self):
        try:
            scan_universe=sorted(set(UNIVERSE)|self._temp_syms)
            total=len(scan_universe); results=[]; skipped=0; vix=20.0
            try:
                vh=yf.Ticker("^VIX").history(period="5d",interval="1d",auto_adjust=True)
                if vh is not None and len(vh)>0: vix=vh["Close"].iloc[-1]
            except Exception: pass
            Arb.fetch_spy()
            try: self._regime.assess(); self.root.after(0,self._upd_regime)
            except Exception: pass
            mult=self._regime.mult(self._mode.get()); cands=[]
            for i,sym in enumerate(scan_universe):
                if self._closing: return
                self.root.after(0,lambda p=int((i+1)/total*70):self._pct.configure(text=f"Scan {p}%"))
                try:
                    evt=Events.check(sym,fast=True)
                    if evt["status"]!="OK": skipped+=1; continue
                    df=fetch_ohlcv(sym,"6mo")
                    if df is None or len(df)<50: skipped+=1; continue
                    add_indicators(df); last,prev=df.iloc[-1],df.iloc[-2]
                    price,atr,rsi=last["Close"],last.get("ATR",0),last.get("RSI",50)
                    if atr<=0 or price<=0: skipped+=1; continue
                    sw=CFG.stop_m*atr/price*100; bs,bn,bc=buy_score(last,prev); ss,sn=sell_score(last,prev)
                    arb=Arb.detect(df); evt_pen=evt.get("penalty",0)
                    bsa_filter=int(bs*mult)+evt_pen; ssa_filter=int(ss*mult)
                    q=fetch_quote(sym)
                    if q.get("extended") and q.get("change_pct",0)<-3: bsa_filter+=20; evt_pen+=20
                    ind={k:last.get(k,0) for k in ["RSI","RSI3","MACD_hist","EMA9","EMA21","EMA50","BB_pct","StochRSI","VolRatio","VWAP","OBV_slope","WillR","ROC5","ROC20"]}
                    ind["RSI_div"]=last.get("RSI_div",False)
                    ind["CMF"]=last.get("CMF",0); ind["OBV_div_bull"]=last.get("OBV_div_bull",False)
                    ind["Fib_near_382"]=last.get("Fib_near_382",False)
                    ind["Fib_near_50"]=last.get("Fib_near_50",False)
                    ind["Fib_382"]=last.get("Fib_382",0)
                    ind["Fib_50"]=last.get("Fib_50",0)
                    ind["MACD_hist_prev"]=prev.get("MACD_hist",0); ind["EMA9_prev"]=prev.get("EMA9",0); ind["EMA21_prev"]=prev.get("EMA21",0)
                    ind["swing_low_20"]=last.get("swing_low_20",0); ind["swing_high_20"]=last.get("swing_high_20",0)
                    ind["EMA200"]=last.get("EMA200",0); ind["BB_upper"]=last.get("BB_upper",0); ind["BB_lower"]=last.get("BB_lower",0)
                    ind["Open"]=last.get("Open",0); ind["High"]=last["High"]; ind["Low"]=last["Low"]; ind["Close"]=last["Close"]
                    try: sp=classify_setup(df,len(df)-1,bc,arb)
                    except Exception: sp=None
                    e={"symbol":sym,"price":price,"rsi":rsi,
                       "buy_score":bs+evt_pen,"sell_score":ss,   # raw (pre-regime); regime applied by score_and_assign
                       "raw_buy":bs,"raw_sell":ss,
                       "atr_swing":sw,"conditions":bc,"n_conditions":bn,"event":evt,"atr":atr,"extra_bonus":evt_pen,
                       "arb":arb,"indicators":ind,"setup_profile":sp}
                    if bsa_filter>=45 or ssa_filter>=CFG.min_sell: cands.append(e)
                    elif bs>=35: e["_w"]=True; cands.append(e)
                except Exception: skipped+=1
                time.sleep(0.05)
            # Phase 2
            for j,c in enumerate([c for c in cands if c.get("buy_score",0)>=45]):
                if self._closing: return
                self.root.after(0,lambda p=70+int((j+1)/max(sum(1 for c in cands if c.get("buy_score",0)>=45),1)*30):self._pct.configure(text=f"Scan {p}%"))
                a=self._cache.adj(c["symbol"])
                if a==0 and not self._cache.fresh():
                    try:
                        d2=fetch_ohlcv(c["symbol"])
                        if d2 is not None and len(d2)>=50: add_indicators(d2); self._cache.put(c["symbol"],backtest(d2)["stats"]); a=self._cache.adj(c["symbol"])
                    except Exception: pass
                a=min(max(a,-20),10); c["bt_adj"]=a; time.sleep(0.05)
            self._cache.save()
            for c in cands:
                if "bt_adj" not in c: c["bt_adj"]=0
            results, bp, buy_count = score_and_assign(cands, mult, self._dm)
            results.sort(key=lambda r:(0 if "ACT NOW" in r["signal"] else 1 if "ARB" in r["signal"] else 2 if "BUY" in r["signal"] else 3 if "SELL" in r["signal"] else 4,-r["final_score"]))
            self._scan_res=results; self._meta={"time":dt.datetime.now().strftime("%H:%M:%S"),"vix":vix,"skip":skipped,"total":total,"bp":bp,"buy_count":buy_count}
            for r in results:
                if any(k in r.get("signal","") for k in ("BUY","ACT NOW","ARB")):
                    self._tracker.log(r["symbol"],r["price"],r["final_score"],self._regime.state,vix,r["conditions"],r["atr_swing"],r.get("setup_profile"),r.get("indicators"),bp=bp,buy_count=buy_count)
            fires=[r for r in results if "ACT NOW" in r.get("signal","")]
            for f in fires: self._fires.append({"symbol":f["symbol"],"score":f["final_score"],"price":f["price"],"rsi":f["rsi"],"atr":f.get("atr",0),"time":dt.datetime.now().strftime("%H:%M"),"setup_profile":f.get("setup_profile")})
            # Save scan summary to data layer (field mapping: symbol→stock, final_score→score, conditions→combo_key)
            try:
                dm_results=[{"stock":r["symbol"],"score":r["final_score"],"combo_key":list(r.get("conditions",set())),
                             "signal":r.get("signal","WATCH"),"suppressed":r.get("suppressed",False),
                             "passes_gates":any(k in r.get("signal","") for k in ("BUY","ACT NOW","ARB"))}
                            for r in results]
                market_ctx={"spy_price":None,"vix":vix,"regime":self._regime.state}
                self._dm.save_scan_summary(dm_results,market_ctx)
            except Exception as e: log.error(f"save_scan_summary: {e}")
            # Log fire alerts to data layer
            for f in fires:
                try:
                    self._dm.log_fire_alert(f["symbol"],"ACT_NOW",
                        {"score":f["final_score"],"price":f["price"],"rsi":f["rsi"],"atr":f.get("atr",0)},
                        triggered_signal={"signal":"ACT NOW","score":f["final_score"]})
                except Exception as e: log.error(f"log_fire_alert: {e}")
            self.root.after(0,lambda:self._render_scan(results,skipped,total,vix,fires,bp))
            # ── Position monitoring: check open trades against current TA ──
            try:
                active=self._dm.get_active_trades()
                scan_by_sym={r["symbol"]:r for r in results}
                pos_alerts=[]
                for trade in active:
                    sym=trade["stock"]; r=scan_by_sym.get(sym)
                    if not r: continue  # stock not in this scan
                    cur_price=r["price"]; entry_price=trade["avg_entry_price"]
                    atr=r.get("atr",0)
                    # Calc bars held (trading days since entry)
                    try:
                        entry_dt=dt.datetime.fromisoformat(trade["entries"][0]["timestamp"].replace("Z",""))
                        bars=max(1,(dt.datetime.now(dt.UTC).replace(tzinfo=None)-entry_dt).days)
                    except Exception: bars=1
                    # Add candle data to indicators for momentum_score
                    ind=dict(r.get("indicators",{}))
                    ms=momentum_score(entry_price,cur_price,bars,ind)
                    # Persist momentum snapshot on the trade
                    trade.setdefault("momentum_history",[])
                    trade["momentum_history"].append({
                        "date":dt.datetime.now().isoformat()[:10],
                        "score":ms["momentum_score"],"strong":ms["strong"],
                        "unrealized_pct":ms["unrealized_pct"],
                        "speed":ms["move_speed_pct_bar"]})
                    # Check proximity to stops/targets
                    se=trade.get("suggested_exits",{})
                    stop_p=se.get("stop_loss_atr",{}).get("price",0)
                    tp1_p=se.get("take_profit_1r",{}).get("price",0)
                    tp2_p=se.get("take_profit_2r",{}).get("price",0)
                    alert={"symbol":sym,"trade_id":trade["trade_id"],
                           "entry":entry_price,"current":cur_price,
                           "momentum":ms,"bars_held":bars}
                    if ms["strong"]:
                        alert["flag"]="⚡ STRONG MOMENTUM"
                    if stop_p and cur_price<=stop_p*1.02:
                        alert["flag"]="🛑 NEAR STOP"
                    if tp1_p and cur_price>=tp1_p*0.98:
                        alert.setdefault("flag","")
                        alert["flag"]=(alert["flag"]+" 🎯 NEAR TP1").strip()
                    if tp2_p and cur_price>=tp2_p*0.98:
                        alert.setdefault("flag","")
                        alert["flag"]=(alert["flag"]+" 🏆 NEAR TP2").strip()
                    if alert.get("flag"):
                        pos_alerts.append(alert)
                self._dm.save_state()  # persist momentum_history
                if pos_alerts:
                    self.root.after(0,lambda a=pos_alerts:self._position_alerts(a))
            except Exception as e: log.error(f"Position monitoring: {e}")
            # ── Auto-prompt: offer to track 80+ / ARB signals as positions ──
            try:
                active_syms={t["stock"] for t in self._dm.get_active_trades()}
                for r in results:
                    sig=r.get("signal","")
                    if not any(k in sig for k in ("BUY","ACT NOW","ARB")): continue
                    if r["symbol"] in active_syms: continue
                    if r["final_score"]>=80 or "ARB" in sig:
                        self.root.after(0,lambda d=r:self._auto_track_prompt(d))
            except Exception as e: log.error(f"Auto-prompt: {e}")
            # ── 300+ outcome gate check ──
            try:
                checked=sum(1 for s in self._tracker.signals
                            if s.get("outcomes",{}).get("7d",{}).get("checked"))
                if checked>=_MOMENTUM_BACKTEST_GATE and not getattr(self,"_momentum_gate_shown",False):
                    self._momentum_gate_shown=True
                    self.root.after(0,self._momentum_gate_alert)
            except Exception: pass
            # Barchart daily collection — scrape only symbols missing from today's cache
            missing=[r["symbol"] for r in results if not self._bc_collector.get_today(r["symbol"])]
            if missing:
                price_map={r["symbol"]:r["price"] for r in results}
                print(f"Barchart: {len(missing)} symbols to scrape, {len(results)-len(missing)} already cached")
                def _bc_bg(missing=missing,price_map=price_map):
                    self._bc_collector.scrape_all(missing,price_map,
                        callback=lambda:self.root.after(0,self._bc_post_scrape))
                threading.Thread(target=_bc_bg,daemon=True).start()
            else:
                print(f"Barchart: all {len(results)} symbols already cached for today")
        except Exception as e: log.error(f"Scan: {e}\n{traceback.format_exc()}")
        finally:
            self._scanning=False; self._temp_syms.clear()
            self.root.after(0,lambda:(self._pct.configure(text=""),self._temp_lbl.configure(text="")))

    # ─── POSITION MONITORING ALERTS ────────────────────────────

    def _position_alerts(self, alerts):
        """Show popup with momentum/proximity alerts for open positions."""
        w=tk.Toplevel(self.root); w.title("📊 Position Monitor"); w.geometry("520x500")
        w.configure(bg="white"); w.protocol("WM_DELETE_WINDOW",w.destroy)
        tk.Label(w,text="📊 OPEN POSITION ALERTS",font=("Helvetica",15,"bold"),fg=P["text"],bg="white").pack(pady=(12,4))
        tk.Label(w,text=f"{len(alerts)} position(s) flagged",font=("Helvetica",10),fg=P["muted"],bg="white").pack(pady=(0,8))
        sf=tk.Frame(w,bg="white"); sf.pack(fill="both",expand=True,padx=12)
        for a in alerts:
            af=tk.Frame(sf,bg=P["panel"],bd=1,relief="solid",padx=10,pady=8); af.pack(fill="x",pady=4)
            flag=a.get("flag","")
            ms=a["momentum"]
            # Header: symbol + flag
            hdr=tk.Frame(af,bg=P["panel"]); hdr.pack(fill="x")
            fc=P["green"] if "STRONG" in flag else P["red"] if "STOP" in flag else P["gold"] if "TP" in flag else P["text"]
            tk.Label(hdr,text=f"{a['symbol']}",font=("Helvetica",14,"bold"),fg=P["text"],bg=P["panel"]).pack(side="left")
            tk.Label(hdr,text=flag,font=("Helvetica",11,"bold"),fg=fc,bg=P["panel"]).pack(side="left",padx=8)
            # P&L line
            pnl=ms["unrealized_pct"]
            pc=P["green"] if pnl>=0 else P["red"]
            tk.Label(af,text=f"Entry: ${a['entry']:.2f}  →  ${a['current']:.2f}  ({pnl:+.1f}%)  |  {a['bars_held']}d held",
                     font=("Helvetica",10),fg=pc,bg=P["panel"]).pack(anchor="w")
            # Momentum checks
            bar="".join("██" if v[0] else "░░" for v in ms["checks"].values())
            tk.Label(af,text=f"Momentum: {bar} {ms['momentum_score']}/4  |  {ms['move_speed_pct_bar']:.2f}%/bar",
                     font=("Courier",10,"bold"),fg=P["text"],bg=P["panel"]).pack(anchor="w",pady=(4,0))
            for key,label in [("move_speed","Speed"),("volume","Volume"),("candle_quality","Candle"),("indicators","Indicators")]:
                passed,detail=ms["checks"][key]
                icon="✓" if passed else "✗"
                iclr="#22aa22" if passed else "#cc4444"
                tk.Label(af,text=f"  {icon} {label}: {detail}",font=("Courier",9),fg=iclr,bg=P["panel"]).pack(anchor="w")
            # Quick profit recommendation
            if ms["strong"] and pnl>0:
                tk.Label(af,text="→ Consider: Exit 50% now, trail stop on remainder",
                         font=("Helvetica",10,"bold"),fg=P["green"],bg=P["panel"]).pack(anchor="w",pady=(4,0))

    def _auto_track_prompt(self, data):
        """Popup to confirm/dismiss auto-tracking a high-score signal as a position."""
        sym=data["symbol"]; score=data["final_score"]; price=data["price"]
        sig=data.get("signal","BUY")
        w=tk.Toplevel(self.root); w.title(f"Track {sym}?"); w.geometry("420x180")
        w.configure(bg=P["card"]); w.protocol("WM_DELETE_WINDOW",w.destroy)
        w.attributes("-topmost",True)
        tk.Label(w,text=f"📌 {sym} — {sig} {score}",font=("Helvetica",14,"bold"),
                 fg=P["green"],bg=P["card"]).pack(pady=(16,4))
        tk.Label(w,text=f"${price:.2f}  |  Track this as an open position?",
                 font=("Helvetica",11),fg=P["text"],bg=P["card"]).pack(pady=(0,12))
        bf=tk.Frame(w,bg=P["card"]); bf.pack(fill="x",padx=20)
        tk.Button(bf,text="✓ I'M IN",font=("Helvetica",12,"bold"),fg="white",bg=P["green"],bd=0,cursor="hand2",pady=6,
                  command=lambda:(w.destroy(),self._log_trade_modal(data))).pack(side="left",fill="x",expand=True,padx=(0,6))
        tk.Button(bf,text="✗ SKIP",font=("Helvetica",12,"bold"),fg=P["muted"],bg=P["card"],bd=1,cursor="hand2",pady=6,
                  command=w.destroy).pack(side="right",fill="x",expand=True)

    def _momentum_gate_alert(self):
        """One-time banner when 300+ checked outcomes are available — time to backtest momentum."""
        checked=sum(1 for s in self._tracker.signals
                    if s.get("outcomes",{}).get("7d",{}).get("checked"))
        w=tk.Toplevel(self.root); w.title("📊 Data Milestone"); w.geometry("500x220")
        w.configure(bg=P["card"]); w.protocol("WM_DELETE_WINDOW",w.destroy)
        w.attributes("-topmost",True)
        tk.Label(w,text="📊 DATA MILESTONE REACHED",font=("Helvetica",16,"bold"),fg=P["gold"],bg=P["card"]).pack(pady=(20,4))
        tk.Label(w,text=f"{checked} checked outcomes",font=("Helvetica",13,"bold"),fg=P["text"],bg=P["card"]).pack()
        tk.Label(w,text="You now have enough data to backtest momentum effectiveness.\n\n"
                        "Recommended: Run backtest comparing strong momentum (3+/4)\n"
                        "quick exits vs normal holds. Check if setup_type or\n"
                        "confidence_score predict better outcomes.",
                 font=("Helvetica",10),fg=P["muted"],bg=P["card"],justify="left",wraplength=460).pack(pady=(8,12))
        tk.Button(w,text="Got it",font=("Helvetica",11,"bold"),fg="white",bg=P["accent"],bd=0,cursor="hand2",pady=6,
                  command=w.destroy).pack(padx=20,fill="x")

    def _bc_post_scrape(self):
        """Called after Barchart scrape completes. Refreshes Opinion column then logs correlation pairs."""
        today_data=self._bc_collector.get_today_all()
        print(f"_bc_post_scrape: {len(today_data)} symbols in today's cache — refreshing Signals tab")
        # Refresh scan table immediately so Opinion column fills in
        if self._scan_res and self._meta.get("time"):
            self._render_scan(self._scan_res,self._meta["skip"],self._meta["total"],
                              self._meta["vix"],[],self._meta.get("bp",1.0))
        def bg():
            for sym,live in today_data.items():
                try:
                    hist=yf.Ticker(sym).history(period="1y")
                    if len(hist)<200: continue
                    synth=SyntheticConsensus.compute(hist['Close'],hist['High'],hist['Low'],hist['Volume'])
                    if synth and live.get('pct') is not None:
                        self._bc_correlation.log(sym,live.get('price',0),synth['pct'],synth['signal'],live['pct'],live['signal'],live.get('trend','→'))
                except Exception as e: print(f"Synth consensus error {sym}: {e}")
            self._bc_correlation.check_outcomes()
            print(f"Barchart correlation: {len(self._bc_correlation.records)} total paired records logged")
        threading.Thread(target=bg,daemon=True).start()

    def _earnings_badge_days(self,sym):
        evt=Events.check(sym,fast=False)
        earn_date=evt.get("earn_date")
        if earn_date is None: return None
        d=(earn_date-dt.date.today()).days
        return d if 5<=d<=28 else None

    def _show_earnings_popup(self,sym):
        evt=Events._cache.get(sym,{})
        earn_date=evt.get("earn_date")
        if earn_date is None: return
        days=(earn_date-dt.date.today()).days
        w=tk.Toplevel(self.root); w.title(f"Earnings — {sym}"); w.geometry("260x130"); w.configure(bg="white")
        w.protocol("WM_DELETE_WINDOW",w.destroy)
        tk.Label(w,text=sym,font=("Helvetica",18,"bold"),fg=P["accent"],bg="white").pack(pady=(16,4))
        tk.Label(w,text=earn_date.strftime("%B %d, %Y"),font=("Helvetica",12),fg=P["text"],bg="white").pack()
        tk.Label(w,text=f"{days} days away",font=("Helvetica",12,"bold"),fg=P["gold"],bg="white").pack(pady=(4,16))

    def _render_scan(self,results,skipped,total,vix,fires,bp):
        try:
            self._upd_back_btn()
            last_scan_txt=self._dm.state.get("last_scan_metadata",{}).get("display","")
            self._last_scan_lbl.configure(text=f"Last scan: {last_scan_txt}" if last_scan_txt else "")
            for w in self._sc.winfo_children(): w.destroy()
            buys=sum(1 for r in results if any(k in r.get("signal","") for k in ("BUY","ACT NOW","ARB")))
            sells=sum(1 for r in results if "SELL" in r.get("signal",""))
            hdr=tk.Frame(self._sc,bg=P["card"],padx=10,pady=6); hdr.pack(fill="x",pady=(0,6))
            tk.Label(hdr,text=f"Scan: {len(results)} stocks | BUY:{buys} SELL:{sells} WATCH:{len(results)-buys-sells} | Below threshold: {total-len(results)-skipped} | Skip: {skipped} | Total: {total}",
                    font=("Helvetica",12,"bold"),fg=P["text"],bg=P["card"]).pack(anchor="w")
            meta=f"{self._regime.summary()} | ×{self._regime.mult(self._mode.get()):.2f}"
            if bp<1: meta+=f" | ⚠️ Breadth ×{bp:.2f}"
            tk.Label(hdr,text=meta,font=("Helvetica",10),fg=P["muted"],bg=P["card"]).pack(anchor="w")
            last_scan_txt=self._dm.state.get("last_scan_metadata",{}).get("display","")
            if last_scan_txt:
                tk.Label(hdr,text=f"Last scan: {last_scan_txt}",font=("Helvetica",9),fg=P["muted"],bg=P["card"]).pack(anchor="w")
            _ref_btn=tk.Button(hdr,text="🔄 Refresh",font=("Helvetica",10),fg=P["accent"],bg=P["card"],bd=0,cursor="hand2")
            _ref_btn.pack(side="right")
            def _refresh_sig_prices():
                _ref_btn.configure(text="Refreshing…",state="disabled")
                def bg():
                    for r in results:
                        q=fetch_quote(r["symbol"])
                        if q.get("price"): r["price"]=q["price"]
                    self._scan_res=results
                    self.root.after(0,lambda:self._render_scan(results,skipped,total,vix,[],bp))
                threading.Thread(target=bg,daemon=True).start()
            _ref_btn.configure(command=_refresh_sig_prices)
            cv=tk.Canvas(self._sc,bg="white",highlightthickness=0); sb=tk.Scrollbar(self._sc,orient="vertical",command=cv.yview)
            sf=tk.Frame(cv,bg="white"); sf.bind("<Configure>",lambda e:cv.configure(scrollregion=cv.bbox("all")))
            cv.create_window((0,0),window=sf,anchor="nw"); cv.configure(yscrollcommand=sb.set); sb.pack(side="right",fill="y"); cv.pack(fill="both",expand=True)
            ch=tk.Frame(sf,bg=P["card"],padx=6,pady=4); ch.pack(fill="x")
            for t,w in [("Symbol",8),("Price",10),("RSI",6),("Tech",6),("BT±",5),("Final",6),("Swing%",7),("ARB",5),("Signal",12),("Opinion",12)]:
                tk.Label(ch,text=t,font=("Consolas",11,"bold"),fg=P["muted"],bg=P["card"],width=w,anchor="w").pack(side="left")
            rows_built=0
            for r in results:
                rf=None
                try:
                    sig=r.get("signal",""); z=r.get("arb",{}).get("z",0)
                    rbg="#fff8e1" if "ACT NOW" in sig else "#fef9e7" if "ARB" in sig else "#f0faf0" if "BUY" in sig else "#faf0f0" if "SELL" in sig else "#efefef" if "SUPPRESSED" in sig else "white"
                    rf=tk.Frame(sf,bg=rbg,padx=6,pady=3); rf.pack(fill="x")
                    sl=tk.Label(rf,text=r["symbol"],font=("Consolas",11,"bold"),fg=P["accent"],bg=rbg,width=8,anchor="w",cursor="hand2")
                    sl.pack(side="left"); sl.bind("<Button-1>",lambda e,d=r:self._detail(d))
                    # HOTPATCH-Item53: ebdays=self._earnings_badge_days(r["symbol"])
                    # HOTPATCH-Item53: if ebdays is not None:
                        # HOTPATCH-Item53: eb=tk.Label(rf,text="E",font=("Consolas",10,"bold"),fg=P["gold"],bg=rbg,cursor="hand2")
                        # HOTPATCH-Item53: eb.pack(side="left")
                        # HOTPATCH-Item53: eb.bind("<Button-1>",lambda e,s=r["symbol"]:self._show_earnings_popup(s))
                    for v,w in [(f"${r['price']:.2f}",10),(f"{r['rsi']:.1f}",6),(str(r.get('raw_buy',0)),6),
                                (f"{r.get('bt_adj',0):+d}",5),(str(r["final_score"]),6),(f"{r['atr_swing']:.1f}%",7),(f"{z:.1f}" if z else "—",5)]:
                        tk.Label(rf,text=v,font=("Consolas",11),fg=P["text"],bg=rbg,width=w,anchor="w").pack(side="left")
                    sc=P["gold"] if "ACT NOW" in sig or "ARB" in sig else P["green"] if "BUY" in sig else P["red"] if "SELL" in sig else P["muted"]
                    tk.Label(rf,text=sig,font=("Consolas",11,"bold"),fg=sc,bg=rbg,width=12,anchor="w").pack(side="left")
                    bc=self._bc_collector.get_today(r["symbol"])
                    if bc and ("pct" in bc) and ("signal" in bc):
                        op_str=f"{bc.get('trend','→')} {bc['pct']}% {bc['signal']}"
                        op_clr=P["green"] if bc.get("signal")=="Buy" else P["red"] if bc.get("signal")=="Sell" else P["muted"]
                    else:
                        op_str="—"; op_clr=P["muted"]
                    tk.Label(rf,text=op_str,font=("Consolas",10),fg=op_clr,bg=rbg,width=12,anchor="w").pack(side="left")
                    if any(k in sig for k in ("BUY","ACT NOW","ARB")):
                        tk.Button(rf,text="📝",font=("Helvetica",10),fg=P["green"],bg=rbg,bd=0,cursor="hand2",
                                 command=lambda d=r:self._log_trade_modal(d)).pack(side="left",padx=2)
                    ef=r.get("event",{}).get("earn_flag","")
                    if ef: tk.Label(rf,text=ef,font=("Helvetica",10),fg=P["orange"],bg=rbg).pack(side="left",padx=4)
                    rows_built+=1
                except Exception as _row_err:
                    log.error(f"row render failed {r.get('symbol','?')}: {_row_err}")
                    if rf is not None:
                        try: rf.destroy()
                        except Exception: pass
                    continue
            for w in self._df2.winfo_children(): w.destroy()
            nc=[r for r in results if r["symbol"] not in CORE and any(k in r.get("signal","") for k in ("BUY","SELL","ACT NOW","ARB"))]
            self._dl.configure(text="Scan results:" if nc else "")
            for i,r in enumerate(nc[:12]):
                sig=r.get("signal",""); bg=P["gold"] if "ACT NOW" in sig or "ARB" in sig else P["green"] if "BUY" in sig else P["red"]
                tk.Button(self._df2,text=r["symbol"],font=("Helvetica",9,"bold"),fg="white",bg=bg,bd=0,cursor="hand2",width=5,
                         command=lambda d=r:self._detail(d)).grid(row=i//3,column=i%3,padx=2,pady=1,sticky="ew")
            if self._fires: self._fb.configure(text=f"🔥 {len(self._fires)}",fg=P["orange"])
            for fh in fires: self._fire_alert(fh["symbol"],fh["final_score"],fh["price"],fh["rsi"],fh.get("atr",0),fh.get("setup_profile"))
            self._nb.select(self._ts); self._ref_tracker()
        except Exception as e:
            log.error(f"_render_scan failed: {e}\n{traceback.format_exc()}")

    # ─── DETAIL POPUP (Option D) ────────────────────────

    def _detail(self,data):
        sym,price,atr,score=data["symbol"],data["price"],data.get("atr",0),data.get("final_score",0)
        sig,conds,ind,arb=data.get("signal",""),data.get("conditions",set()),data.get("indicators",{}),data.get("arb",{})
        win=tk.Toplevel(self.root); win.title(f"{sym}"); win.geometry("520x580"); win.configure(bg="white"); win.protocol("WM_DELETE_WINDOW",win.destroy)
        sc=P["gold"] if "ACT NOW" in sig or "ARB" in sig else P["green"] if "BUY" in sig else P["red"] if "SELL" in sig else P["muted"]
        hdr=tk.Frame(win,bg=P["card"],padx=12,pady=8); hdr.pack(fill="x")
        tk.Label(hdr,text=f"{sym} ${price:.2f} — {sig}",font=("Helvetica",16,"bold"),fg=sc,bg=P["card"]).pack(side="left")
        tk.Label(hdr,text=f"Score: {score}",font=("Helvetica",14,"bold"),fg=P["text"],bg=P["card"]).pack(side="right")
        if any(k in sig for k in ("BUY","ACT NOW","ARB")):
            tk.Button(hdr,text="📝 Log Trade",font=("Helvetica",10,"bold"),fg="white",bg=P["green"],bd=0,cursor="hand2",padx=8,
                     command=lambda:(win.destroy(),self._log_trade_modal(data))).pack(side="right",padx=(0,8))
        nb=ttk.Notebook(win); nb.pack(fill="both",expand=True,padx=8,pady=8)
        # Breakdown
        t1=tk.Frame(nb,bg="white"); nb.add(t1,text="📊 Breakdown")
        comp=tk.Frame(t1,bg=P["card"],padx=10,pady=6); comp.pack(fill="x",padx=8,pady=8)
        tk.Label(comp,text=f"Tech:{data.get('raw_buy',0)} + BT:{data.get('bt_adj',0):+d} = {score}",
                font=("Consolas",12,"bold"),fg=P["text"],bg=P["card"]).pack(anchor="w")
        if arb.get("is_arb"): tk.Label(comp,text=f"⚡ ARB Z={arb['z']:.1f} → +{arb['bonus']}",font=("Consolas",11,"bold"),fg=P["gold"],bg=P["card"]).pack(anchor="w")
        scroll=tk.Frame(t1,bg="white"); scroll.pack(fill="both",expand=True,padx=8)
        rules=[("RSI recovery","rsi_recovery",ind.get("RSI3",50)<=20,24,f"RSI3:{ind.get('RSI3',50):.1f}"),
               ("RSI mid","rsi_mid",30<=ind.get("RSI",50)<=45,12,f"RSI:{ind.get('RSI',50):.1f}"),
               ("MACD cross","macd_cross",ind.get("MACD_hist",0)>0 and ind.get("MACD_hist_prev",0)<=0,28,f"Hist:{ind.get('MACD_hist',0):.3f}"),
               ("EMA 9/21","ema_cross",ind.get("EMA9",0)>ind.get("EMA21",0) and ind.get("EMA9_prev",0)<=ind.get("EMA21_prev",0),26,""),
               ("Lower BB","at_lower_bb",ind.get("BB_pct",50)<20,22,f"BB%:{ind.get('BB_pct',50):.1f}"),
               ("StochRSI","stoch_oversold",ind.get("StochRSI",50)<25,18,f"St:{ind.get('StochRSI',50):.1f}"),
               ("Vol surge","vol_surge",ind.get("VolRatio",1)>1.8,12,f"Vol:{ind.get('VolRatio',1):.1f}×"),
               ("Above VWAP","above_vwap",price>ind.get("VWAP",price),6,""),
               ("OBV divergence","obv_divergence",ind.get("OBV_div_bull",False),12,"accum↑"),
               ("OBV rising","obv_rising",ind.get("OBV_slope",0)>0 and not ind.get("OBV_div_bull",False),3,""),
               ("ROC5","roc5_strong",ind.get("ROC5",0)>3,10,f"{ind.get('ROC5',0):.1f}%"),
               ("Williams %R","willr_oversold",ind.get("WillR",-50)<-80,8,f"{ind.get('WillR',-50):.1f}"),
               ("EMA aligned","ema_aligned",ind.get("EMA9",0)>ind.get("EMA21",0)>ind.get("EMA50",0)>0,5,""),
               ("RSI divergence","rsi_divergence",ind.get("RSI_div",False),22,"price↓ RSI↑"),
               ("Fib 38.2%","fib_382",ind.get("Fib_near_382",False),10,f"${ind.get('Fib_382',0):.2f}" if ind.get("Fib_382") else ""),
               ("Fib 50%","fib_50",ind.get("Fib_near_50",False),6,f"${ind.get('Fib_50',0):.2f}" if ind.get("Fib_50") else ""),
               ("CMF inflow","cmf_positive",ind.get("CMF",0)>0.10,4,f"CMF:{ind.get('CMF',0):+.3f}"),
               ("CMF outflow","cmf_negative_filter",ind.get("CMF",0)<-0.10 and not ind.get("RSI_div",False),-8,f"CMF:{ind.get('CMF',0):+.3f}")]
        for name,key,fired,pts,detail in rules:
            rf=tk.Frame(scroll,bg="white"); rf.pack(fill="x",pady=1)
            ic,iclr=("✓",P["green"]) if fired else ("✗",P["muted"])
            tk.Label(rf,text=ic,font=("Consolas",12,"bold"),fg=iclr,bg="white",width=2).pack(side="left")
            tk.Label(rf,text=f"{'★ ' if key in PRIMARY else ''}{name}",font=("Consolas",11),fg=P["text"] if fired else P["muted"],bg="white",width=18,anchor="w").pack(side="left")
            tk.Label(rf,text=detail,font=("Consolas",10),fg=P["muted"],bg="white").pack(side="left")
            tk.Label(rf,text=f"{pts:+d}" if fired else "—",font=("Consolas",11,"bold"),fg=iclr,bg="white",width=4,anchor="e").pack(side="right")
        hp=bool(conds&PRIMARY) if isinstance(conds,set) else False
        sp=bool(conds&STRONG_PRIMARY) if isinstance(conds,set) else False
        nc2=len(conds) if isinstance(conds,set) else 0; sw=data.get("atr_swing",0)
        ok=hp and sp and nc2>=3 and sw>=4
        tk.Label(scroll,text=f"{'✓' if hp else '✗'} Primary | {'✓' if sp else '✗'} Strong≥24pt | {'✓' if nc2>=3 else '✗'} 3+conds({nc2}) | {'✓' if sw>=4 else '✗'} Swing≥4%({sw:.1f}%)",
                font=("Consolas",10),fg=P["green"] if ok else P["red"],bg="white").pack(anchor="w",pady=(8,0))
        # Trade card
        t2=tk.Frame(nb,bg="white"); nb.add(t2,text="📋 Trade card")
        tc2=tk.Frame(t2,bg="white",padx=8,pady=8); tc2.pack(fill="both",expand=True)
        stype="BUY" if any(k in sig for k in ("BUY","ACT NOW","ARB")) else "SELL" if "SELL" in sig else "WATCH"
        trade_card(tc2,price,atr,score,stype,sym,evt=data.get("event",{}))
        # Setup Profile tab
        t5=tk.Frame(nb,bg="white"); nb.add(t5,text="⚙️ Setup")
        sp_scroll=tk.Frame(t5,bg="white"); sp_scroll.pack(fill="both",expand=True,padx=4,pady=4)
        _render_setup_profile(sp_scroll,data.get("setup_profile"),bg="white")
        # Load chart
        t3=tk.Frame(nb,bg="white"); nb.add(t3,text="📈 Chart")
        lc=tk.Frame(t3,bg="white"); lc.pack(expand=True)
        tk.Label(lc,text=f"Load chart + backtest for {sym}",font=("Helvetica",14),fg=P["text"],bg="white").pack(pady=(40,16))
        tk.Button(lc,text=f"▶ ANALYZE {sym}",font=("Helvetica",14,"bold"),fg="white",bg=P["accent"],bd=0,cursor="hand2",padx=20,pady=10,
                 command=lambda:(win.destroy(),self._pick(sym))).pack()
        # Consensus tab
        t4=tk.Frame(nb,bg="white"); nb.add(t4,text="🔍 Consensus")
        cf=tk.Frame(t4,bg="white",padx=12,pady=12); cf.pack(fill="both",expand=True)
        bc=self._bc_collector.get_today(sym)
        def _row(label,val,clr=P["text"]):
            r=tk.Frame(cf,bg="white"); r.pack(fill="x",pady=2)
            tk.Label(r,text=label,font=("Consolas",11),fg=P["muted"],bg="white",width=18,anchor="w").pack(side="left")
            tk.Label(r,text=val,font=("Consolas",11,"bold"),fg=clr,bg="white").pack(side="left")
        if bc:
            bc_clr=P["green"] if bc.get("signal")=="Buy" else P["red"] if bc.get("signal")=="Sell" else P["muted"]
            _row("Barchart Live:",f"{bc.get('trend','→')} {bc['pct']}% {bc['signal']}",bc_clr)
            if bc.get("yesterday"): _row("Yesterday:",bc["yesterday"])
            if bc.get("last_week"): _row("Last Week:",bc["last_week"])
            if bc.get("last_month"): _row("Last Month:",bc["last_month"])
        else:
            tk.Label(cf,text="No Barchart data yet — runs after scan completes",font=("Helvetica",11),fg=P["muted"],bg="white").pack(pady=4)
        ttk.Separator(cf).pack(fill="x",pady=8)
        synth_lbl=tk.Label(cf,text="Synthetic: loading…",font=("Consolas",11),fg=P["muted"],bg="white"); synth_lbl.pack(anchor="w")
        detail_lbl=tk.Label(cf,text="",font=("Consolas",9),fg=P["muted"],bg="white",wraplength=380,justify="left"); detail_lbl.pack(anchor="w",pady=(2,0))
        def _load_synth():
            try:
                hist=yf.Ticker(sym).history(period="1y")
                if len(hist)<200: synth_lbl.configure(text="Synthetic: insufficient data (<200 bars)"); return
                synth=SyntheticConsensus.compute(hist['Close'],hist['High'],hist['Low'],hist['Volume'])
                if synth:
                    sc2=P["green"] if synth["signal"]=="Buy" else P["red"] if synth["signal"]=="Sell" else P["muted"]
                    synth_lbl.configure(text=f"Synthetic:       {synth['pct']}% {synth['signal']}  ({synth['bull']}↑ {synth['bear']}↓ {synth['neutral']}→)",fg=sc2)
                    detail_lbl.configure(text="  ".join(f"{k}:{v[0].upper()}" for k,v in synth['detail'].items()))
                    if bc and bc.get("pct") is not None:
                        div=synth['pct']-bc['pct']
                        div_lbl=tk.Label(cf,text=f"Divergence:      {'+' if div>0 else ''}{div} pts {'← synth stronger' if div>5 else '← live stronger' if div<-5 else '(aligned)'}",
                                         font=("Consolas",11),fg=P["orange"] if abs(div)>10 else P["text"],bg="white")
                        div_lbl.pack(anchor="w",pady=(4,0))
                else: synth_lbl.configure(text="Synthetic: could not compute")
            except Exception as ex: synth_lbl.configure(text=f"Synthetic: error — {ex}")
        threading.Thread(target=lambda:cf.after(0,_load_synth) if cf.winfo_exists() else None,daemon=True).start()
        cf.after(100,_load_synth)

    # ─── REGIME RECALC ──────────────────────────────────

    def _recalc(self):
        if not self._scan_res: return
        m=self._regime.mult(self._mode.get())
        for r in self._scan_res:
            r["buy_score"]=r.get("raw_buy",0)+r.get("extra_bonus",0)
            r["sell_score"]=r.get("raw_sell",0)
        self._scan_res, bp, buy_count = score_and_assign(self._scan_res, m, self._dm)
        self._scan_res.sort(key=lambda r:(0 if "ACT NOW" in r["signal"] else 1 if "ARB" in r["signal"] else 2 if "BUY" in r["signal"] else 3 if "SELL" in r["signal"] else 4,-r["final_score"]))
        self._render_scan(self._scan_res,self._meta["skip"],self._meta["total"],self._meta["vix"],[],bp)

    # ─── SL/TP MONITOR ───────────────────────────────────

    def _monitor_loop(self):
        """Daemon thread: check active trades against SL/TP every 30s during market hours (9:30–18:30 ET weekdays)."""
        import pytz
        et=pytz.timezone("America/New_York")
        while not self._closing:
            try:
                cfg=self._dm.get_monitor_config()
                if not cfg.get("enabled",False): break
                now_et=dt.datetime.now(et)
                wkd=now_et.weekday()<5
                h,m=now_et.hour,now_et.minute
                end_h,end_m=(int(x) for x in cfg.get("end_et","18:30").split(":"))
                in_hours=wkd and (h>9 or (h==9 and m>=30)) and (h<end_h or (h==end_h and m<=end_m))
                if in_hours:
                    trades=self._dm.get_active_trades()
                    for trade in trades:
                        sym=trade["stock"]; trade_id=trade["trade_id"]
                        exits=trade.get("suggested_exits",{})
                        sl=exits.get("stop_loss_atr",{}).get("price")
                        tp1=exits.get("take_profit_1r",{}).get("price")
                        tp2=exits.get("take_profit_2r",{}).get("price")
                        if not (sl or tp1 or tp2): continue
                        q=fetch_quote(sym); cur=q.get("price")
                        if not cur: continue
                        # E0-live: update excursion telemetry on this active trade
                        try:
                            self._dm.update_active_trade_excursion(trade_id, cur)
                        except Exception as e:
                            log.error(f"E0-live update_active_trade_excursion: {e}")
                        if sl and cur<=sl and (trade_id,"SL") not in self._monitor_alerted:
                            self._monitor_alerted.add((trade_id,"SL"))
                            self.root.after(0,lambda s=sym,p=cur,lv=sl,t=trade_id:self._sltp_alert(s,p,"STOP LOSS",lv,t))
                        elif tp2 and cur>=tp2 and (trade_id,"TP2") not in self._monitor_alerted:
                            self._monitor_alerted.add((trade_id,"TP2"))
                            self.root.after(0,lambda s=sym,p=cur,lv=tp2,t=trade_id:self._sltp_alert(s,p,"TAKE PROFIT 2R",lv,t))
                        elif tp1 and cur>=tp1 and (trade_id,"TP1") not in self._monitor_alerted:
                            self._monitor_alerted.add((trade_id,"TP1"))
                            self.root.after(0,lambda s=sym,p=cur,lv=tp1,t=trade_id:self._sltp_alert(s,p,"TAKE PROFIT 1R",lv,t))
            except Exception as e: log.error(f"monitor_loop: {e}")
            interval=self._dm.get_monitor_config().get("interval_sec",30)
            time.sleep(interval)

    def _sltp_alert(self,sym,price,alert_type,level,trade_id):
        try: subprocess.Popen(["notify-send","-u","critical","⚠️ SL/TP Alert",f"{sym} {alert_type} @ ${level:.2f}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except Exception: pass
        w=tk.Toplevel(self.root); w.title(f"⚠️ {sym} {alert_type}"); w.geometry("420x200"); w.configure(bg="#1a1a2e"); w.protocol("WM_DELETE_WINDOW",w.destroy)
        tk.Label(w,text=f"⚠️ {alert_type}",font=("Helvetica",20,"bold"),fg=P["gold"],bg="#1a1a2e").pack(pady=(16,4))
        tk.Label(w,text=f"{sym}  Current: ${price:.2f}  Level: ${level:.2f}",font=("Helvetica",13,"bold"),fg="white",bg="#1a1a2e").pack(pady=(0,12))
        br=tk.Frame(w,bg="#1a1a2e"); br.pack(fill="x",padx=16,pady=8)
        tk.Button(br,text="▶ CHART",font=("Helvetica",11,"bold"),fg="white",bg=P["green"],bd=0,pady=6,
                 command=lambda:(w.destroy(),self._pick(sym))).pack(side="left",fill="x",expand=True,padx=(0,4))
        tk.Button(br,text="✕ DISMISS",font=("Helvetica",11,"bold"),fg="white",bg="#555",bd=0,pady=6,
                 command=w.destroy).pack(side="right",fill="x",expand=True,padx=(4,0))
        try: self._dm.log_fire_alert(sym,alert_type,{"price":price,"level":level},trade_id=trade_id)
        except Exception: pass

    # ─── FIRE / HOME / REGIME ────────────────────────────

    def _fire_alert(self,sym,score,price,rsi,atr,setup_profile=None):
        geo="480x780" if setup_profile else "480x520"
        w=tk.Toplevel(self.root); w.title(f"🔥 {sym}"); w.geometry(geo); w.configure(bg="#4a0000"); w.protocol("WM_DELETE_WINDOW",w.destroy)
        try: subprocess.Popen(["notify-send","-u","critical","🔥 Michael Swing Trader",f"{sym} {score} ${price:.2f}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except Exception: pass
        tk.Label(w,text="🔥 ACT NOW",font=("Helvetica",24,"bold"),fg="#ff6600",bg="#4a0000").pack(pady=(16,4))
        tk.Label(w,text=f"{sym} — {score} — ${price:.2f} — RSI {rsi:.1f}",font=("Helvetica",14,"bold"),fg="white",bg="#4a0000").pack(pady=(0,12))
        cf=tk.Frame(w,bg="#4a0000"); cf.pack(fill="x",padx=16); trade_card(cf,price,atr,score,"BUY",sym,evt=None)
        if setup_profile:
            spf=tk.Frame(w,bg="#2a0000",padx=4); spf.pack(fill="x",padx=16,pady=(4,0))
            _render_setup_profile(spf,setup_profile,bg="#2a0000")
        br=tk.Frame(w,bg="#4a0000"); br.pack(fill="x",padx=16,pady=12)
        tk.Button(br,text="▶ CHART",font=("Helvetica",12,"bold"),fg="white",bg=P["green"],bd=0,pady=6,
                 command=lambda:(w.destroy(),self._pick(sym))).pack(side="left",fill="x",expand=True,padx=(0,4))
        tk.Button(br,text="✕ DISMISS",font=("Helvetica",12,"bold"),fg="white",bg="#666",bd=0,pady=6,
                 command=w.destroy).pack(side="right",fill="x",expand=True,padx=(4,0))

    def _fire_hist(self):
        w=tk.Toplevel(self.root); w.title("🔥 History"); w.geometry("500x400"); w.configure(bg="white"); w.protocol("WM_DELETE_WINDOW",w.destroy)
        tk.Label(w,text="🔥 Fire History",font=("Helvetica",16,"bold"),fg=P["text"],bg="white").pack(pady=10)
        def _open_profile(f):
            pw=tk.Toplevel(self.root); pw.title(f"🔥 {f['symbol']} — Setup Profile")
            pw.configure(bg="white"); pw.protocol("WM_DELETE_WINDOW",pw.destroy)
            spf=tk.Frame(pw,bg="white",padx=8,pady=8); spf.pack(fill="both",expand=True)
            _render_setup_profile(spf,f.get("setup_profile"),bg="white")
        for fa in reversed(self._fires):
            rf=tk.Frame(w,bg="#fff8e1",padx=10,pady=6); rf.pack(fill="x",padx=10,pady=2)
            sym_lbl=tk.Label(rf,text=f"🔥 {fa['symbol']}",font=("Helvetica",12,"bold"),fg=P["accent"],bg="#fff8e1",cursor="hand2")
            sym_lbl.pack(side="left")
            sym_lbl.bind("<Button-1>",lambda e,s=fa["symbol"]:(w.destroy(),self._pick(s)))
            tk.Label(rf,text=f"  Score:{fa['score']} ${fa['price']:.2f} {fa['time']}",
                    font=("Helvetica",12,"bold"),fg=P["orange"],bg="#fff8e1").pack(side="left")
            tk.Button(rf,text="📋 Profile",font=("Helvetica",10),fg=P["text"],bg="#fff8e1",bd=0,cursor="hand2",
                     command=lambda f=fa:_open_profile(f)).pack(side="right")

    def _home(self):
        w=tk.Toplevel(self.root); w.title("🏠"); w.geometry("600x700"); w.configure(bg="white"); w.protocol("WM_DELETE_WINDOW",w.destroy)
        tk.Label(w,text="🏠 Scan Results",font=("Helvetica",16,"bold"),fg=P["text"],bg="white").pack(pady=10)
        if self._meta["time"]: tk.Label(w,text=f"Last: {self._meta['time']}",font=("Helvetica",10),fg=P["muted"],bg="white").pack()
        tk.Button(w,text="⚡ RESCAN",font=("Helvetica",11,"bold"),fg="white",bg=P["accent"],bd=0,command=lambda:(w.destroy(),self._scan())).pack(pady=8)
        for r in self._scan_res:
            sig=r.get("signal",""); sc=P["gold"] if "ACT NOW" in sig or "ARB" in sig else P["green"] if "BUY" in sig else P["red"] if "SELL" in sig else P["muted"]
            rf=tk.Frame(w,bg="white",padx=8,pady=4); rf.pack(fill="x")
            tk.Button(rf,text=r["symbol"],font=("Helvetica",12,"bold"),fg=sc,bg="white",bd=0,cursor="hand2",width=7,
                     command=lambda s=r["symbol"]:(w.destroy(),self._pick(s))).pack(side="left")
            tk.Label(rf,text=f"${r['price']:.2f} Score:{r['final_score']} {sig}",font=("Helvetica",11),fg=sc,bg="white").pack(side="left",padx=8)

    def _regime_popup(self):
        w=tk.Toplevel(self.root); w.title("Regime"); w.geometry("450x300"); w.configure(bg="white"); w.protocol("WM_DELETE_WINDOW",w.destroy)
        lbl,clr=Regime.LABELS.get(self._regime.state,("⚪",P["muted"]))
        tk.Label(w,text=lbl,font=("Helvetica",22,"bold"),fg=clr,bg="white").pack(pady=16)
        for t in [f"SPY {'above' if self._regime.spy_above else 'below'} 50MA ({self._regime.spy_pct:+.1f}%)",
                  f"VIX: {self._regime.vix:.1f} {'↑rising' if self._regime.vix_rising else '↓falling'}",
                  f"Mode: {self._mode.get().capitalize()}",f"×{self._regime.mult(self._mode.get()):.2f}"]:
            tk.Label(w,text=t,font=("Helvetica",13),fg=P["text"],bg="white").pack(padx=20,pady=2)

    def _upd_regime(self):
        l,c=Regime.LABELS.get(self._regime.state,("⚪",P["muted"])); self._rl.configure(text=l,fg=c)
        # Auto-switch to Conservative when VIX >= 30
        if self._regime.vix >= 30 and not self._vix_forced:
            self._vix_forced=True
            self._mode.set("conservative")
            self._vix_warn.configure(text=f"⚠️ VIX {self._regime.vix:.0f} — AUTO CONSERVATIVE")
            if self._scan_res: self._recalc()
        elif self._regime.vix < 28 and self._vix_forced:
            # Restore to normal when VIX drops back (28 = hysteresis to prevent flipping)
            self._vix_forced=False
            self._mode.set("normal")
            self._vix_warn.configure(text="")
            if self._scan_res: self._recalc()
        elif self._vix_forced:
            self._vix_warn.configure(text=f"⚠️ VIX {self._regime.vix:.0f} — AUTO CONSERVATIVE")

    # ─── TRACKER ────────────────────────────────────────

    def _check_oc(self):
        def bg(): n=self._tracker.check_outcomes(); self.root.after(0,self._ref_tracker); self.root.after(0,lambda:messagebox.showinfo("Tracker",f"Updated {n}."))
        threading.Thread(target=bg,daemon=True).start()

    def _ref_tracker(self):
        for w in self._trs.winfo_children(): w.destroy()
        tk.Label(self._trs,text=f"Signals: {len(self._tracker.signals)}",font=("Helvetica",12,"bold"),fg=P["text"],bg="white").pack(anchor="w")
        for regime,data in self._tracker.stats_by(lambda c:c.get("regime","normal")).items():
            clr=P["green"] if data["wr"]>=50 else P["red"]
            tk.Label(self._trs,text=f"  {Regime.LABELS.get(regime,(regime,))[0]}: {data['total']} sig, {data['wr']:.0f}% win",
                    font=("Helvetica",11),fg=clr,bg="white").pack(anchor="w")
        trends=self._tracker.detect_trends()
        if trends:
            ttk.Separator(self._trs).pack(fill="x",pady=(8,4))
            tk.Label(self._trs,text="Trends:",font=("Helvetica",11,"bold"),fg=P["text"],bg="white").pack(anchor="w")
            for t in trends:
                ic,clr=("▲",P["green"]) if t["type"]=="positive" else ("▼",P["red"]) if t["type"]=="negative" else ("●",P["muted"])
                tf=tk.Frame(self._trs,bg="white"); tf.pack(fill="x",pady=1)
                tk.Label(tf,text=ic,font=("Helvetica",11,"bold"),fg=clr,bg="white",width=2).pack(side="left")
                tk.Label(tf,text=t["text"],font=("Helvetica",11),fg=clr,bg="white",wraplength=700,justify="left").pack(side="left")
        self._trt.configure(state="normal"); self._trt.delete("1.0","end")
        self._trt.insert("end","SIGNAL LOG\n"+"─"*80+"\n","hdr")
        for sig in reversed(self._tracker.signals[-50:]):
            self._trt.insert("end",f"{sig['date'][:16]} {sig['symbol']:<6} ${sig['price']:<8.2f} Score:{sig['score']:<4} {sig.get('regime','')}\n")
            for p in ("7d","14d","21d"):
                o=sig.get("outcomes",{}).get(p,{})
                self._trt.insert("end",f"  {p}: {o['change_pct']:+.1f}%\n" if o.get("checked") else f"  {p}: pending\n",
                                "win" if o.get("checked") and o["change_pct"]>0 else "loss" if o.get("checked") else "muted")
            self._trt.insert("end","\n")
        self._trt.configure(state="disabled")

    # ─── ANALYTICS ──────────────────────────────────────

    def _query(self):
        sigs=self._tracker.signals
        if not sigs: return
        syms=sorted(set(s["symbol"] for s in sigs)); m=self._aqsm["menu"]; m.delete(0,"end")
        m.add_command(label="All",command=lambda:self._aq["stock"].set("All"))
        for s in syms: m.add_command(label=s,command=lambda s=s:self._aq["stock"].set(s))
        fv={k:v.get() for k,v in self._aq.items()}; dm={"Mon":0,"Tue":1,"Wed":2,"Thu":3,"Fri":4}
        filtered=[]
        for s in sigs:
            if fv["regime"]!="All" and s.get("regime")!=fv["regime"]: continue
            sc=s.get("score",0)
            if fv["score"]!="All":
                if fv["score"]=="72-79" and not 72<=sc<=79: continue
                if fv["score"]=="80-84" and not 80<=sc<=84: continue
                if fv["score"]=="85-89" and not 85<=sc<=89: continue
                if fv["score"]=="90+" and sc<90: continue
            vx=s.get("vix",20)
            if fv["vix"]!="All":
                if fv["vix"]=="< 15" and vx>=15: continue
                if fv["vix"]=="15-20" and not 15<=vx<20: continue
                if fv["vix"]=="20-25" and not 20<=vx<25: continue
                if fv["vix"]=="25-30" and not 25<=vx<30: continue
                if fv["vix"]=="> 30" and vx<=30: continue
            if fv["cond"]!="All" and fv["cond"] not in s.get("conditions",[]): continue
            if fv["stock"]!="All" and s.get("symbol")!=fv["stock"]: continue
            if fv["day"]!="All":
                try:
                    if dt.datetime.fromisoformat(s["date"]).weekday()!=dm.get(fv["day"],-1): continue
                except Exception: continue
            if fv["setup_type"]!="All":
                want=fv["setup_type"].lower().replace(" ","_")
                if s.get("setup_type","")!=want: continue
            if fv["confidence"]!="All":
                cs=s.get("confidence_score")
                if cs is None: continue
                if fv["confidence"]=="0-1 (Low)" and not 0<=cs<=1: continue
                if fv["confidence"]=="2-3 (Moderate)" and not 2<=cs<=3: continue
                if fv["confidence"]=="4-5 (High)" and cs<4: continue
            filtered.append(s)
        checked=[s for s in filtered if s.get("outcomes",{}).get("7d",{}).get("checked")]
        for w in self._aqc.winfo_children(): w.destroy()
        self._aqt.configure(state="normal"); self._aqt.delete("1.0","end")
        af=[f"{k}={v}" for k,v in fv.items() if v!="All"]
        self._aqt.insert("end",f"Query: {' + '.join(af) or 'All'}\nMatched: {len(filtered)} ({len(checked)} checked)\n\n","hdr")
        if not checked: self._aqt.insert("end","No checked outcomes.\n","muted"); self._aqt.configure(state="disabled"); return
        chgs=[s["outcomes"]["7d"]["change_pct"] for s in checked]; w=[c for c in chgs if c>0]
        wr=len(w)/len(checked)*100; avg=np.mean(chgs)
        for lbl,val,clr in [("Win%",f"{wr:.0f}%",P["green"] if wr>=50 else P["red"]),("Avg",f"{avg:+.1f}%",P["green"] if avg>0 else P["red"]),
                             ("N",str(len(checked)),P["text"]),("Best",f"{max(chgs):+.1f}%",P["green"]),("Worst",f"{min(chgs):+.1f}%",P["red"])]:
            cd=tk.Frame(self._aqc,bg=P["card"],padx=12,pady=8); cd.pack(side="left",padx=(0,6),fill="x",expand=True)
            tk.Label(cd,text=lbl,font=("Helvetica",9),fg=P["muted"],bg=P["card"]).pack(anchor="w")
            tk.Label(cd,text=val,font=("Helvetica",16,"bold"),fg=clr,bg=P["card"]).pack(anchor="w")
        self._aqt.insert("end",f"[{_conf(len(checked))}]\n\n","accent")
        if len(checked)>=3:
            cp=defaultdict(lambda:{"w":0,"t":0})
            for s in checked:
                win=s["outcomes"]["7d"]["change_pct"]>0
                for c in s.get("conditions",[]): cp[c]["t"]+=1; cp[c]["w"]+=win
            rated={c:{**s,"wr":s["w"]/s["t"]*100} for c,s in cp.items() if s["t"]>=2}
            if rated:
                self._aqt.insert("end","Conditions:\n","hdr")
                for c,s in sorted(rated.items(),key=lambda x:-x[1]["wr"]):
                    self._aqt.insert("end",f"  {CN.get(c,c):<22} {s['wr']:5.0f}% {s['w']}/{s['t']}\n","win" if s["wr"]>=55 else "loss" if s["wr"]<40 else "")
        self._aqt.insert("end","\nSignals:\n","hdr")
        for s in reversed(checked[-30:]):
            o=s["outcomes"]["7d"]
            self._aqt.insert("end",f"  {s['date'][:16]} {s['symbol']:<6} sc:{s['score']} {o['change_pct']:+.1f}% {s.get('regime','')}\n",
                            "win" if o["change_pct"]>0 else "loss")
        self._aqt.configure(state="disabled")

    # ─── EXPORT ──────────────────────────────────────────

    def _export(self):
        if self._df is None: messagebox.showinfo("","Analyze first."); return
        p=filedialog.asksaveasfilename(defaultextension=".csv",initialfile=f"michael_swing_{self._sym.get()}_{dt.date.today()}.csv")
        if p:
            try: self._df.to_csv(p); messagebox.showinfo("OK",f"Saved {p}")
            except Exception as e: messagebox.showerror("Error",str(e))

    # ─── TICK ────────────────────────────────────────────

    def _et(self): return dt.datetime.now(dt.timezone(dt.timedelta(hours=-4)))

    def _tick(self):
        if self._closing: return
        now=self._et(); h,m,wd=now.hour,now.minute,now.weekday(); today=now.strftime("%Y-%m-%d")
        if self._auto_today and not any(today in s for s in self._auto_today): self._auto_today.clear()
        wkd=wd<5; mkt=wkd and ((h==9 and m>=30) or 10<=h<16); pre=wkd and (4<=h<9 or (h==9 and m<30)); ah=wkd and 16<=h<20
        if mkt:
            ml=(16-h)*60-m; hl,ml2=divmod(max(ml,0),60)
            ct,cc=f"🟢 MARKET OPEN — {hl}h {ml2}m left",P["green"]
        elif pre: mt=9*60+30-h*60-m; ht,mt2=divmod(max(mt,0),60); ct,cc=f"Pre-market — opens {ht}h {mt2}m",P["yellow"]
        elif ah: ct,cc="After hours",P["muted"]
        elif wkd and h<4: mt=9*60+30-h*60-m; ht,mt2=divmod(max(mt,0),60); ct,cc=f"Overnight — opens {ht}h {mt2}m",P["muted"]
        elif wkd: ct,cc="After hours",P["muted"]
        else: ct,cc="Weekend",P["muted"]
        self._clk.configure(text=ct,fg=cc)
        if self._auto.get() and wkd and not self._scanning:
            cm=h*60+m
            for name,s,e in [("open",9*60+35,9*60+40),("close",16*60+5,16*60+10),("ah",18*60,18*60+5)]:
                key=f"{today}_{name}"
                if s<=cm<=e and key not in self._auto_today: self._auto_today.add(key); self._scan(); break
        if wkd and h==16 and 30<=m<=35 and self._oc_date!=today:
            self._oc_date=today
            is_friday = dt.date.today().weekday() == 4
            def _nightly_bg(run_bt=is_friday):
                self._tracker.check_outcomes()
                self._dm.recompute_if_dirty()
                self.root.after(0,self._ref_tracker)
                if run_bt:
                    try:
                        cache_date, raw_data = _bt_load_ohlcv()
                        today_iso = dt.date.today().isoformat()
                        if not raw_data or cache_date != today_iso:
                            raw_data = _bt_fetch_all(UNIVERSE)
                            _bt_save_ohlcv(raw_data)
                        stats = _bt_run_default(raw_data)
                        stats["date"] = today_iso
                        self._dm.state["last_backtest"] = stats
                        self._dm.save_state()
                        self.root.after(0, self._refresh_bt_health)
                    except Exception:
                        pass
            threading.Thread(target=_nightly_bg,daemon=True).start()
        if not self._regime.last_update or (dt.datetime.now()-self._regime.last_update).total_seconds()>CFG.regime_refresh*60:
            threading.Thread(target=lambda:(self._regime.assess(),self.root.after(0,self._upd_regime)),daemon=True).start()
        # Health check every 5 min (on minutes 0/5/10/.../55)
        if hasattr(self,"_health_mon") and m % 5 == 0:
            self._refresh_health_dot()
        self.root.after(60_000,self._tick)

    def _refresh_health_dot(self):
        worst = self._health_mon.run_all()
        colors = {"green": "#00aa00", "yellow": "#cc8800", "red": "#cc0000"}
        if hasattr(self,"_health_dot") and self._health_dot.winfo_exists():
            self._health_dot.configure(fg=colors.get(worst, "#888"))

    def _health_popup(self):
        if not hasattr(self,"_health_mon"): return
        # Force refresh on click so popup shows current state
        self._refresh_health_dot()
        t=tk.Toplevel(self.root); t.title("System Health"); t.geometry("520x380")
        t.configure(bg="white")
        tk.Label(t,text="System Health Check",font=("Helvetica",14,"bold"),bg="white").pack(pady=(12,4))
        last=self._health_mon.last_check
        ts_str = last.strftime("%Y-%m-%d %H:%M:%S") if last else "never"
        tk.Label(t,text=f"Last check: {ts_str}",font=("Helvetica",10),fg="#666",bg="white").pack()
        f=tk.Frame(t,bg="white"); f.pack(fill="both",expand=True,padx=16,pady=12)
        colors = {"green": "#00aa00", "yellow": "#cc8800", "red": "#cc0000"}
        for name, (status, detail) in self._health_mon.results.items():
            row=tk.Frame(f,bg="white"); row.pack(fill="x",pady=3)
            tk.Label(row,text="●",fg=colors.get(status,"#888"),font=("Helvetica",14),bg="white").pack(side="left",padx=(0,6))
            tk.Label(row,text=name,font=("Helvetica",11,"bold"),bg="white",width=12,anchor="w").pack(side="left")
            tk.Label(row,text=detail,font=("Helvetica",10),fg="#333",bg="white",anchor="w",wraplength=340,justify="left").pack(side="left",fill="x",expand=True)

    def _close(self):
        self._closing=True
        try: plt.close("all")
        except Exception: pass
        for w in self.root.winfo_children():
            if isinstance(w,tk.Toplevel):
                try: w.destroy()
                except Exception: pass
        self.root.quit(); self.root.destroy()

if __name__=="__main__":
    root=tk.Tk(); App(root); root.mainloop()
