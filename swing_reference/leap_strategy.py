import yfinance as yf
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Must be before pyplot import — prevents thread crashes
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import io
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
from datetime import datetime, time as dtime, timedelta
import zoneinfo
import threading
import time
import requests
from bs4 import BeautifulSoup
import re
from leap_scoring import score_leap, PILLAR_MAX, MAX_SCORE, STRONG_THRESHOLD, MONITOR_THRESHOLD

# ─────────────────────────────────────────────────────────────────────────────
#  Universe Management
# ─────────────────────────────────────────────────────────────────────────────

CORE_TICKERS = ['CRWD','ORCL','SNOW','SSYS','LMND','PLTR','BMNR','TSLA',
                'NVDA','GRNY','DE','MU','NVMI','SOFI','HOOD','NOW']

# Full discovery universe for Scan All — NOT used by daily LEAP scan.
# Daily scan uses CORE_TICKERS only. FULL_UNIVERSE is wired to nothing until
# the Scan All button is implemented (Track 2 Step 3 in roadmap).
FULL_UNIVERSE = [
    'AA','AAPL','ABBV','ABNB','ADBE','AFRM','AI','AMAT','AMD','AMGN','AMZN',
    'ARM','ASML','AVGO','AXP','BA','BAC','BKNG','BLK','BMY','BMNR','C','CAT',
    'CDNS','CEG','CLSK','CMCSA','COIN','COP','COST','CRM','CRWD','CSCO','CVX',
    'DASH','DDOG','DE','DELL','DIS','DKNG','DXCM','ENPH','F','FCX','FDX',
    'FSLR','FTNT','GE','GILD','GM','GOOGL','GS','GRNY','HD','HON','HOOD',
    'HPE','HUBS','IBM','ILMN','INTC','IONQ','ISRG','JNJ','JPM','KLAC','KO',
    'LCID','LLY','LMND','LMT','LOW','LRCX','LULU','LYFT','MA','MARA','MCD',
    'MCHP','MDB','META','MO','MRK','MRNA','MRVL','MS','MSFT','MSTR','MU',
    'NEE','NEM','NET','NFLX','NKE','NOC','NOW','NVDA','NVMI','ON','ORCL',
    'OXY','PANW','PATH','PENN','PEP','PFE','PLTR','PM','PYPL','QCOM','RBLX',
    'REGN','RGTI','RIOT','RIVN','ROKU','ROST','RTX','SBUX','SCHW','SHOP',
    'SLB','SMCI','SNOW','SNPS','SOFI','SPOT','SSYS','SWKS','T','TEAM','TGT',
    'TJX','TMO','TSLA','TSM','TTD','TXN','U','UBER','UNH','UPS','UPST','V',
    'VEEV','VRTX','VST','VZ','WDAY','WFC','WMT','WOLF','XOM','ZM','ZS',
]  # 155 tickers — excludes XYZ (not a real ticker). Includes all CORE_TICKERS.

_UNI_PATH = os.path.expanduser("~/.michael_leap_universe.json")

def _load_universe():
    added, removed = [], []
    try:
        if os.path.exists(_UNI_PATH):
            d = json.load(open(_UNI_PATH))
            added = d.get("added", [])
            removed = d.get("removed", [])
    except Exception: pass
    return [s for s in CORE_TICKERS if s not in removed] + [s for s in added if s not in CORE_TICKERS]

def _save_universe(added, removed):
    try: json.dump({"added": sorted(added), "removed": sorted(removed)}, open(_UNI_PATH, "w"), indent=2)
    except Exception: pass

TICKERS = _load_universe()

MARKET_INSTRUMENTS = [
    ('^DJI',    'Dow Jones'),
    ('^IXIC',   'Nasdaq'),
    ('^GSPC',   'S&P 500'),
    ('^VIX',    'VIX'),
    ('BTC-USD', 'Bitcoin'),
    ('ETH-USD', 'Ethereum'),
    ('KAS-USD', 'Kaspa'),
]

# ─────────────────────────────────────────────────────────────────────────────
#  Recommendation Tracker
# ─────────────────────────────────────────────────────────────────────────────

_REC_PATH  = os.path.expanduser("~/.michael_leap_recommendations.json")

_SCORE_HIST_PATH = os.path.expanduser("~/.michael_leap_score_history.json")


class ScoreHistory:
    """Track daily LEAP scores per stock. Keeps every daily snapshot."""

    def __init__(self):
        self.data = {}  # {symbol: [{date, score, price, signal, breakdown}, ...]}
        try:
            if os.path.exists(_SCORE_HIST_PATH):
                self.data = json.load(open(_SCORE_HIST_PATH))
        except Exception:
            pass

    def _save(self):
        try:
            json.dump(self.data, open(_SCORE_HIST_PATH, "w"), indent=2)
        except Exception:
            pass

    def log(self, symbol, score, price, signal, breakdown):
        """Log today's score for a stock. One entry per stock per day."""
        today = datetime.now().strftime("%Y-%m-%d")
        if symbol not in self.data:
            self.data[symbol] = []
        # Don't double-log same day
        for entry in self.data[symbol]:
            if entry.get("date") == today:
                # Update if score changed (re-scan)
                entry["score"] = score
                entry["price"] = price
                entry["signal"] = signal
                entry["breakdown"] = breakdown
                self._save()
                return
        self.data[symbol].append({
            "date": today, "score": score, "price": price,
            "signal": signal, "breakdown": breakdown,
        })
        self._save()

    def peak(self, symbol):
        """Return the peak score entry for a stock, or None."""
        entries = self.data.get(symbol, [])
        if not entries:
            return None
        return max(entries, key=lambda e: e["score"])

    def recent(self, symbol, n=10):
        """Return last N score entries for a stock (newest first)."""
        entries = self.data.get(symbol, [])
        return list(reversed(entries[-n:]))

    def top_scores_ever(self, n=10):
        """Return top N highest scores across all stocks and dates."""
        all_entries = []
        for sym, entries in self.data.items():
            for e in entries:
                all_entries.append({**e, "symbol": sym})
        all_entries.sort(key=lambda x: -x["score"])
        return all_entries[:n]

    def top_current(self, n=10):
        """Return top N stocks by their most recent score."""
        today = datetime.now().strftime("%Y-%m-%d")
        latest = {}
        for sym, entries in self.data.items():
            if entries:
                last = entries[-1]
                latest[sym] = last
        ranked = sorted(latest.items(), key=lambda x: -x[1]["score"])
        return [(sym, entry) for sym, entry in ranked[:n]]

class LeapTracker:
    def __init__(self):
        self.recs = []
        try:
            if os.path.exists(_REC_PATH):
                self.recs = json.load(open(_REC_PATH))
        except Exception: pass

    def _save(self):
        try: json.dump(self.recs, open(_REC_PATH, "w"), indent=2, default=str)
        except Exception: pass

    def log(self, sym, price, score, signal, breakdown, leap_info=None, barchart_opinion=None):
        # Don't double-log same stock same day
        today = datetime.now().strftime("%Y-%m-%d")
        for r in self.recs:
            if r.get("symbol") == sym and r.get("date", "").startswith(today):
                return
        entry = {
            "date": datetime.now().isoformat(), "symbol": sym, "price": price,
            "score": score, "signal": signal, "breakdown": breakdown,
            "barchart_opinion": barchart_opinion,
            "leap": {"strike": leap_info["strike"], "premium": leap_info["premium"],
                     "exp": leap_info["exp"], "dte": leap_info["dte"]} if leap_info else None,
            "outcomes": {},
        }
        self.recs.append(entry)
        self._save()

    def check_outcomes(self):
        """For each record, fetch the historical close price at each elapsed
        horizon (7d, 14d, 30d, 60d) and compute change_pct from entry price.
        Uses yfinance.history() for the date range starting at the target date
        and extending 5 days forward (handles weekends/holidays — takes first
        available close).
        """
        updated, now = 0, datetime.now()
        for rec in self.recs:
            rec_date = datetime.fromisoformat(rec["date"])
            elapsed = (now - rec_date).days
            for label, days in [("7d", 7), ("14d", 14), ("30d", 30), ("60d", 60)]:
                if rec.get("outcomes", {}).get(label, {}).get("checked") or elapsed < days:
                    continue
                try:
                    target = rec_date + timedelta(days=days)
                    end    = target + timedelta(days=5)
                    hist = yf.Ticker(rec["symbol"]).history(
                        start=target.strftime('%Y-%m-%d'),
                        end=end.strftime('%Y-%m-%d')
                    )
                    if hist.empty:
                        continue
                    p = round(float(hist['Close'].iloc[0]), 2)
                    actual_date = hist.index[0].strftime('%Y-%m-%d')
                    rec.setdefault("outcomes", {})[label] = {
                        "price": p,
                        "change_pct": round((p - rec["price"]) / rec["price"] * 100, 2),
                        "checked": True,
                        "actual_date": actual_date,  # which trading day was used
                    }
                    updated += 1
                except Exception: continue
        if updated: self._save()
        return updated

    def _checked(self):
        return [{**r, "_chg": r["outcomes"]["30d"]["change_pct"]}
                for r in self.recs if r.get("outcomes", {}).get("30d", {}).get("checked")]

    def pillar_accuracy(self):
        """Analyze which pillars predict winners (30-day outcome)."""
        checked = self._checked()
        if len(checked) < 3:
            return {}
        pillars = {}
        for rec in checked:
            win = rec["_chg"] > 0
            bd = rec.get("breakdown", {})
            for pillar, pts in bd.items():
                if pillar not in pillars:
                    pillars[pillar] = {"wins": 0, "total": 0, "max_pts": 0, "pts_when_win": [], "pts_when_loss": []}
                pillars[pillar]["total"] += 1
                if win:
                    pillars[pillar]["wins"] += 1
                    pillars[pillar]["pts_when_win"].append(pts)
                else:
                    pillars[pillar]["pts_when_loss"].append(pts)
                # Track max possible
                max_map = PILLAR_MAX
                pillars[pillar]["max_pts"] = max_map.get(pillar, 5)
        for p, s in pillars.items():
            s["wr"] = s["wins"] / s["total"] * 100 if s["total"] > 0 else 0
            s["avg_win_pts"] = sum(s["pts_when_win"]) / len(s["pts_when_win"]) if s["pts_when_win"] else 0
            s["avg_loss_pts"] = sum(s["pts_when_loss"]) / len(s["pts_when_loss"]) if s["pts_when_loss"] else 0
        return dict(sorted(pillars.items(), key=lambda x: -x[1]["wr"]))

    def score_accuracy(self):
        """Win rate by score band."""
        checked = self._checked()
        hi  = f"{STRONG_THRESHOLD}-{MAX_SCORE}"
        mid = f"{MONITOR_THRESHOLD}-{STRONG_THRESHOLD - 1}"
        lo  = f"0-{MONITOR_THRESHOLD - 1}"
        bands = {hi: [], mid: [], lo: []}
        for rec in checked:
            sc = rec.get("score", 0)
            if sc >= STRONG_THRESHOLD: bands[hi].append(rec["_chg"] > 0)
            elif sc >= MONITOR_THRESHOLD: bands[mid].append(rec["_chg"] > 0)
            else: bands[lo].append(rec["_chg"] > 0)
        return {k: {"total": len(v), "wr": sum(v)/len(v)*100 if v else 0} for k, v in bands.items()}

    def signal_accuracy(self):
        """Win rate by signal type."""
        checked = self._checked()
        sigs = {}
        for rec in checked:
            sig = rec.get("signal", "—")
            if sig not in sigs: sigs[sig] = []
            sigs[sig].append(rec["_chg"] > 0)
        return {k: {"total": len(v), "wr": sum(v)/len(v)*100 if v else 0} for k, v in sigs.items()}

def _conf(n):
    if n >= 50: return "high confidence"
    if n >= 20: return "moderate confidence"
    if n >= 10: return "low confidence"
    return f"very low — {n} recs, need more data"


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def parse_barchart_signal(opinion_str):
    """Extract percentage and signal from '85% Buy' or '60% Sell' etc."""
    if not opinion_str or opinion_str == 'N/A':
        return None, None
    try:
        parts = opinion_str.split()
        pct = float(parts[0].rstrip('%'))
        signal = parts[1] if len(parts) > 1 else None
        return pct, signal
    except:
        return None, None


def calc_barchart_trend(yesterday, last_week, last_month):
    """Compare timeframes to show trend momentum. Returns (arrow, momentum_pts) or (None, None)."""
    y_pct, y_sig = parse_barchart_signal(yesterday)
    w_pct, w_sig = parse_barchart_signal(last_week)
    if y_pct is None or w_pct is None:
        return None, None
    momentum = y_pct - w_pct
    if momentum > 3:
        arrow = "↑"
    elif momentum < -3:
        arrow = "↓"
    else:
        arrow = "→"
    return arrow, round(momentum, 1)


def format_barchart_opinion_b(yesterday, last_week, last_month):
    """Approach B: '↑ 85% Buy' or '↓ 60% Sell' etc. Shows current opinion + trend direction."""
    y_pct, y_sig = parse_barchart_signal(yesterday)
    arrow, momentum = calc_barchart_trend(yesterday, last_week, last_month)
    if y_pct is None or y_sig is None:
        return "N/A"
    if arrow:
        return f"{arrow} {int(y_pct)}% {y_sig}"
    else:
        return f"{int(y_pct)}% {y_sig}"


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 1) if not pd.isna(val) else None


def calc_rev_confirmed(closes, period=14):
    if len(closes) < period + 2:
        return False
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    rsi_ma9 = rsi.ewm(span=9, adjust=False).mean()
    if (pd.isna(rsi.iloc[-1]) or pd.isna(rsi.iloc[-2])
            or pd.isna(rsi_ma9.iloc[-1]) or pd.isna(rsi_ma9.iloc[-2])):
        return False
    return bool(rsi.iloc[-1] > rsi_ma9.iloc[-1] and rsi.iloc[-2] <= rsi_ma9.iloc[-2])


def calc_pivot_points(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if len(hist) < 2:
            return (None,) * 6
        h, l, c = hist['High'].iloc[-2], hist['Low'].iloc[-2], hist['Close'].iloc[-2]
        pivot = (h + l + c) / 3
        return (
            round(pivot - 2*(h-l), 2),
            round(pivot - (h-l), 2),
            round(2*pivot - h, 2),
            round(2*pivot - l, 2),
            round(pivot + (h-l), 2),
            round(pivot + 2*(h-l), 2),
        )
    except:
        return (None,) * 6


def calc_monthly_pivots(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="3mo", interval="1mo")
        if len(hist) < 2:
            return None, None
        h, l, c = hist['High'].iloc[-2], hist['Low'].iloc[-2], hist['Close'].iloc[-2]
        pivot = (h + l + c) / 3
        s2 = round(pivot - (h - l), 2)
        s3 = round(pivot - 2*(h - l), 2)
        return s2, s3
    except:
        return None, None


def get_ath_and_52w(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist_max = stock.history(period="max")
        hist_1y  = stock.history(period="1y")
        ath  = round(float(hist_max['High'].max()), 2) if len(hist_max) else None
        w52h = round(float(hist_1y['High'].max()),  2) if len(hist_1y)  else None
        return ath, w52h
    except:
        return None, None


def get_best_leap(ticker, target_strike):
    try:
        stock = yf.Ticker(ticker)
        exps  = stock.options
        today = datetime.today()
        candidates = []
        furthest = None
        furthest_dte = 0
        for exp_str in exps:
            dte = (datetime.strptime(exp_str, "%Y-%m-%d") - today).days
            if dte < 540:
                continue
            chain = stock.option_chain(exp_str).calls
            chain = chain[(chain['strike'] > 0) &
                          ((chain['lastPrice'] > 0) | (chain['ask'] > 0))].copy()
            if chain.empty:
                continue
            chain['dist'] = abs(chain['strike'] - target_strike)
            row = chain.loc[chain['dist'].idxmin()]
            premium = float(row['lastPrice']) if float(row['lastPrice']) > 0 else float(row['ask'])
            iv = round(float(row['impliedVolatility']) * 100, 1) if row['impliedVolatility'] else None
            if premium > 0:
                c = {'strike': float(row['strike']), 'premium': round(premium, 2),
                     'dte': dte, 'exp': exp_str, 'iv': iv,
                     'dist': abs(float(row['strike']) - target_strike)}
                candidates.append(c)
                if dte > furthest_dte:
                    furthest_dte = dte
                    furthest = {k: v for k, v in c.items()}
        if not candidates:
            return None, None
        return min(candidates, key=lambda x: x['dist']), furthest
    except:
        return None, None


def calc_ma_rsi_signal(closes, rsi):
    try:
        current_price = closes.iloc[-1]
        best_ma_len, best_bounces = 21, -1
        for ma_len in [21, 50, 100, 200]:
            if len(closes) < ma_len:
                continue
            ma = closes.rolling(ma_len).mean()
            bounces, in_zone, zone_low = 0, False, None
            for i in range(ma_len, len(closes) - 5):
                price, ma_val = closes.iloc[i], ma.iloc[i]
                if pd.isna(ma_val):
                    continue
                pct = ((price - ma_val) / ma_val) * 100
                if not in_zone and -5 <= pct <= 5:
                    in_zone, zone_low = True, price
                elif in_zone:
                    if price < zone_low:
                        zone_low = price
                    recovery = ((price - zone_low) / zone_low) * 100 if zone_low else 0
                    if recovery >= 5:
                        bounces += 1
                        in_zone = False
                    elif pct < -10 or pct > 15:
                        in_zone = False
            if bounces > best_bounces:
                best_bounces, best_ma_len = bounces, ma_len

        ma_vals = closes.rolling(best_ma_len).mean()
        current_ma = ma_vals.iloc[-1]
        pct_vs_ma = ((current_price - current_ma) / current_ma) * 100

        divergence = False
        rc = closes.iloc[-20:]
        if len(rc) >= 14:
            d = rc.diff()
            rs2 = d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean()
            rsi_s = 100 - (100 / (1 + rs2))
            if rc.iloc[-1] < rc.iloc[-10] and rsi_s.iloc[-1] > rsi_s.iloc[-10]:
                divergence = True

        if divergence and pct_vs_ma < 0:
            sig = "Diverg"
        elif pct_vs_ma < -2 and rsi is not None and rsi < 45:
            sig = "Entry"
        elif pct_vs_ma > 5 and rsi is not None and rsi > 60:
            sig = "Wait"
        else:
            sig = "Watch"

        return sig, best_ma_len, round(pct_vs_ma, 1)
    except:
        return "—", None, None


# score_leap moved to leap_scoring.py in Commit 0.5


def get_barchart_opinion(ticker):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
        url = f'https://www.barchart.com/stocks/quotes/{ticker}/opinion'
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        pct_tag = (soup.find('span', class_='opinion-percent buy') or
                   soup.find('span', class_='opinion-percent sell') or
                   soup.find('span', class_='opinion-percent hold'))
        sig_tag = (soup.find('span', class_='opinion-signal buy') or
                   soup.find('span', class_='opinion-signal sell') or
                   soup.find('span', class_='opinion-signal hold'))
        pct = pct_tag.get_text(strip=True) if pct_tag else 'N/A'
        sig = sig_tag.get_text(strip=True) if sig_tag else 'N/A'
        opinion = f"{pct} {sig}" if pct != 'N/A' else 'N/A'
        strength = direction = yesterday = last_week = last_month = 'N/A'
        graphs = soup.find('div', class_='opinion-graphs')
        if graphs:
            txt = graphs.get_text(strip=True)
            if 'Strength:' in txt and 'Direction:' in txt:
                parts = txt.replace('Strength:', '').replace('Direction:', '|').split('|')
                strength = parts[0].strip() if parts else 'N/A'
                direction = parts[1].strip() if len(parts) > 1 else 'N/A'
        snap = soup.find('h3', string='Snapshot Opinion')
        if snap:
            txt = snap.find_parent().get_text(strip=True)
            ym = re.search(r'Yesterday(\d+%\s+\w+?)Last', txt)
            wm = re.search(r'Last Week(\d+%\s+\w+?)Last', txt)
            mm = re.search(r'Last Month(\d+%\s+\w+?)Snapshot', txt)
            yesterday  = ym.group(1).strip() if ym else 'N/A'
            last_week  = wm.group(1).strip() if wm else 'N/A'
            last_month = mm.group(1).strip() if mm else 'N/A'
        return opinion, strength, direction, yesterday, last_week, last_month
    except:
        return 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A'


def get_market_info():
    try:
        ET = zoneinfo.ZoneInfo("America/New_York")
        now_et = datetime.now(ET)
        weekday = now_et.weekday()
        half_days = {
            (2025, 11, 28), (2025, 12, 24),
            (2026,  1,  2), (2026,  4,  2), (2026,  7,  3),
            (2026, 11, 27), (2026, 12, 24),
        }
        today_key = (now_et.year, now_et.month, now_et.day)
        is_half_day = today_key in half_days
        market_open  = dtime(9, 30)
        market_close = dtime(13, 0) if is_half_day else dtime(16, 0)
        now_t = now_et.time()
        if weekday >= 5:
            status = "Markets closed (weekend)"
        elif now_t < market_open:
            status = f"Pre-market  (opens 9:30 AM ET)"
        elif now_t <= market_close:
            suffix = " — Half Day" if is_half_day else ""
            status = f"Markets open{suffix}"
        else:
            suffix = " (half day)" if is_half_day else ""
            status = f"Markets closed{suffix}"
        open_min  = 9 * 60 + 30
        close_min = 13 * 60 if is_half_day else 16 * 60
        span = close_min - open_min
        def fmt_min(m):
            h, mm = divmod(m, 60)
            suffix = 'A' if h < 12 else 'P'
            h12 = h if h <= 12 else h - 12
            return f"{h12}{suffix}" if mm == 0 else f"{h12}:{mm:02d}{suffix}"
        step = span // 3
        labels = [fmt_min(open_min + step * i) for i in range(1, 3)]
        time_labels = ['T'] + labels
        return status, time_labels
    except Exception as e:
        return "Markets", ['T', '1P', '3P']


def make_sparkline_image(vals, chg, width=220, height=80):
    try:
        color = '#00AA00' if chg >= 0 else '#CC0000'
        fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
        fig.patch.set_facecolor('white')
        ax.set_facecolor('white')
        x = list(range(len(vals)))
        mn, mx = min(vals), max(vals)
        pad = (mx - mn) * 0.05 if mx != mn else 1
        ax.plot(x, vals, color=color, linewidth=1.8)
        ax.fill_between(x, vals, mn - pad, alpha=0.18, color=color)
        ax.set_ylim(mn - pad, mx + pad)
        ax.set_xlim(0, len(vals)-1)
        ax.axis('off')
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except:
        plt.close('all')
        return None


def vix_label(price):
    if price < 15:   return '😌 Calm / Bullish'
    if price < 20:   return '😐 Neutral'
    if price < 30:   return '😟 Fear Rising'
    if price < 40:   return '😱 Panic'
    return '🔥 Possible Bottom'


# ─────────────────────────────────────────────────────────────────────────────
#  Main App
# ─────────────────────────────────────────────────────────────────────────────

class StockTracker:
    def __init__(self, root):
        self.root = root
        self.root.title("Stock Tracker - Updates every 30 minutes")
        self.root.geometry("2600x900")
        self.root.attributes('-zoomed', True)
        self.tickers = list(TICKERS)
        self.leap_data_cache = {}
        self._spark_images = {}
        self._temp_syms = set()
        self._user_added = []
        self._user_removed = []
        self._tracker = LeapTracker()
        self._score_history = ScoreHistory()
        try:
            if os.path.exists(_UNI_PATH):
                d = json.load(open(_UNI_PATH))
                self._user_added = d.get("added", [])
                self._user_removed = d.get("removed", [])
        except Exception: pass

        self._setup_styles()
        self._build_tabs()

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.after(500, self._start_tracker_load)
        self._auto_refresh()
        self.root.after(800, self._launch_pulse_window)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Treeview", background="white", foreground="black",
                         rowheight=35, fieldbackground="white", font=('Arial', 12))
        style.configure("Treeview.Heading", font=('Arial', 12, 'bold'),
                         background="#4A90E2", foreground="white", relief="raised")
        style.map('Treeview', background=[('selected', '#4A90E2')])

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=0, column=0, sticky='nsew')

        t1 = ttk.Frame(self.notebook, padding="15")
        t2 = ttk.Frame(self.notebook, padding="10")
        t3 = ttk.Frame(self.notebook, padding="10")
        t4 = ttk.Frame(self.notebook, padding="10")

        self.notebook.add(t1, text="  📈 Stock Tracker  ")
        self.notebook.add(t2, text="  🎯 LEAP Scanner  ")
        self.notebook.add(t3, text="  📊 MA Analysis  ")
        self.notebook.add(t4, text="  🌎 Market Overview  ")

        self._build_tracker_tab(t1)
        self._build_leap_tab(t2)
        self._build_ma_tab(t3)
        self._build_market_tab(t4)

    # ══════════════════════════════════════════════════════════════════════════
    #  TAB 1 — Stock Tracker (unchanged)
    # ══════════════════════════════════════════════════════════════════════════

    def _build_tracker_tab(self, parent):
        cols = ('Ticker','Price','Change %','S3','S2','S1','R3','Opinion')
        widths = {
            'Ticker':90,'Price':100,'Change %':100,
            'S3':100,'S2':100,'S1':100,'R3':100,
            'Opinion':130
        }
        self.tree = ttk.Treeview(parent, columns=cols, show='headings', height=18)
        for col in cols:
            self.tree.heading(col, text=col,
                command=lambda c=col: self._sort_column(self.tree, c, '_tracker_sort'))
            self.tree.column(col, width=widths[col], anchor='center')
        self.tree.tag_configure('pos',     foreground='#006600')
        self.tree.tag_configure('neg',     foreground='#CC0000')
        self.tree.tag_configure('evenrow', background='#F8F8F8')
        self.tree.tag_configure('oddrow',  background='#FFFFFF')
        sb = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sb.grid(row=1, column=0, sticky='ew')
        ctrl = tk.Frame(parent, bg='#f5f5f5')
        ctrl.grid(row=2, column=0, sticky='ew')
        tk.Button(ctrl, text="🔄 Refresh Data", bg='#4A90E2', fg='white',
                  font=('Arial', 10, 'bold'), padx=10, pady=3, relief='flat',
                  cursor='hand2', command=self._start_tracker_load).pack(side=tk.LEFT, padx=5, pady=4)
        self.status_label = ttk.Label(ctrl, text="Loading...", foreground="blue", font=('Arial', 10))
        self.status_label.pack(side=tk.LEFT, padx=10)
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.tree.bind('<Double-Button-1>', self._show_chart)
        self._tracker_sort = {}

    def _start_tracker_load(self):
        self.status_label.config(text="Loading...", foreground="blue")
        threading.Thread(target=self._load_tracker_data, daemon=True).start()

    def _load_tracker_data(self):
        rows = []
        for ticker in self.tickers:
            try:
                stock = yf.Ticker(ticker)
                hist  = stock.history(period="1d")
                if hist.empty: continue
                price = round(float(hist['Close'].iloc[-1]), 2)
                prev  = stock.info.get('previousClose', price)
                chg   = round(((price - prev) / prev) * 100, 2)
                s3, s2, s1, r1, r2, r3 = calc_pivot_points(ticker)
                def fmt(val, bracket):
                    if val is None: return 'N/A'
                    return f"{val:.2f} ►" if bracket else f"{val:.2f}"
                levels = [(s3,'s3'),(s2,'s2'),(s1,'s1'),(r1,'r1'),(r2,'r2'),(r3,'r3')]
                valid  = [(v,n) for v,n in levels if v is not None]
                below  = [(v,n) for v,n in valid if v < price]
                above  = [(v,n) for v,n in valid if v > price]
                bl = max(below, key=lambda x: x[0])[1] if below else None
                al = min(above, key=lambda x: x[0])[1] if above else None
                opinion, strength, direction, yesterday, last_week, last_month = get_barchart_opinion(ticker)
                opinion_b = format_barchart_opinion_b(yesterday, last_week, last_month)
                time.sleep(1)
                rows.append({
                    'ticker': ticker, 'price': price, 'chg': chg,
                    's3': fmt(s3, bl=='s3'), 's2': fmt(s2, bl=='s2'),
                    's1': fmt(s1, bl=='s1'), 'r1': fmt(r1, al=='r1'),
                    'r2': fmt(r2, al=='r2'), 'r3': fmt(r3, al=='r3'),
                    'opinion': opinion_b,
                    'tag': 'pos' if chg >= 0 else 'neg'
                })
            except Exception as e:
                print(f"Tracker error {ticker}: {e}")
        self.root.after(0, lambda r=rows: self._populate_tracker(r))

    def _populate_tracker(self, rows):
        try: self.tree.get_children()
        except: return
        self.tree.delete(*self.tree.get_children())
        # Sort by LEAP score (highest first) to match LEAP tab ordering
        rows.sort(key=lambda r: -self.leap_data_cache.get(r['ticker'], {}).get('score', 0))
        for i, r in enumerate(rows):
            self.tree.insert('', tk.END, values=(
                r['ticker'], f"${r['price']}", f"{r['chg']:+.2f}%",
                r['s3'], r['s2'], r['s1'], r['r3'],
                r['opinion']
            ), tags=(r['tag'], 'evenrow' if i % 2 == 0 else 'oddrow'))
        self.status_label.config(
            text=f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Next: 30min | Double-click for chart | Opinion: ↑↓→ = warming/cooling/stable | ► = bracketing levels",
            foreground="green")

    # ══════════════════════════════════════════════════════════════════════════
    #  TAB 2 — LEAP Scanner (with universe management + accuracy)
    # ══════════════════════════════════════════════════════════════════════════

    def _build_leap_tab(self, parent):
        # Alert bar
        self.leap_alert_var = tk.StringVar(value="Loading LEAP data...")
        alert_bar = tk.Label(parent, textvariable=self.leap_alert_var,
                             bg='#f5f5f5', fg='#333333', font=('Arial', 11, 'bold'),
                             anchor='w', padx=10, pady=4)
        alert_bar.grid(row=0, column=0, sticky='ew')

        # Legend bar
        legend = tk.Label(parent,
            text=("  ⓘ  Prem Effcncy = cents you pay per $1 of stock  "
                  "(8 = pay $8 to control $100 — lower is better)     "
                  "✦  Leverage = dollars of stock controlled per $1 spent  "
                  "(12 = your $10 moves like $120 — higher is better)"),
            bg='#e8e8e8', fg='#333333', font=('Arial', 9), anchor='w', padx=10, pady=3)
        legend.grid(row=1, column=0, sticky='ew')

        # Control bar — refresh + temp stock + manage list + check outcomes
        ctrl = tk.Frame(parent, bg='#f0f0f0')
        ctrl.grid(row=2, column=0, sticky='ew', pady=(4, 4))
        tk.Button(ctrl, text="🔄 Refresh LEAP Data", bg='#4A90E2', fg='white',
                  font=('Arial', 10, 'bold'), padx=10, pady=3, relief='flat',
                  cursor='hand2', command=self._refresh_leap).pack(side=tk.LEFT, padx=5, pady=3)

        # Temp stock entry
        tk.Label(ctrl, text="  Add:", bg='#f0f0f0', font=('Arial', 10, 'bold')).pack(side=tk.LEFT)
        self._temp_entry = tk.Entry(ctrl, font=('Arial', 11), width=7)
        self._temp_entry.pack(side=tk.LEFT, padx=3)
        tk.Button(ctrl, text="➕ Scan", bg='#00AA00', fg='white',
                  font=('Arial', 9, 'bold'), padx=6, pady=2, relief='flat', cursor='hand2',
                  command=self._add_temp).pack(side=tk.LEFT, padx=2)
        tk.Button(ctrl, text="➕ Perm", bg='#006600', fg='white',
                  font=('Arial', 9, 'bold'), padx=6, pady=2, relief='flat', cursor='hand2',
                  command=self._add_perm).pack(side=tk.LEFT, padx=2)

        tk.Button(ctrl, text="📋 Manage List", bg='#555555', fg='white',
                  font=('Arial', 9, 'bold'), padx=8, pady=2, relief='flat', cursor='hand2',
                  command=self._manage_universe).pack(side=tk.LEFT, padx=(10, 2))

        tk.Button(ctrl, text="🔍 Check Outcomes", bg='#996600', fg='white',
                  font=('Arial', 9, 'bold'), padx=8, pady=2, relief='flat', cursor='hand2',
                  command=self._check_outcomes).pack(side=tk.LEFT, padx=2)

        tk.Button(ctrl, text="📊 Accuracy", bg='#660066', fg='white',
                  font=('Arial', 9, 'bold'), padx=8, pady=2, relief='flat', cursor='hand2',
                  command=self._show_accuracy).pack(side=tk.LEFT, padx=2)

        tk.Button(ctrl, text="🏆 Top Scores", bg='#CC6600', fg='white',
                  font=('Arial', 9, 'bold'), padx=8, pady=2, relief='flat', cursor='hand2',
                  command=self._show_top_scores).pack(side=tk.LEFT, padx=2)

        tk.Button(ctrl, text="🌐 Scan All", bg='#1a1d9e', fg='white',
                  font=('Arial', 9, 'bold'), padx=8, pady=2, relief='flat', cursor='hand2',
                  command=self._start_scan_all).pack(side=tk.LEFT, padx=2)

        self._temp_lbl = tk.Label(ctrl, text="", bg='#f0f0f0', fg='#006600', font=('Arial', 9))
        self._temp_lbl.pack(side=tk.LEFT, padx=8)

        self.leap_status = ttk.Label(ctrl, text="", foreground="blue", font=('Arial', 10))
        self.leap_status.pack(side=tk.RIGHT, padx=10)

        # Table
        leap_cols = ('Ticker','Price','52W High','% from 52W','Prem Effcncy','Leverage',
                     'Monthly S2','Monthly S3','vs S2','vs S3',
                     'Furthest Exp','Furthest Prem',f'LEAP Score /{MAX_SCORE}','RSI Score','S2S3 Score','MA+RSI','Signal','Rev?')
        leap_widths = {
            'Ticker':80,'Price':90,'52W High':100,'% from 52W':110,
            'Prem Effcncy':110,'Leverage':90,
            'Monthly S2':100,'Monthly S3':100,'vs S2':90,'vs S3':90,
            'Furthest Exp':120,'Furthest Prem':120,
            f'LEAP Score /{MAX_SCORE}':130,'RSI Score':90,'S2S3 Score':90,'MA+RSI':100,'Signal':160,'Rev?':70
        }
        self.leap_tree = ttk.Treeview(parent, columns=leap_cols, show='headings', height=12)
        for col in leap_cols:
            self.leap_tree.heading(col, text=col,
                command=lambda c=col: self._sort_column(self.leap_tree, c, '_leap_sort'))
            self.leap_tree.column(col, width=leap_widths[col], anchor='center')
        self.leap_tree.tag_configure('s3_alert', background='#FFCCCC', foreground='#AA0000')
        self.leap_tree.tag_configure('s2_alert', background='#FFF3CC', foreground='#996600')
        self.leap_tree.tag_configure('strong',   background='#CCFFCC', foreground='#006600')
        self.leap_tree.tag_configure('evenrow',  background='#F8F8F8')
        self.leap_tree.tag_configure('oddrow',   background='#FFFFFF')
        self.leap_tree.tag_configure('ma_entry',  background='#E6FFE6')
        self.leap_tree.tag_configure('ma_diverg', background='#E6F0FF')
        self.leap_tree.tag_configure('ma_wait',   background='#FFE6E6')
        self.leap_tree.tag_configure('ma_watch',  background='#FFFFF0')
        sb = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.leap_tree.xview)
        self.leap_tree.configure(xscrollcommand=sb.set)
        self.leap_tree.grid(row=3, column=0, sticky='nsew')
        sb.grid(row=4, column=0, sticky='ew')

        # Detail panel
        detail_frame = ttk.LabelFrame(parent, text="  Selected Ticker — LEAP Detail  ", padding="10")
        detail_frame.grid(row=5, column=0, sticky='ew', pady=(6, 0))
        self.leap_detail_var = tk.StringVar(value="Click a row to see LEAP detail.")
        tk.Label(detail_frame, textvariable=self.leap_detail_var,
                 font=('Courier', 10), anchor='w', justify='left',
                 bg='#F8F8F8').pack(fill=tk.X)

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        self._leap_sort = {}
        self.leap_tree.bind('<ButtonRelease-1>', self._show_leap_detail)
        threading.Thread(target=self._load_leap_data, daemon=True).start()

    # ── Universe management ──────────────────────────────────────────────────

    def _add_temp(self):
        sym = self._temp_entry.get().upper().strip()
        if sym:
            self._temp_syms.add(sym)
            self._temp_entry.delete(0, "end")
            self._temp_lbl.configure(text=f"Temp: {', '.join(sorted(self._temp_syms))}")

    def _add_perm(self):
        sym = self._temp_entry.get().upper().strip()
        if not sym: return
        self._temp_entry.delete(0, "end")
        if sym not in self._user_added:
            self._user_added.append(sym)
        if sym in self._user_removed:
            self._user_removed.remove(sym)
        _save_universe(self._user_added, self._user_removed)
        global TICKERS
        TICKERS = _load_universe()
        self.tickers = list(TICKERS)
        messagebox.showinfo("Added", f"{sym} added permanently. List: {len(self.tickers)} stocks.")

    def _manage_universe(self):
        w = tk.Toplevel(self.root)
        w.title("📋 Manage Stock List")
        w.geometry("450x650")
        w.configure(bg="white")
        w.protocol("WM_DELETE_WINDOW", w.destroy)

        tk.Label(w, text="📋 Stock Universe", font=('Arial', 16, 'bold'),
                 fg='#1a1d24', bg='white').pack(pady=(10, 4))
        tk.Label(w, text=f"{len(self.tickers)} stocks ({len(self._user_added)} added, {len(self._user_removed)} removed)",
                 font=('Arial', 10), fg='#666666', bg='white').pack()

        # Add new
        af = tk.Frame(w, bg='#eef0f4', padx=10, pady=8)
        af.pack(fill=tk.X, padx=10, pady=8)
        tk.Label(af, text="Add symbol:", font=('Arial', 11, 'bold'), bg='#eef0f4').pack(side=tk.LEFT)
        add_e = tk.Entry(af, font=('Arial', 12), width=10)
        add_e.pack(side=tk.LEFT, padx=8)
        def do_add():
            s = add_e.get().upper().strip()
            if not s: return
            if s not in self._user_added: self._user_added.append(s)
            if s in self._user_removed: self._user_removed.remove(s)
            _save_universe(self._user_added, self._user_removed)
            global TICKERS; TICKERS = _load_universe(); self.tickers = list(TICKERS)
            w.destroy(); self._manage_universe()
        tk.Button(af, text="➕ Add", font=('Arial', 11, 'bold'), fg='white', bg='#00AA00',
                  relief='flat', cursor='hand2', command=do_add).pack(side=tk.LEFT)

        # Scrollable list
        cv = tk.Canvas(w, bg='white', highlightthickness=0)
        sb = tk.Scrollbar(w, orient='vertical', command=cv.yview)
        sf = tk.Frame(cv, bg='white')
        sf.bind('<Configure>', lambda e: cv.configure(scrollregion=cv.bbox('all')))
        cv.create_window((0, 0), window=sf, anchor='nw')
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10))
        cv.pack(fill=tk.BOTH, expand=True, padx=10)

        for sym in self.tickers:
            is_core = sym in CORE_TICKERS
            is_added = sym in self._user_added
            rf = tk.Frame(sf, bg='white')
            rf.pack(fill=tk.X, pady=1)
            tag = " [CORE]" if is_core else " [added]" if is_added else ""
            clr = '#4A90E2' if is_core else '#00AA00' if is_added else '#333333'
            tk.Label(rf, text=f"{sym}{tag}", font=('Consolas', 11), fg=clr,
                     bg='white', width=20, anchor='w').pack(side=tk.LEFT)
            if not is_core:
                def do_rm(s=sym):
                    if s in self._user_added: self._user_added.remove(s)
                    elif s not in self._user_removed: self._user_removed.append(s)
                    _save_universe(self._user_added, self._user_removed)
                    global TICKERS; TICKERS = _load_universe(); self.tickers = list(TICKERS)
                    w.destroy(); self._manage_universe()
                tk.Button(rf, text="✕", font=('Arial', 10, 'bold'), fg='#CC0000',
                          bg='white', relief='flat', cursor='hand2', command=do_rm).pack(side=tk.RIGHT, padx=8)

    # ── Recommendation tracking + accuracy ────────────────────────────────────

    def _check_outcomes(self):
        def bg():
            n = self._tracker.check_outcomes()
            self.root.after(0, lambda: messagebox.showinfo("Outcomes", f"Updated {n} outcomes."))
        threading.Thread(target=bg, daemon=True).start()

    def _show_accuracy(self):
        w = tk.Toplevel(self.root)
        w.title("📊 LEAP Accuracy Analysis")
        w.geometry("600x700")
        w.configure(bg='white')
        w.protocol("WM_DELETE_WINDOW", w.destroy)

        tk.Label(w, text="📊 LEAP Recommendation Accuracy", font=('Arial', 16, 'bold'),
                 fg='#1a1d24', bg='white').pack(pady=(10, 4))

        n_total = len(self._tracker.recs)
        n_checked = len(self._tracker._checked())
        conf = _conf(n_checked)
        tk.Label(w, text=f"Recommendations: {n_total} | Checked (30d): {n_checked} | [{conf}]",
                 font=('Arial', 10), fg='#666666', bg='white').pack(pady=(0, 8))

        if n_checked < 3:
            tk.Label(w, text="Need at least 3 checked outcomes for analysis.\n\n"
                             "Recommendations are logged every LEAP scan.\n"
                             "Outcomes are checked at +7, +14, +30, +60 days.\n\n"
                             "Click '🔍 Check Outcomes' to update.",
                     font=('Arial', 12), fg='#999999', bg='white', justify='left').pack(padx=20, pady=20)
            return

        # Pillar accuracy
        pillars = self._tracker.pillar_accuracy()
        if pillars:
            pf = tk.LabelFrame(w, text="  Pillar Accuracy (30-day outcomes)  ",
                               font=('Arial', 11, 'bold'), bg='white', padx=10, pady=8)
            pf.pack(fill=tk.X, padx=10, pady=8)
            for pillar, stats in pillars.items():
                wr = stats["wr"]
                clr = '#006600' if wr >= 60 else '#CC0000' if wr < 40 else '#333333'
                avg_w = stats["avg_win_pts"]
                avg_l = stats["avg_loss_pts"]
                insight = ""
                if wr >= 65 and stats["total"] >= 5:
                    insight = " — strong predictor"
                elif wr < 35 and stats["total"] >= 5:
                    insight = " — weak predictor, consider reweighting"
                tk.Label(pf, text=f"{pillar:<18} {wr:5.0f}% win  ({stats['wins']}/{stats['total']})  "
                               f"avg pts: win={avg_w:.1f} loss={avg_l:.1f}{insight}",
                         font=('Consolas', 10), fg=clr, bg='white', anchor='w').pack(fill=tk.X)

        # Score band accuracy
        bands = self._tracker.score_accuracy()
        if any(v["total"] > 0 for v in bands.values()):
            sf = tk.LabelFrame(w, text="  Score Band Accuracy  ",
                               font=('Arial', 11, 'bold'), bg='white', padx=10, pady=8)
            sf.pack(fill=tk.X, padx=10, pady=8)
            for band, stats in bands.items():
                if stats["total"] == 0: continue
                clr = '#006600' if stats["wr"] >= 50 else '#CC0000'
                tk.Label(sf, text=f"Score {band}: {stats['wr']:.0f}% win rate ({stats['total']} recs)",
                         font=('Consolas', 11), fg=clr, bg='white', anchor='w').pack(fill=tk.X)

        # Signal accuracy
        sigs = self._tracker.signal_accuracy()
        if sigs:
            sigf = tk.LabelFrame(w, text="  Signal Type Accuracy  ",
                                 font=('Arial', 11, 'bold'), bg='white', padx=10, pady=8)
            sigf.pack(fill=tk.X, padx=10, pady=8)
            for sig, stats in sigs.items():
                if stats["total"] == 0: continue
                clr = '#006600' if stats["wr"] >= 50 else '#CC0000'
                tk.Label(sigf, text=f"{sig}: {stats['wr']:.0f}% win ({stats['total']} recs)",
                         font=('Consolas', 11), fg=clr, bg='white', anchor='w').pack(fill=tk.X)

        # Recent recommendations
        rf = tk.LabelFrame(w, text="  Recent Recommendations  ",
                           font=('Arial', 11, 'bold'), bg='white', padx=10, pady=8)
        rf.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        rt = tk.Text(rf, font=('Consolas', 10), bg='white', fg='#333333', wrap='word', bd=0, height=10)
        rs = tk.Scrollbar(rf, command=rt.yview)
        rt.configure(yscrollcommand=rs.set)
        rs.pack(side=tk.RIGHT, fill=tk.Y)
        rt.pack(fill=tk.BOTH, expand=True)
        rt.tag_configure("win", foreground="#006600")
        rt.tag_configure("loss", foreground="#CC0000")
        rt.tag_configure("pending", foreground="#999999")
        for rec in reversed(self._tracker.recs[-30:]):
            line = f"{rec['date'][:10]}  {rec['symbol']:<6}  ${rec['price']:<8.2f}  Score:{rec['score']:<3}  {rec['signal']}\n"
            o30 = rec.get("outcomes", {}).get("30d", {})
            if o30.get("checked"):
                tag = "win" if o30["change_pct"] > 0 else "loss"
                rt.insert(tk.END, line, tag)
                rt.insert(tk.END, f"  30d: {o30['change_pct']:+.1f}%\n", tag)
            else:
                rt.insert(tk.END, line, "pending")
        rt.configure(state="disabled")

    def _show_top_scores(self):
        w = tk.Toplevel(self.root)
        w.title("🏆 LEAP Score History")
        w.geometry("700x750")
        w.configure(bg='white')
        w.protocol("WM_DELETE_WINDOW", w.destroy)

        tk.Label(w, text="🏆 LEAP Score History", font=('Arial', 16, 'bold'),
                 fg='#1a1d24', bg='white').pack(pady=(10, 4))

        # Top all-time scores
        top_ever = self._score_history.top_scores_ever(20)
        if not top_ever:
            tk.Label(w, text="No score history yet.\n\nScores are logged each LEAP scan.\n"
                             "Run a scan to start building history.",
                     font=('Arial', 12), fg='#999999', bg='white', justify='left').pack(padx=20, pady=20)
            return

        # All-time highest
        tf = tk.LabelFrame(w, text="  All-Time Highest Scores  ",
                           font=('Arial', 11, 'bold'), bg='white', padx=10, pady=8)
        tf.pack(fill=tk.X, padx=10, pady=8)
        for entry in top_ever:
            sc = entry['score']
            clr = '#006600' if sc >= STRONG_THRESHOLD else '#996600' if sc >= MONITOR_THRESHOLD else '#333333'
            sig = entry.get('signal', '—')
            tk.Label(tf, text=f"{entry['symbol']:<6}  {sc}/{MAX_SCORE}  on {entry['date']}  "
                              f"@ ${entry['price']:.2f}  {sig}",
                     font=('Consolas', 10), fg=clr, bg='white', anchor='w').pack(fill=tk.X)

        # Per-stock peak scores
        pf = tk.LabelFrame(w, text="  Peak Score per Stock  ",
                           font=('Arial', 11, 'bold'), bg='white', padx=10, pady=8)
        pf.pack(fill=tk.X, padx=10, pady=8)
        peaks = []
        for sym in sorted(self._score_history.data.keys()):
            peak = self._score_history.peak(sym)
            if peak:
                peaks.append((sym, peak))
        peaks.sort(key=lambda x: -x[1]['score'])
        for sym, peak in peaks:
            sc = peak['score']
            clr = '#006600' if sc >= STRONG_THRESHOLD else '#996600' if sc >= MONITOR_THRESHOLD else '#333333'
            recent = self._score_history.recent(sym, 5)
            recent_str = "  ".join([f"{e['score']}" for e in recent])
            tk.Label(pf, text=f"{sym:<6}  Peak: {sc}/{MAX_SCORE} on {peak['date']} @ ${peak['price']:.2f}  "
                              f"| Recent: {recent_str}",
                     font=('Consolas', 10), fg=clr, bg='white', anchor='w').pack(fill=tk.X)

        # Stats summary
        sf = tk.LabelFrame(w, text="  Summary  ",
                           font=('Arial', 11, 'bold'), bg='white', padx=10, pady=8)
        sf.pack(fill=tk.X, padx=10, pady=8)
        total_stocks = len(self._score_history.data)
        total_entries = sum(len(v) for v in self._score_history.data.values())
        dates = set()
        for entries in self._score_history.data.values():
            for e in entries:
                dates.add(e['date'])
        tk.Label(sf, text=f"Tracking {total_stocks} stocks  |  "
                          f"{total_entries} total score entries  |  "
                          f"{len(dates)} scan days recorded",
                 font=('Arial', 10), fg='#555555', bg='white').pack(anchor='w')

    # ── LEAP data loading (with recommendation logging) ──────────────────────

    def _load_leap_data(self):
        scan_tickers = list(set(self.tickers) | self._temp_syms)
        results = []
        for ticker in scan_tickers:
            try:
                stock  = yf.Ticker(ticker)
                hist1y = stock.history(period="1y")
                if hist1y.empty: continue
                closes = hist1y['Close'].dropna()
                if closes.empty: continue
                price  = round(float(closes.iloc[-1]), 2)
                ath, w52h = get_ath_and_52w(ticker)
                ws2, ws3  = calc_monthly_pivots(ticker)
                rsi       = calc_rsi(closes)
                try:
                    rev_confirmed = calc_rev_confirmed(closes)
                except Exception:
                    rev_confirmed = False
                # RETEST: run ~/confirmation_test.py when JSON hits 500 entries
                ma_sig, best_ma, pct_vs_ma = calc_ma_rsi_signal(closes, rsi)
                leap, furthest = get_best_leap(ticker, w52h or ath) if (w52h or ath) else (None, None)
                prem_pct = leverage = None
                if leap and price:
                    prem_pct = round((leap['premium'] / price) * 100, 1)
                    leverage = round(price / leap['premium'], 1)
                vs_s2 = round(((price - ws2) / ws2) * 100, 1) if ws2 else None
                vs_s3 = round(((price - ws3) / ws3) * 100, 1) if ws3 else None
                closest = None
                if vs_s2 is not None and vs_s3 is not None:
                    closest = 's2' if abs(vs_s2) < abs(vs_s3) else 's3'
                elif vs_s2 is not None: closest = 's2'
                elif vs_s3 is not None: closest = 's3'
                pct_from_ath = round(((ath - price) / ath) * 100, 1) if ath else None
                sc, bd = score_leap(price, ath, prem_pct, leverage,
                                    vs_s2, vs_s3, leap['dte'] if leap else None, rsi,
                                    ma_signal=ma_sig)
                if vs_s3 is not None and abs(vs_s3) <= 5:
                    signal = "🔴 S3 ALERT"
                elif vs_s2 is not None and abs(vs_s2) <= 5:
                    signal = "🟡 S2 ALERT"
                elif sc >= STRONG_THRESHOLD:
                    signal = "🟢 STRONG SETUP"
                elif sc >= MONITOR_THRESHOLD:
                    signal = "⚪ MONITOR"
                else:
                    signal = "—"

                row = {
                    'ticker': ticker, 'price': price, 'w52h': w52h, 'ath': ath,
                    'pct_from_ath': pct_from_ath, 'rsi': rsi,
                    'ma_rsi_signal': ma_sig, 'best_ma_len': best_ma, 'pct_vs_ma': pct_vs_ma,
                    'prem_pct': prem_pct, 'leverage': leverage,
                    'ws2': ws2, 'ws3': ws3, 'vs_s2': vs_s2, 'vs_s3': vs_s3,
                    'closest': closest, 'score': sc, 'breakdown': bd,
                    'signal': signal, 'leap': leap, 'furthest_leap': furthest,
                    'rev_confirmed': rev_confirmed
                }
                results.append(row)
                self.leap_data_cache[ticker] = row

                # Log recommendation if actionable
                if signal != "—":
                    try:
                        opinion_raw = get_barchart_opinion(ticker)[0]
                    except Exception:
                        opinion_raw = None
                    self._tracker.log(ticker, price, sc, signal, bd, leap,
                                      barchart_opinion=opinion_raw)

                # Log score history (every stock, every scan)
                self._score_history.log(ticker, sc, price, signal, bd)
            except Exception as e:
                print(f"LEAP error {ticker}: {e}")

        # Clear temp stocks after scan
        self._temp_syms.clear()

        results.sort(key=lambda r: -r['score'])
        self.root.after(0, lambda r=results: self._populate_leap_table(r))

    def _populate_leap_table(self, results):
        try: self.leap_tree.get_children()
        except: return
        self.leap_tree.delete(*self.leap_tree.get_children())
        self._temp_lbl.configure(text="")
        alerts = []
        for i, r in enumerate(results):
            def f(v, pre='$'): return f"{pre}{v}" if v else 'N/A'
            w52h_str  = f(r['w52h'])
            athdraw   = f"{r['pct_from_ath']}" if r['pct_from_ath'] else 'N/A'
            prem_str  = f"{r['prem_pct']}"  if r['prem_pct']  else 'N/A'
            lev_str   = f"{r['leverage']}"  if r['leverage']  else 'N/A'
            ws2_str   = f(r['ws2'])
            ws3_str   = f(r['ws3'])
            cl = r.get('closest')
            vs2 = r['vs_s2']; vs3 = r['vs_s3']
            vs2_str = (f"{vs2:+.1f}  ◄" if cl=='s2' else f"{vs2:+.1f}") if vs2 is not None else 'N/A'
            vs3_str = (f"{vs3:+.1f}  ◄" if cl=='s3' else f"{vs3:+.1f}") if vs3 is not None else 'N/A'
            fl = r.get('furthest_leap')
            leap = r.get('leap')
            same = fl and leap and fl['exp'] == leap['exp']
            fe_str = fl['exp']        if fl and not same else '—'
            fp_str = f"${fl['premium']}" if fl and not same else '—'
            score_str = f"{r['score']} / {MAX_SCORE}"
            bd = r.get('breakdown') or {}
            rsi_score  = bd.get('RSI', '—')
            s2s3_score = bd.get('S2/S3 Level', '—')
            ma_str = r.get('ma_rsi_signal', '—')
            sig    = r['signal']
            rev_confirmed = r.get('rev_confirmed', False)
            rev_str = '✅' if rev_confirmed else '❌'
            if 'S3' in sig:
                tag = 's3_alert'; alerts.insert(0, f"S3: {r['ticker']}")
            elif 'S2' in sig:
                tag = 's2_alert'; alerts.append(f"S2: {r['ticker']}")
            elif r['score'] >= STRONG_THRESHOLD:
                tag = 'strong'
                if rev_confirmed:
                    alerts.append(f"STRONG+REV: {r['ticker']}")
            else:
                ma = r.get('ma_rsi_signal', '')
                if ma == 'Diverg':   tag = 'ma_diverg'
                elif ma == 'Entry':  tag = 'ma_entry'
                elif ma == 'Wait':   tag = 'ma_wait'
                else:                tag = 'ma_watch'
            self.leap_tree.insert('', tk.END, values=(
                r['ticker'], f"${r['price']}", w52h_str, athdraw,
                prem_str, lev_str, ws2_str, ws3_str, vs2_str, vs3_str,
                fe_str, fp_str, score_str, rsi_score, s2s3_score, ma_str, sig, rev_str
            ), tags=(tag,))
        alert_text = "  |  ".join(alerts) if alerts else "No S2/S3 alerts at this time"
        self.leap_alert_var.set(f"ALERTS:  {alert_text}")
        self.leap_status.config(
            text=f"LEAP scan: {datetime.now().strftime('%H:%M:%S')} | {len(self._tracker.recs)} recs tracked",
            foreground="green")
        # Refresh Top 5 on Market tab + Pulse top LEAPs
        self._refresh_top5()

    def _refresh_top5(self):
        """Update Top 5 LEAP table on Market tab and Pulse window after LEAP scan."""
        try:
            self.market_watch_tree.delete(*self.market_watch_tree.get_children())
            if self.leap_data_cache:
                top5 = sorted(self.leap_data_cache.values(), key=lambda r: -r['score'])[:5]
                for r in top5:
                    tag = 'strong' if r['score'] >= STRONG_THRESHOLD else ('alert' if 'ALERT' in r['signal'] else 'normal')
                    self.market_watch_tree.insert('', tk.END, values=(
                        r['ticker'], f"${r['price']}", f"{r['score']} / {MAX_SCORE}",
                        r['signal'], r.get('ma_rsi_signal', '—')
                    ), tags=(tag,))
        except Exception: pass
        # Update Pulse top 3
        try:
            if self.leap_data_cache and hasattr(self, 'pulse_leap_lbl'):
                top3 = sorted(self.leap_data_cache.values(), key=lambda r: -r['score'])[:3]
                self.pulse_leap_lbl.config(text='   '.join([f"{r['ticker']} {r['score']}/{MAX_SCORE}" for r in top3]))
        except Exception: pass

    def _show_leap_detail(self, event):
        sel = self.leap_tree.selection()
        if not sel: return
        ticker = self.leap_tree.item(sel[0])['values'][0]
        r = self.leap_data_cache.get(ticker)
        if not r: return
        leap = r['leap']; bd = r['breakdown']
        fl = r.get('furthest_leap')
        same = fl and leap and fl['exp'] == leap['exp']
        lines = [
            f"{'─'*70}",
            f"  {ticker}   |   Price: ${r['price']}   |   52W High: ${r['w52h']}   |   ATH: ${r['ath']}   |   Drawdown: {r['pct_from_ath']}%",
            f"{'─'*70}",
        ]
        if leap:
            lines += [
                f"  Best LEAP:    ${leap['strike']} Call  |  Expiry: {leap['exp']}  |  DTE: {leap['dte']} days",
                f"  Premium:      ${leap['premium']}  |  Prem/Price: {r['prem_pct']}%  |  Leverage: {r['leverage']}x",
                f"  IV:           {leap['iv']}%" if leap.get('iv') else "  IV:  N/A",
            ]
        if fl and not same:
            fl_pp  = round((fl['premium'] / r['price']) * 100, 1) if fl['premium'] and r['price'] else None
            fl_lev = round(r['price'] / fl['premium'], 1) if fl['premium'] and r['price'] else None
            fl_sc, _ = score_leap(r['price'], r['ath'], fl_pp, fl_lev,
                                   r['vs_s2'], r['vs_s3'], fl['dte'], r.get('rsi'),
                                   ma_signal=r.get('ma_rsi_signal'))
            lines += [
                f"{'─'*70}",
                f"  Furthest LEAP: ${fl['strike']} Call  |  Expiry: {fl['exp']}  |  DTE: {fl['dte']} days",
                f"  Premium: ${fl['premium']}  |  Prem/Price: {fl_pp}%  |  Leverage: {fl_lev}x  |  IV: {fl.get('iv','N/A')}%",
                f"  Furthest LEAP Score: {fl_sc} / {MAX_SCORE}",
            ]
        lines += [
            f"{'─'*70}",
            f"  Monthly S2: ${r['ws2']}  ({r['vs_s2']:+.1f}% from price)" if r['ws2'] else "  Monthly S2: N/A",
            f"  Monthly S3: ${r['ws3']}  ({r['vs_s3']:+.1f}% from price)" if r['ws3'] else "  Monthly S3: N/A",
            f"{'─'*70}",
            f"  SCORE BREAKDOWN:",
            f"    ATH Drawdown:    {bd.get('ATH Drawdown',0)}/3",
            f"    Prem Efficiency: {bd.get('Prem Efficiency',0)}/2",
            f"    Leverage:        {bd.get('Leverage',0)}/2",
            f"    S2/S3 Level:     {bd.get('S2/S3 Level',0)}/3",
            f"    {'─'*20}",
            f"    RSI+MA:          {r.get('rsi','N/A')} / {r.get('ma_rsi_signal','—')} → {bd.get('RSI',0)}/5",
            f"    {'─'*20}",
            f"    RSI+MA:          {r.get('ma_rsi_signal','—')}  "
            f"(Best MA: {r.get('best_ma_len','?')}-period, Price {r.get('pct_vs_ma',0):+.1f}% vs MA)",
            f"    TOTAL:           {r['score']}/{MAX_SCORE}   {r['signal']}",
            f"{'─'*70}",
        ]

        # Score history
        peak = self._score_history.peak(ticker)
        recent = self._score_history.recent(ticker, 10)
        if peak:
            lines.append(f"  SCORE HISTORY:")
            lines.append(f"    Peak:  {peak['score']}/{MAX_SCORE}  on {peak['date']}  @ ${peak['price']}")
            if recent and len(recent) > 1:
                hist_str = "    Last " + str(len(recent)) + " scans:  "
                hist_str += "  ".join([f"{e['date'][-5:]}:{e['score']}" for e in recent])
                lines.append(hist_str)
            lines.append(f"{'─'*70}")

        self.leap_detail_var.set("\n".join(lines))

    def _refresh_leap(self):
        self.leap_tree.delete(*self.leap_tree.get_children())
        self.leap_detail_var.set("Refreshing...")
        self.leap_data_cache = {}
        threading.Thread(target=self._load_leap_data, daemon=True).start()

    def _start_scan_all(self):
        """Scan FULL_UNIVERSE minus CORE_TICKERS, merge with cached daily results."""
        already_cached = set(self.leap_data_cache.keys())
        scan_targets = [t for t in FULL_UNIVERSE if t not in already_cached]
        if not scan_targets:
            # All already cached — just re-render merged results
            merged = list(self.leap_data_cache.values())
            merged.sort(key=lambda r: -r['score'])
            self.root.after(0, lambda r=merged: self._populate_leap_table(r))
            return
        self.leap_status.config(text=f"Scan All: 0/{len(scan_targets)}...", foreground="blue")
        threading.Thread(target=self._load_scan_all, args=(scan_targets,), daemon=True).start()

    def _load_scan_all(self, scan_targets):
        """Background worker: scan additional tickers, merge with cache, repopulate table."""
        total = len(scan_targets)
        for i, ticker in enumerate(scan_targets):
            self.root.after(0, lambda i=i, t=total: self.leap_status.config(
                text=f"Scanning {i+1}/{t}...", foreground="blue"))
            try:
                stock  = yf.Ticker(ticker)
                hist1y = stock.history(period="1y")
                if hist1y.empty:
                    continue
                closes = hist1y['Close'].dropna()
                if closes.empty: continue
                price  = round(float(closes.iloc[-1]), 2)
                ath, w52h = get_ath_and_52w(ticker)
                ws2, ws3  = calc_monthly_pivots(ticker)
                rsi       = calc_rsi(closes)
                try:
                    rev_confirmed = calc_rev_confirmed(closes)
                except Exception:
                    rev_confirmed = False
                ma_sig, best_ma, pct_vs_ma = calc_ma_rsi_signal(closes, rsi)
                leap, furthest = get_best_leap(ticker, w52h or ath) if (w52h or ath) else (None, None)
                prem_pct = leverage = None
                if leap and price:
                    prem_pct = round((leap['premium'] / price) * 100, 1)
                    leverage = round(price / leap['premium'], 1)
                vs_s2 = round(((price - ws2) / ws2) * 100, 1) if ws2 else None
                vs_s3 = round(((price - ws3) / ws3) * 100, 1) if ws3 else None
                closest = None
                if vs_s2 is not None and vs_s3 is not None:
                    closest = 's2' if abs(vs_s2) < abs(vs_s3) else 's3'
                elif vs_s2 is not None: closest = 's2'
                elif vs_s3 is not None: closest = 's3'
                pct_from_ath = round(((ath - price) / ath) * 100, 1) if ath else None
                sc, bd = score_leap(price, ath, prem_pct, leverage,
                                    vs_s2, vs_s3, leap['dte'] if leap else None, rsi,
                                    ma_signal=ma_sig)
                if vs_s3 is not None and abs(vs_s3) <= 5:
                    signal = "🔴 S3 ALERT"
                elif vs_s2 is not None and abs(vs_s2) <= 5:
                    signal = "🟡 S2 ALERT"
                elif sc >= STRONG_THRESHOLD:
                    signal = "🟢 STRONG SETUP"
                elif sc >= MONITOR_THRESHOLD:
                    signal = "⚪ MONITOR"
                else:
                    signal = "—"
                row = {
                    'ticker': ticker, 'price': price, 'w52h': w52h, 'ath': ath,
                    'pct_from_ath': pct_from_ath, 'rsi': rsi,
                    'ma_rsi_signal': ma_sig, 'best_ma_len': best_ma, 'pct_vs_ma': pct_vs_ma,
                    'prem_pct': prem_pct, 'leverage': leverage,
                    'ws2': ws2, 'ws3': ws3, 'vs_s2': vs_s2, 'vs_s3': vs_s3,
                    'closest': closest, 'score': sc, 'breakdown': bd,
                    'signal': signal, 'leap': leap, 'furthest_leap': furthest,
                    'rev_confirmed': rev_confirmed
                }
                self.leap_data_cache[ticker] = row
                if signal != "—":
                    self._tracker.log(ticker, price, sc, signal, bd, leap)
                self._score_history.log(ticker, sc, price, signal, bd)
            except Exception as e:
                print(f"Scan All error {ticker}: {e}")

        # Merge with existing cache and repopulate
        merged = list(self.leap_data_cache.values())
        merged.sort(key=lambda r: -r['score'])
        self.root.after(0, lambda r=merged: self._populate_leap_table(r))
        self.root.after(0, lambda: self.leap_status.config(
            text=f"Scan All complete: {len(self.leap_data_cache)} stocks | "
                 f"{datetime.now().strftime('%H:%M:%S')}",
            foreground="green"))

    # ══════════════════════════════════════════════════════════════════════════
    #  TAB 3 — MA Analysis (unchanged)
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ma_tab(self, parent):
        ctrl = tk.Frame(parent)
        ctrl.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        tk.Label(ctrl, text="Ticker:", font=('Arial', 11, 'bold')).pack(side=tk.LEFT, padx=(0,5))
        self.ma_ticker_var = tk.StringVar(value="SNOW")
        tk.Entry(ctrl, textvariable=self.ma_ticker_var, width=8,
                 font=('Arial', 11)).pack(side=tk.LEFT, padx=(0,10))
        tk.Label(ctrl, text="Period:", font=('Arial', 11, 'bold')).pack(side=tk.LEFT, padx=(0,5))
        self.ma_period_var = tk.StringVar(value="2y")
        for p in ["6mo","1y","2y","3y","5y"]:
            tk.Radiobutton(ctrl, text=p, variable=self.ma_period_var,
                           value=p, font=('Arial', 10)).pack(side=tk.LEFT, padx=3)
        tk.Button(ctrl, text="🔍 Analyze", bg='#4A90E2', fg='white',
                  font=('Arial', 10, 'bold'), padx=10, pady=3, relief='flat',
                  cursor='hand2', command=self._run_ma_analysis).pack(side=tk.LEFT, padx=(15,0))
        self.ma_status = ttk.Label(ctrl, text="", foreground="blue", font=('Arial', 10))
        self.ma_status.pack(side=tk.LEFT, padx=(15,0))
        ma_cols = ('MA','Period','Current Value','Price vs MA',
                   'Bounce Count','Bounce Rate','Avg Recovery %','Accuracy Score')
        ma_widths = {'MA':80,'Period':70,'Current Value':120,'Price vs MA':110,
                     'Bounce Count':110,'Bounce Rate':110,'Avg Recovery %':130,'Accuracy Score':130}
        self.ma_tree = ttk.Treeview(parent, columns=ma_cols, show='headings', height=6)
        for col in ma_cols:
            self.ma_tree.heading(col, text=col)
            self.ma_tree.column(col, width=ma_widths[col], anchor='center')
        self.ma_tree.tag_configure('best',   background='#CCFFCC', foreground='#006600', font=('Arial',12,'bold'))
        self.ma_tree.tag_configure('second', background='#FFF3CC', foreground='#996600', font=('Arial',12))
        self.ma_tree.tag_configure('normal', background='#FFFFFF', foreground='#000000', font=('Arial',11))
        self.ma_tree.grid(row=1, column=0, sticky='ew', pady=(0,8))
        detail_frame = ttk.LabelFrame(parent, text="  MA Analysis Detail  ", padding="10")
        detail_frame.grid(row=2, column=0, sticky='ew', pady=(0,8))
        self.ma_detail_var = tk.StringVar(value="Enter a ticker and click Analyze.")
        tk.Label(detail_frame, textvariable=self.ma_detail_var,
                 font=('Courier', 10), anchor='w', justify='left',
                 bg='#F8F8F8').pack(fill=tk.X)
        self.ma_chart_frame = tk.Frame(parent, bg='white', height=350)
        self.ma_chart_frame.grid(row=3, column=0, sticky='nsew', pady=(0,4))
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

    def _run_ma_analysis(self):
        self.ma_status.config(text="Analyzing...", foreground="blue")
        ticker = self.ma_ticker_var.get().strip().upper()
        period = self.ma_period_var.get()
        threading.Thread(target=self._do_ma_analysis, args=(ticker, period), daemon=True).start()

    def _do_ma_analysis(self, ticker, period):
        try:
            hist = yf.Ticker(ticker).history(period=period)
            if len(hist) < 200:
                self.root.after(0, lambda: self.ma_status.config(text="Not enough data.", foreground="red"))
                return
            closes = hist['Close']
            results = []
            for ma_len, label in [(21,'21 MA'),(50,'50 MA'),(100,'100 MA'),(200,'200 MA')]:
                if len(closes) < ma_len: continue
                ma = closes.rolling(ma_len).mean()
                curr_ma = round(float(ma.iloc[-1]), 2)
                curr_p  = round(float(closes.iloc[-1]), 2)
                pct_vs  = round(((curr_p - curr_ma) / curr_ma) * 100, 1)
                bounces, recs, in_zone, zone_low = 0, [], False, None
                for i in range(ma_len, len(closes)-5):
                    p, mv = closes.iloc[i], ma.iloc[i]
                    if pd.isna(mv): continue
                    pf = ((p - mv) / mv) * 100
                    if not in_zone and -5 <= pf <= 5:
                        in_zone, zone_low = True, p
                    elif in_zone:
                        if p < zone_low: zone_low = p
                        rec = ((p - zone_low) / zone_low) * 100 if zone_low else 0
                        if rec >= 5:
                            bounces += 1; recs.append(round(rec,1)); in_zone = False
                        elif pf < -10 or pf > 15:
                            in_zone = False
                total = len(closes) - ma_len
                br   = round((bounces / max(total/20, 1)) * 100, 1)
                avgr = round(sum(recs)/len(recs), 1) if recs else 0
                acc  = round((br * 0.6 + min(avgr,20) * 2), 1)
                results.append({'label':label,'period':ma_len,'current':curr_ma,
                                 'pct_vs_ma':pct_vs,'bounce_count':bounces,
                                 'bounce_rate':br,'avg_recovery':avgr,'accuracy_score':acc})
            results.sort(key=lambda x: -x['accuracy_score'])
            self.root.after(0, lambda: self._populate_ma_results(ticker, results, hist))
        except Exception as e:
            self.root.after(0, lambda: self.ma_status.config(text=f"Error: {e}", foreground="red"))

    def _populate_ma_results(self, ticker, results, hist):
        self.ma_tree.delete(*self.ma_tree.get_children())
        for i, r in enumerate(results):
            tag = 'best' if i == 0 else ('second' if i == 1 else 'normal')
            star = ' ★' if i == 0 else ''
            self.ma_tree.insert('', tk.END, values=(
                r['label']+star, r['period'], f"${r['current']}",
                f"{r['pct_vs_ma']:+.1f}%", r['bounce_count'],
                f"{r['bounce_rate']}%", f"{r['avg_recovery']}%", r['accuracy_score']
            ), tags=(tag,))
        best = results[0] if results else None
        if best:
            lines = [f"  {ticker} — MA Bounce Analysis", f"  {'─'*50}"]
            for i, r in enumerate(results):
                star = ' ★ MOST ACCURATE' if i == 0 else (' ▲ 2nd best' if i == 1 else '')
                lines.append(f"  #{i+1}  {r['label']}{star}  —  Bounce Rate: {r['bounce_rate']}%  |  Avg Recovery: {r['avg_recovery']}%  |  Score: {r['accuracy_score']}")
            lines.append(f"\n  Best MA: {best['label']} with {best['bounce_count']} bounces  |  Current: ${best['current']}  |  Price {best['pct_vs_ma']:+.1f}% vs MA")
            self.ma_detail_var.set("\n".join(lines))
        self.ma_status.config(text=f"Analysis complete: {ticker}", foreground="green")
        self._draw_ma_chart(ticker, hist, results)

    def _draw_ma_chart(self, ticker, hist, results):
        for w in self.ma_chart_frame.winfo_children(): w.destroy()
        fig, ax = plt.subplots(figsize=(14, 4))
        closes = hist['Close']
        ax.plot(hist.index, closes, label='Price', linewidth=2, color='#2196F3', zorder=5)
        colors = ['#FF6B00','#00AA00','#AA00AA','#CC0000']
        for i, r in enumerate(results):
            ma = closes.rolling(r['period']).mean()
            lbl = f"{r['label']} ★" if i == 0 else r['label']
            lw  = 2.5 if i == 0 else 1.2
            ax.plot(hist.index, ma, label=lbl, color=colors[i], linewidth=lw, alpha=0.85)
        ax.set_title(f"{ticker} — Moving Average Analysis", fontsize=13, fontweight='bold')
        ax.set_ylabel('Price ($)'); ax.legend(loc='best', fontsize=9); ax.grid(True, alpha=0.3)
        plt.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self.ma_chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  TAB 4 — Market Overview (unchanged)
    # ══════════════════════════════════════════════════════════════════════════

    def _build_market_tab(self, parent):
        ctrl = tk.Frame(parent, bg='#f5f5f5')
        ctrl.grid(row=0, column=0, sticky='ew', pady=(0,4))
        tk.Button(ctrl, text="Refresh", bg='#4A90E2', fg='white',
                  font=('Arial', 10, 'bold'), padx=10, pady=3, relief='flat',
                  cursor='hand2', command=self._refresh_market).pack(side=tk.LEFT, padx=5, pady=4)
        self.market_status = tk.Label(ctrl, text="Loading...", bg='#f5f5f5',
                                      fg='#333333', font=('Arial', 10))
        self.market_status.pack(side=tk.LEFT, padx=10)
        self.vix_context_var = tk.StringVar(value="")
        self.vix_context_bar = tk.Label(parent, textvariable=self.vix_context_var,
                                         bg='#f0f4ff', fg='#333333',
                                         font=('Arial', 10, 'bold'),
                                         anchor='w', padx=12, pady=4, relief='flat', bd=1)
        self.vix_context_bar.grid(row=1, column=0, sticky='ew', pady=(0,6))
        self.market_cards_frame = tk.Frame(parent, bg='#f0f0f0')
        self.market_cards_frame.grid(row=2, column=0, sticky='ew', pady=(0,8))
        heatmap_outer = ttk.LabelFrame(parent, text="  Watchlist Heatmap  --  % Change Today  (tile size = LEAP score)", padding="8")
        heatmap_outer.grid(row=3, column=0, sticky='ew', pady=(0,8))
        self.heatmap_frame = tk.Frame(heatmap_outer, bg='white', height=160)
        self.heatmap_frame.pack(fill=tk.BOTH, expand=True)
        self.heatmap_detail = tk.Label(heatmap_outer, text="Click a tile for detail",
                                        bg='white', fg='#555555', font=('Arial', 9))
        self.heatmap_detail.pack(anchor='w', pady=(4,0))
        watch_frame = ttk.LabelFrame(parent, text="  Top 5 by LEAP Score  ", padding="8")
        watch_frame.grid(row=4, column=0, sticky='ew', pady=(0,8))
        watch_cols = ('Ticker','Price','LEAP Score','Signal','MA+RSI')
        self.market_watch_tree = ttk.Treeview(watch_frame, columns=watch_cols,
                                               show='headings', height=5)
        for col, w in zip(watch_cols, [80,100,120,160,110]):
            self.market_watch_tree.heading(col, text=col)
            self.market_watch_tree.column(col, width=w, anchor='center')
        self.market_watch_tree.tag_configure('strong', background='#CCFFCC', foreground='#006600')
        self.market_watch_tree.tag_configure('alert',  background='#FFF3CC', foreground='#996600')
        self.market_watch_tree.tag_configure('normal', background='#FFFFFF')
        self.market_watch_tree.pack(fill=tk.X)
        parent.columnconfigure(0, weight=1)
        threading.Thread(target=self._load_market_data, daemon=True).start()

    def _load_market_data(self):
        self.root.after(0, lambda: self.market_status.config(text="Loading market data...", fg='#4A90E2'))
        market_status_str, time_labels = get_market_info()
        cards_data = []
        vix_price = None
        vix_week_ago = None
        for sym, name in MARKET_INSTRUMENTS:
            try:
                hist = yf.Ticker(sym).history(period="1mo")
                if len(hist) < 2: continue
                price = float(hist['Close'].iloc[-1])
                prev  = float(hist['Close'].iloc[-2])
                chg   = ((price - prev) / prev) * 100
                if name == 'VIX':
                    vix_price = price
                    if len(hist) >= 6:
                        vix_week_ago = float(hist['Close'].iloc[-6])  # ~5 trading days ago
                vals  = [float(v) for v in hist['Close'].values[-30:]]
                spark_bytes = make_sparkline_image(vals, chg)
                cards_data.append((sym, name, price, chg, spark_bytes))
            except Exception as e:
                print(f"Market data error {sym}: {e}")
        heatmap_data = []
        crypto_syms = [('BTC-USD','BTC'), ('ETH-USD','ETH'), ('KAS-USD','KAS')]
        heat_tickers = [(t, t) for t in self.tickers] + crypto_syms
        for sym, label in heat_tickers:
            try:
                hist = yf.Ticker(sym).history(period="2d")
                if len(hist) < 2: continue
                price = float(hist['Close'].iloc[-1])
                prev  = float(hist['Close'].iloc[-2])
                chg   = ((price - prev) / prev) * 100
                score = self.leap_data_cache.get(sym, {}).get('score', 8)
                heatmap_data.append((label, price, chg, score))
            except Exception as e:
                print(f"Heatmap error {sym}: {e}")
        self.root.after(0, lambda: self._populate_market_cards(
            cards_data, market_status_str, time_labels, heatmap_data, vix_price, vix_week_ago))

    def _populate_market_cards(self, cards_data, market_status_str="", time_labels=None,
                               heatmap_data=None, vix_price=None, vix_week_ago=None):
        if time_labels is None: time_labels = ['T', '1P', '3P']
        if vix_price is not None:
            vl = vix_label(vix_price)
            if vix_price < 15: tip, bg_color = "Options premiums cheap. Good time to buy LEAPs.", '#e8f5e9'
            elif vix_price < 20: tip, bg_color = "Normal conditions. Standard sizing.", '#f5f5f5'
            elif vix_price < 30: tip, bg_color = "Elevated fear. Premiums rising but entry improving.", '#fff8e1'
            elif vix_price < 40: tip, bg_color = "Panic levels. Premiums expensive but potential bottom.", '#fce4ec'
            else: tip, bg_color = "Extreme panic. Strong contrarian LEAP signal if fundamentals intact.", '#f8d7da'
            # Trend direction from week ago
            trend = ""
            if vix_week_ago is not None:
                vix_chg = vix_price - vix_week_ago
                vix_pct = (vix_chg / vix_week_ago) * 100
                arrow = "↓" if vix_chg < 0 else "↑"
                trend = f"  {arrow} from {vix_week_ago:.1f} ({vix_pct:+.0f}% wk)"
                if vix_chg < -3:
                    tip = "Fear easing. Premiums improving — better LEAP entries ahead."
                elif vix_chg > 3:
                    tip = "Fear spiking. Wait for VIX to stabilize before new LEAPs."
            self.vix_context_var.set(f"  Market Context:  VIX {vix_price:.1f}{trend}  --  {vl}  |  {tip}")
            self.vix_context_bar.config(bg=bg_color)
        for w in self.market_cards_frame.winfo_children(): w.destroy()
        for col_i, (sym, name, price, chg, spark_bytes) in enumerate(cards_data):
            color = '#00AA00' if chg >= 0 else '#CC0000'
            card = tk.Frame(self.market_cards_frame, bg='white', relief='raised', bd=1)
            card.grid(row=0, column=col_i, padx=6, pady=4, sticky='nsew')
            self.market_cards_frame.columnconfigure(col_i, weight=1)
            display_name = f"VIX  —  {vix_label(price)}" if name == 'VIX' else name
            tk.Label(card, text=display_name, bg='white', fg='#555555', font=('Arial', 9)).pack(anchor='w', padx=8, pady=(6,0))
            price_str = f"${price:,.2f}" if price >= 1000 else f"${price:.2f}" if price >= 1 else f"${price:.4f}"
            tk.Label(card, text=price_str, bg='white', fg='#111111', font=('Arial', 13, 'bold')).pack(anchor='w', padx=8)
            tk.Label(card, text=f"{chg:+.2f}%", bg='white', fg=color, font=('Arial', 11, 'bold')).pack(anchor='w', padx=8, pady=(0,2))
            if spark_bytes:
                try:
                    img = Image.open(io.BytesIO(spark_bytes))
                    photo = ImageTk.PhotoImage(img)
                    lbl = tk.Label(card, image=photo, bg='white')
                    lbl.image = photo; lbl.pack(padx=4, pady=(0,0))
                    self._spark_images[sym] = photo
                except Exception as e: print(f"Sparkline error {sym}: {e}")
            tl_frame = tk.Frame(card, bg='white')
            tl_frame.pack(fill=tk.X, padx=6, pady=(0,6))
            for i, lbl_text in enumerate(time_labels):
                side = tk.LEFT if i == 0 else tk.RIGHT if i == len(time_labels)-1 else tk.LEFT
                tk.Label(tl_frame, text=lbl_text, bg='white', fg='#999999',
                         font=('Arial', 8)).pack(side=side, expand=(0 < i < len(time_labels)-1))
        if heatmap_data:
            try:
                for w in self.heatmap_frame.winfo_children(): w.destroy()
                def chg_to_color(chg):
                    if chg >= 4: return '#006400', 'white'
                    elif chg >= 2: return '#4CAF50', 'white'
                    elif chg >= 0: return '#A5D6A7', '#111111'
                    elif chg >= -2: return '#EF9A9A', '#111111'
                    elif chg >= -4: return '#E53935', 'white'
                    else: return '#8B0000', 'white'
                cols = 5
                for i, (label, price, chg, score) in enumerate(heatmap_data):
                    row_i, col_i = i // cols, i % cols
                    bg, fg = chg_to_color(chg)
                    tile_w = max(80, min(160, 80 + score * 4))
                    tile = tk.Frame(self.heatmap_frame, bg=bg, relief='flat', bd=1, width=tile_w, height=70)
                    tile.grid(row=row_i, column=col_i, padx=2, pady=2, sticky='nsew')
                    tile.grid_propagate(False)
                    self.heatmap_frame.columnconfigure(col_i, weight=1)
                    tk.Label(tile, text=label, bg=bg, fg=fg, font=('Arial', 11, 'bold')).pack(pady=(8,0))
                    tk.Label(tile, text=f"{chg:+.2f}%", bg=bg, fg=fg, font=('Arial', 10)).pack()
                    if score != 8 or label in self.tickers:
                        sc_lbl = self.leap_data_cache.get(label, {}).get('score')
                        if sc_lbl:
                            tk.Label(tile, text=f"{sc_lbl}/{MAX_SCORE}", bg=bg, fg=fg, font=('Arial', 8)).pack()
                    def on_click(e, lbl=label, p=price, c=chg, sc=score):
                        r = self.leap_data_cache.get(lbl)
                        if r:
                            self.heatmap_detail.config(text=f"  {lbl}  ${p:.2f}  {c:+.2f}%  |  LEAP: {sc}/{MAX_SCORE}  |  {r.get('signal','')}  |  {r.get('ma_rsi_signal','')}")
                        else:
                            self.heatmap_detail.config(text=f"  {lbl}  ${p:.4f if p < 1 else p:.2f}  {c:+.2f}%")
                    tile.bind('<Button-1>', on_click)
                    for child in tile.winfo_children(): child.bind('<Button-1>', on_click)
            except Exception as e: print(f"Heatmap error: {e}")
        try:
            self.market_watch_tree.delete(*self.market_watch_tree.get_children())
            if self.leap_data_cache:
                top5 = sorted(self.leap_data_cache.values(), key=lambda r: -r['score'])[:5]
                for r in top5:
                    tag = 'strong' if r['score'] >= STRONG_THRESHOLD else ('alert' if 'ALERT' in r['signal'] else 'normal')
                    self.market_watch_tree.insert('', tk.END, values=(
                        r['ticker'], f"${r['price']}", f"{r['score']} / {MAX_SCORE}",
                        r['signal'], r.get('ma_rsi_signal','—')
                    ), tags=(tag,))
        except Exception as e: print(f"Watch tree error: {e}")
        self.market_status.config(
            text=f"{market_status_str}  —  Updated: {datetime.now().strftime('%H:%M:%S')}", fg='#00AA00')

    def _refresh_market(self):
        threading.Thread(target=self._load_market_data, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════════
    #  Shared utilities
    # ══════════════════════════════════════════════════════════════════════════

    def _sort_column(self, tree, col, sort_attr):
        store = getattr(self, sort_attr)
        reverse = store.get(col, False)
        # Columns where sorting should be by proximity (closer to 0 = better)
        proximity_cols = {'vs S2', 'vs S3'}
        is_proximity = col in proximity_cols
        data = [(tree.set(k, col), k) for k in tree.get_children('')]
        def key(x):
            v = x[0].replace('$','').replace('%','').replace(',','').replace('◄','').replace('★','').strip()
            v = v.split()[0] if v else '0'
            try:
                num = float(v)
                return abs(num) if is_proximity else num
            except: return x[0].lower()
        data.sort(key=key, reverse=reverse)
        for i, (_, k) in enumerate(data): tree.move(k, '', i)
        store[col] = not reverse
        arrow = ' ▼' if reverse else ' ▲'
        for c in tree['columns']:
            tree.heading(c, text=c + (arrow if c == col else ''),
                command=lambda cc=c: self._sort_column(tree, cc, sort_attr))

    # ══════════════════════════════════════════════════════════════════════════
    #  Market Pulse (unchanged)
    # ══════════════════════════════════════════════════════════════════════════

    def _launch_pulse_window(self):
        self.pulse = tk.Toplevel(self.root)
        self.pulse.title("Market Pulse")
        self.pulse.geometry("420x300+20+20")
        self.pulse.resizable(False, False)
        self.pulse.attributes('-topmost', True)
        self.pulse.configure(bg='#f5f5f5')
        self.pulse.protocol("WM_DELETE_WINDOW", self._hide_pulse)
        hdr = tk.Frame(self.pulse, bg='#f5f5f5')
        hdr.pack(fill=tk.X, padx=8, pady=(6,2))
        tk.Label(hdr, text="📊 Market Pulse", bg='#f5f5f5', fg='#333333',
                 font=('Arial', 12, 'bold')).pack(side=tk.LEFT)
        tk.Button(hdr, text="🔄", bg='#f5f5f5', fg='#333333',
                  font=('Arial', 10), relief='flat', cursor='hand2',
                  command=self._refresh_pulse).pack(side=tk.RIGHT)
        self.pulse_status = tk.Label(self.pulse, text="Loading...",
                                     bg='#f5f5f5', fg='#333333', font=('Arial', 8))
        self.pulse_status.pack(anchor='w', padx=10)
        tk.Frame(self.pulse, bg='#e8e8e8', height=1).pack(fill=tk.X, padx=8, pady=4)
        idx_frame = tk.Frame(self.pulse, bg='#f5f5f5')
        idx_frame.pack(fill=tk.X, padx=8, pady=2)
        self.pulse_idx_labels = {}
        for col, sym in enumerate(['^DJI', '^IXIC', '^GSPC']):
            f = tk.Frame(idx_frame, bg='#ffffff', relief='flat', bd=0)
            f.grid(row=0, column=col, padx=3, sticky='nsew')
            idx_frame.columnconfigure(col, weight=1)
            name_map = {'^DJI':'Dow Jones', '^IXIC':'Nasdaq', '^GSPC':'S&P 500'}
            tk.Label(f, text=name_map[sym], bg='#ffffff', fg='#555555', font=('Arial', 8)).pack(anchor='w', padx=6, pady=(4,0))
            price_lbl = tk.Label(f, text="—", bg='#ffffff', fg='#333333', font=('Arial', 11, 'bold'))
            price_lbl.pack(anchor='w', padx=6)
            chg_lbl = tk.Label(f, text="—", bg='#ffffff', fg='#999999', font=('Arial', 9))
            chg_lbl.pack(anchor='w', padx=6, pady=(0,4))
            self.pulse_idx_labels[sym] = (price_lbl, chg_lbl)
        tk.Frame(self.pulse, bg='#e8e8e8', height=1).pack(fill=tk.X, padx=8, pady=4)
        row2 = tk.Frame(self.pulse, bg='#f5f5f5')
        row2.pack(fill=tk.X, padx=8, pady=2)
        self.pulse_row2_labels = {}
        for col, (sym, name) in enumerate([('^VIX','VIX'), ('BTC-USD','BTC'), ('ETH-USD','ETH'), ('KAS-USD','KAS')]):
            f = tk.Frame(row2, bg='#ffffff')
            f.grid(row=0, column=col, padx=3, sticky='nsew')
            row2.columnconfigure(col, weight=1)
            tk.Label(f, text=name, bg='#ffffff', fg='#555555', font=('Arial', 8)).pack(anchor='w', padx=5, pady=(3,0))
            val_lbl = tk.Label(f, text="—", bg='#ffffff', fg='#333333', font=('Arial', 9, 'bold'))
            val_lbl.pack(anchor='w', padx=5)
            sub_lbl = tk.Label(f, text="—", bg='#ffffff', fg='#777777', font=('Arial', 8))
            sub_lbl.pack(anchor='w', padx=5, pady=(0,3))
            self.pulse_row2_labels[sym] = (val_lbl, sub_lbl)
        tk.Frame(self.pulse, bg='#e8e8e8', height=1).pack(fill=tk.X, padx=8, pady=4)
        leap_frame = tk.Frame(self.pulse, bg='#f5f5f5')
        leap_frame.pack(fill=tk.X, padx=8, pady=(0,6))
        tk.Label(leap_frame, text="⭐ Top LEAP:", bg='#f5f5f5', fg='#333333',
                 font=('Arial', 9, 'bold')).pack(side=tk.LEFT)
        self.pulse_leap_lbl = tk.Label(leap_frame, text="(loading...)",
                                        bg='#f5f5f5', fg='#006600', font=('Arial', 9, 'bold'))
        self.pulse_leap_lbl.pack(side=tk.LEFT, padx=6)
        threading.Thread(target=self._load_pulse_data, daemon=True).start()
        self._pulse_refresh_loop()

    def _load_pulse_data(self):
        data = {}
        for sym in ['^DJI','^IXIC','^GSPC','^VIX','BTC-USD','ETH-USD','KAS-USD']:
            try:
                hist = yf.Ticker(sym).history(period="2d")
                if len(hist) < 2: continue
                price = float(hist['Close'].iloc[-1])
                prev  = float(hist['Close'].iloc[-2])
                data[sym] = (price, ((price - prev) / prev) * 100)
            except: pass
        status_str, _ = get_market_info()
        self.root.after(0, lambda: self._update_pulse_ui(data, status_str))

    def _update_pulse_ui(self, data, status_str):
        try:
            self.pulse_status.config(text=f"{status_str}  —  {datetime.now().strftime('%H:%M:%S')}")
            for sym, (price_lbl, chg_lbl) in self.pulse_idx_labels.items():
                if sym in data:
                    price, chg = data[sym]
                    color = '#00AA00' if chg >= 0 else '#CC0000'
                    price_lbl.config(text=f"${price:,.2f}", fg='#111111')
                    chg_lbl.config(text=f"{chg:+.2f}% {'▲' if chg >= 0 else '▼'}", fg=color)
            for sym, (val_lbl, sub_lbl) in self.pulse_row2_labels.items():
                if sym in data:
                    price, chg = data[sym]
                    color = '#00AA00' if chg >= 0 else '#CC0000'
                    if sym == '^VIX':
                        val_lbl.config(text=f"{price:.1f}", fg='#CC6600')
                        sub_lbl.config(text=f"{chg:+.1f}%  {vix_label(price)}", fg='#666666')
                    elif price >= 1000:
                        val_lbl.config(text=f"${price:,.0f}", fg='#111111')
                        sub_lbl.config(text=f"{chg:+.2f}% {'▲' if chg >= 0 else '▼'}", fg=color)
                    elif price >= 1:
                        val_lbl.config(text=f"${price:.2f}", fg='#111111')
                        sub_lbl.config(text=f"{chg:+.2f}% {'▲' if chg >= 0 else '▼'}", fg=color)
                    else:
                        val_lbl.config(text=f"${price:.4f}", fg='#111111')
                        sub_lbl.config(text=f"{chg:+.2f}% {'▲' if chg >= 0 else '▼'}", fg=color)
            if self.leap_data_cache:
                top3 = sorted(self.leap_data_cache.values(), key=lambda r: -r['score'])[:3]
                self.pulse_leap_lbl.config(text='   '.join([f"{r['ticker']} {r['score']}/{MAX_SCORE}" for r in top3]))
        except Exception as e: print(f"Pulse UI error: {e}")

    def _refresh_pulse(self):
        threading.Thread(target=self._load_pulse_data, daemon=True).start()

    def _hide_pulse(self):
        self.pulse.withdraw()

    def _pulse_refresh_loop(self):
        def loop():
            while True:
                time.sleep(300)
                try:
                    if self.pulse.winfo_exists(): self._refresh_pulse()
                except: break
        threading.Thread(target=loop, daemon=True).start()

    def _auto_refresh(self):
        def loop():
            while True:
                time.sleep(1800)
                self.root.after(0, self._start_tracker_load)
        threading.Thread(target=loop, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════════
    #  Chart window (unchanged)
    # ══════════════════════════════════════════════════════════════════════════

    def _show_chart(self, event):
        sel = self.tree.selection()
        if not sel: return
        ticker = self.tree.item(sel[0])['values'][0]
        win = tk.Toplevel(self.root)
        win.title(f"{ticker} - Interactive Chart")
        win.geometry("1200x900")
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        btn_frame = tk.Frame(win, bg='#f0f0f0')
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(btn_frame, text=ticker, bg='#f0f0f0', fg='#111111',
                 font=('Arial', 14, 'bold')).pack(side=tk.LEFT, padx=10)
        periods = {'1D':'1d','1W':'5d','1M':'1mo','3M':'3mo','1Y':'1y','2Y':'2y','3Y':'3y','MAX':'max'}
        chart = {'fig': None, 'ax1': None, 'ax2': None, 'canvas': None,
                 'press_x': None, 'press_xlim': None, 'press_ax': None}
        chart['fig'], (chart['ax1'], chart['ax2']) = plt.subplots(
            2, 1, figsize=(12,9), gridspec_kw={'height_ratios':[3,1]})
        plt.subplots_adjust(hspace=0.3)
        chart['canvas'] = FigureCanvasTkAgg(chart['fig'], master=win)
        def update_chart(period, yf_period):
            chart['ax1'].cla(); chart['ax2'].cla()
            stock = yf.Ticker(ticker)
            if period == '1D': hist = stock.history(period='1d', interval='5m')
            elif period == '1W': hist = stock.history(period='5d', interval='30m')
            else: hist = stock.history(period=yf_period)
            if hist.empty: return
            chart['ax1'].plot(hist.index, hist['Close'], linewidth=2, color='#2196F3', label='Price')
            if period not in ['1D','1W']:
                for n, c in [(20,'orange'),(50,'green'),(200,'red')]:
                    if len(hist) >= n:
                        chart['ax1'].plot(hist.index, hist['Close'].rolling(n).mean(), label=f'{n}-MA', alpha=0.8, color=c)
            s3,s2,s1,r1,r2,r3 = calc_pivot_points(ticker)
            for val, lbl, c, ls in [(s1,'S1','red','--'),(s2,'S2','red',':'),(s3,'S3','red','-.'),(r1,'R1','green','--'),(r2,'R2','green',':'),(r3,'R3','green','-.') ]:
                if val: chart['ax1'].axhline(y=val, color=c, linestyle=ls, alpha=0.5, label=f'{lbl}:{val}')
            chart['ax1'].set_title(f'{ticker} — {period}', fontsize=14, fontweight='bold')
            chart['ax1'].set_ylabel('Price ($)', fontsize=12)
            chart['ax1'].legend(loc='best', fontsize=8); chart['ax1'].grid(True, alpha=0.3)
            colors = ['green' if c >= o else 'red' for c, o in zip(hist['Close'], hist['Open'])]
            chart['ax2'].bar(hist.index, hist['Volume'], color=colors, alpha=0.6)
            chart['ax2'].set_ylabel('Volume', fontsize=12); chart['ax2'].grid(True, alpha=0.3)
            fmt = mdates.DateFormatter('%H:%M' if period == '1D' else '%m/%d' if period == '1W' else '%Y-%m')
            chart['ax2'].xaxis.set_major_formatter(fmt)
            plt.setp(chart['ax2'].xaxis.get_majorticklabels(), rotation=45)
            chart['canvas'].draw()
            for p, b in period_buttons.items(): b.config(bg='#4A90E2' if p == period else '#34495E')
        period_buttons = {}
        for lbl, val in periods.items():
            b = tk.Button(btn_frame, text=lbl, bg='#34495E', fg='white',
                          font=('Arial',10,'bold'), padx=10, pady=5, relief='flat', cursor='hand2',
                          command=lambda p=lbl, v=val: update_chart(p, v))
            b.pack(side=tk.LEFT, padx=2); period_buttons[lbl] = b
        tk.Button(btn_frame, text='Reset', bg='#E74C3C', fg='white',
                  font=('Arial',10,'bold'), padx=10, pady=5, relief='flat', cursor='hand2',
                  command=lambda: update_chart('3M','3mo')).pack(side=tk.RIGHT, padx=10)
        tk.Label(btn_frame, text="Drag to pan  |  Scroll to zoom",
                 bg='#f0f0f0', fg='#555555', font=('Arial',9)).pack(side=tk.RIGHT, padx=10)
        chart['canvas'].get_tk_widget().pack(fill=tk.BOTH, expand=True)
        def on_press(e):
            if e.inaxes and e.button == 1:
                chart['press_x'] = e.xdata; chart['press_xlim'] = e.inaxes.get_xlim(); chart['press_ax'] = e.inaxes
        def on_motion(e):
            if chart['press_x'] and e.inaxes == chart['press_ax'] and e.xdata:
                dx = e.xdata - chart['press_x']; xl = chart['press_xlim']
                chart['press_ax'].set_xlim(xl[0]-dx, xl[1]-dx); chart['canvas'].draw_idle()
            if e.inaxes in [chart['ax1'], chart['ax2']] and e.xdata and e.ydata:
                for attr in ['_hl','_vl1','_vl2','_pl','_dl']:
                    if attr in chart:
                        try: chart[attr].remove()
                        except: pass
                chart['_vl1'] = chart['ax1'].axvline(x=e.xdata, color='black', linewidth=1.5, linestyle='--')
                chart['_vl2'] = chart['ax2'].axvline(x=e.xdata, color='black', linewidth=1.5, linestyle='--')
                chart['_hl']  = chart['ax1'].axhline(y=e.ydata, color='black', linewidth=1.5, linestyle='--')
                chart['_pl']  = chart['ax1'].text(chart['ax1'].get_xlim()[0], e.ydata,
                    f' ${e.ydata:.2f}', color='white', backgroundcolor='#2196F3',
                    fontsize=9, fontweight='bold', va='center', zorder=5)
                try: ds = mdates.num2date(e.xdata).strftime('%Y-%m-%d')
                except: ds = ''
                chart['_dl'] = chart['ax2'].text(e.xdata, chart['ax2'].get_ylim()[0],
                    f' {ds} ', color='white', backgroundcolor='#2196F3',
                    fontsize=9, fontweight='bold', ha='center', va='bottom', zorder=5)
                chart['canvas'].draw_idle()
        def on_release(e): chart['press_x'] = None
        def on_scroll(e):
            if e.inaxes:
                ax = e.inaxes; xl, yl = ax.get_xlim(), ax.get_ylim()
                sc = 0.9 if e.button == 'up' else 1.1
                ax.set_xlim([e.xdata-(e.xdata-xl[0])*sc, e.xdata+(xl[1]-e.xdata)*sc])
                ax.set_ylim([e.ydata-(e.ydata-yl[0])*sc, e.ydata+(yl[1]-e.ydata)*sc])
                chart['canvas'].draw_idle()
        chart['fig'].canvas.mpl_connect('button_press_event', on_press)
        chart['fig'].canvas.mpl_connect('button_release_event', on_release)
        chart['fig'].canvas.mpl_connect('motion_notify_event', on_motion)
        chart['fig'].canvas.mpl_connect('scroll_event', on_scroll)
        update_chart('3M', '3mo')


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app  = StockTracker(root)
    root.mainloop()
