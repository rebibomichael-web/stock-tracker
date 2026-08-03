from flask import Flask, jsonify, render_template_string
import yfinance as yf
from datetime import datetime
import threading
import time
import requests
from bs4 import BeautifulSoup
import re

try:
    import leverage_monitor
except Exception:
    leverage_monitor = None

app = Flask(__name__)

TICKERS = ['CRWD', 'ORCL', 'SNOW', 'SSYS', 'LMND', 'PLTR', 'BMNR', 'TSLA', 'NVDA', 'GRNY', 'DE', 'MU', 'NVMI', 'SOFI', 'HOOD', 'NOW']

cache = {
    'tracker': [],
    'leap': [],
    'leverage': None,
    'tracker_updated': None,
    'leap_updated': None,
    'leverage_updated': None,
    'leap_loading': False
}

# ─── PIVOT CALCULATIONS ───────────────────────────────────────────────

def calc_daily_pivots(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if len(hist) < 2:
            return [None]*6
        h = hist['High'].iloc[-2]
        l = hist['Low'].iloc[-2]
        c = hist['Close'].iloc[-2]
        p = (h + l + c) / 3
        return [round(2*p-h, 2), round(p-(h-l), 2), round(2*p-l, 2),  # s3,s2,s1... wait
                round(2*p-h, 2), round(p-(h-l), 2), round(p-2*(h-l), 2)]
    except:
        return [None]*6

def calc_pivots_correct(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if len(hist) < 2:
            return None, None, None, None, None, None
        h = hist['High'].iloc[-2]
        l = hist['Low'].iloc[-2]
        c = hist['Close'].iloc[-2]
        p = (h + l + c) / 3
        r1 = round(2*p - l, 2)
        r2 = round(p + (h - l), 2)
        r3 = round(p + 2*(h - l), 2)
        s1 = round(2*p - h, 2)
        s2 = round(p - (h - l), 2)
        s3 = round(p - 2*(h - l), 2)
        return s3, s2, s1, r1, r2, r3
    except:
        return None, None, None, None, None, None

def calc_weekly_pivots(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="1mo", interval="1wk")
        if len(hist) < 2:
            return None, None
        h = hist['High'].iloc[-2]
        l = hist['Low'].iloc[-2]
        c = hist['Close'].iloc[-2]
        p = (h + l + c) / 3
        return round(p - (h - l), 2), round(p - 2*(h - l), 2)
    except:
        return None, None

# ─── BARCHART ─────────────────────────────────────────────────────────

def get_barchart(ticker):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
        url = f'https://www.barchart.com/stocks/quotes/{ticker}/opinion'
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        pct = (soup.find('span', class_='opinion-percent buy') or
               soup.find('span', class_='opinion-percent sell') or
               soup.find('span', class_='opinion-percent hold'))
        sig = (soup.find('span', class_='opinion-signal buy') or
               soup.find('span', class_='opinion-signal sell') or
               soup.find('span', class_='opinion-signal hold'))
        graphs = soup.find('div', class_='opinion-graphs')
        opinion = f"{pct.get_text(strip=True)} {sig.get_text(strip=True)}" if pct else 'N/A'
        strength = direction = 'N/A'
        if graphs:
            txt = graphs.get_text(strip=True)
            if 'Strength:' in txt and 'Direction:' in txt:
                parts = txt.replace('Strength:', '').replace('Direction:', '|').split('|')
                strength = parts[0].strip() if parts else 'N/A'
                direction = parts[1].strip() if len(parts) > 1 else 'N/A'
        yesterday = last_week = last_month = 'N/A'
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

# ─── LEAP HELPERS ─────────────────────────────────────────────────────

def get_ath_and_52w(ticker):
    try:
        stock = yf.Ticker(ticker)
        hmax = stock.history(period="max")
        h1y  = stock.history(period="1y")
        ath  = round(hmax['High'].max(), 2) if len(hmax) else None
        w52h = round(h1y['High'].max(), 2)  if len(h1y)  else None
        return ath, w52h
    except:
        return None, None

def get_leaps(ticker, ath):
    try:
        stock = yf.Ticker(ticker)
        exps  = stock.options
        today = datetime.today()
        best = furthest = None
        furthest_dte = 0
        for exp_str in exps:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
            dte = (exp_date - today).days
            if dte < 540:
                continue
            chain = stock.option_chain(exp_str).calls
            if chain.empty:
                continue
            chain = chain[chain['strike'] > 0].copy()
            chain['dist'] = abs(chain['strike'] - ath)
            row = chain.loc[chain['dist'].idxmin()]
            strike = row['strike']
            prem   = row['lastPrice'] if row['lastPrice'] > 0 else row['ask']
            iv     = round(row['impliedVolatility'] * 100, 1) if row['impliedVolatility'] else None
            if prem and prem > 0:
                if best is None or abs(strike - ath) < abs(best['strike'] - ath):
                    best = {'strike': strike, 'premium': round(prem, 2), 'dte': dte, 'exp': exp_str, 'iv': iv}
                if dte > furthest_dte:
                    furthest_dte = dte
                    furthest = {'strike': strike, 'premium': round(prem, 2), 'dte': dte, 'exp': exp_str, 'iv': iv}
        return best, furthest
    except:
        return None, None

def score_leap(price, ath, prem_pct, leverage, vs_s2, vs_s3, dte):
    score = 0
    bd = {}
    # Drawdown from ATH
    if ath and price:
        d = ((ath - price) / ath) * 100
        p1 = 3 if d >= 30 else (2 if d >= 15 else 1)
    else:
        p1 = 0
    score += p1; bd['ATH Drawdown'] = p1
    # Prem efficiency
    if prem_pct is not None:
        p2 = 3 if prem_pct < 10 else (2 if prem_pct < 15 else 1)
    else:
        p2 = 0
    score += p2; bd['Prem Efficiency'] = p2
    # Leverage
    if leverage is not None:
        p3 = 3 if leverage >= 8 else (2 if leverage >= 4 else 1)
    else:
        p3 = 0
    score += p3; bd['Leverage'] = p3
    # DTE
    if dte:
        p4 = 3 if dte >= 720 else (2 if dte >= 540 else 1)
    else:
        p4 = 0
    score += p4; bd['Time Horizon'] = p4
    # S2/S3
    if vs_s3 is not None and abs(vs_s3) <= 5:
        p5 = 3
    elif vs_s2 is not None and abs(vs_s2) <= 5:
        p5 = 2
    elif vs_s2 is not None and abs(vs_s2) <= 15:
        p5 = 1
    else:
        p5 = 0
    score += p5; bd['S2/S3 Level'] = p5
    return score, bd

# ─── DATA REFRESH THREADS ─────────────────────────────────────────────

def refresh_tracker():
    rows = []
    for ticker in TICKERS:
        try:
            stock = yf.Ticker(ticker)
            hist  = stock.history(period="2d")
            if len(hist) == 0:
                continue
            price    = round(hist['Close'].iloc[-1], 2)
            prev     = round(hist['Close'].iloc[-2], 2) if len(hist) > 1 else price
            chg_pct  = round(((price - prev) / prev) * 100, 2)
            s3, s2, s1, r1, r2, r3 = calc_pivots_correct(ticker)
            levels = [(s3,'s3'),(s2,'s2'),(s1,'s1'),(r1,'r1'),(r2,'r2'),(r3,'r3')]
            valid  = [(l,n) for l,n in levels if l is not None]
            below  = [(l,n) for l,n in valid if l < price]
            above  = [(l,n) for l,n in valid if l > price]
            below_lvl = max(below, key=lambda x: x[0])[1] if below else (min(valid, key=lambda x: x[0])[1] if valid else None)
            above_lvl = min(above, key=lambda x: x[0])[1] if above else (max(valid, key=lambda x: x[0])[1] if valid else None)
            try:
                opinion, strength, direction, yesterday, last_week, last_month = get_barchart(ticker)
                time.sleep(1)
            except:
                opinion = strength = direction = yesterday = last_week = last_month = 'N/A'
            rows.append({
                'ticker': ticker, 'price': price, 'chg_pct': chg_pct,
                's3': s3, 's2': s2, 's1': s1, 'r1': r1, 'r2': r2, 'r3': r3,
                'below': below_lvl, 'above': above_lvl,
                'opinion': opinion, 'strength': strength, 'direction': direction,
                'yesterday': yesterday, 'last_week': last_week, 'last_month': last_month,
                'updated': datetime.now().strftime("%H:%M:%S")
            })
            # Update cache after each ticker so page shows partial data
            cache['tracker'] = rows.copy()
            cache['tracker_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            print(f"Tracker error {ticker}: {e}")
    cache['tracker'] = rows
    cache['tracker_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def refresh_leap():
    if cache['leap_loading']:
        return
    cache['leap_loading'] = True
    rows = []
    for ticker in TICKERS:
        try:
            stock = yf.Ticker(ticker)
            hist  = stock.history(period="1d")
            if len(hist) == 0:
                continue
            price = round(hist['Close'].iloc[-1], 2)
            ath, w52h = get_ath_and_52w(ticker)
            ws2, ws3  = calc_weekly_pivots(ticker)
            leap, furthest = get_leaps(ticker, ath) if ath else (None, None)
            prem_pct = leverage = None
            if leap and price and leap['premium']:
                prem_pct = round((leap['premium'] / price) * 100, 1)
                leverage = round(price / leap['premium'], 1)
            vs_s2 = round(((price - ws2) / ws2) * 100, 1) if ws2 else None
            vs_s3 = round(((price - ws3) / ws3) * 100, 1) if ws3 else None
            pct_52w = round(((w52h - price) / w52h) * 100, 1) if w52h else None
            closest = None
            if vs_s2 is not None and vs_s3 is not None:
                closest = 's2' if abs(vs_s2) < abs(vs_s3) else 's3'
            elif vs_s2 is not None:
                closest = 's2'
            elif vs_s3 is not None:
                closest = 's3'
            sc, bd = score_leap(price, ath, prem_pct, leverage, vs_s2, vs_s3,
                                leap['dte'] if leap else None)
            if vs_s3 is not None and abs(vs_s3) <= 5:
                signal = 'S3 ALERT'
            elif vs_s2 is not None and abs(vs_s2) <= 5:
                signal = 'S2 ALERT'
            elif sc >= 12:
                signal = 'STRONG SETUP'
            elif sc >= 8:
                signal = 'MONITOR'
            else:
                signal = '—'
            rows.append({
                'ticker': ticker, 'price': price, 'w52h': w52h,
                'pct_52w': pct_52w, 'prem_pct': prem_pct, 'leverage': leverage,
                'ws2': ws2, 'ws3': ws3, 'vs_s2': vs_s2, 'vs_s3': vs_s3,
                'closest': closest, 'score': sc, 'breakdown': bd,
                'signal': signal, 'leap': leap, 'furthest': furthest
            })
        except Exception as e:
            print(f"LEAP error {ticker}: {e}")
    rows.sort(key=lambda r: -r['score'])
    cache['leap'] = rows
    cache['leap_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache['leap_loading'] = False

def refresh_leverage():
    """Margin dimmer + L-ETF frenzy flag (leverage_monitor). Failures are
    swallowed — the tracker must never depend on this."""
    if leverage_monitor is None:
        return
    try:
        regime = leverage_monitor.regime_state()
        margin = leverage_monitor.margin_state(regime)
        frenzy = leverage_monitor.frenzy_state(regime)
        cache['leverage'] = {'regime': regime, 'margin': margin, 'frenzy': frenzy}
        cache['leverage_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"Leverage monitor error: {e}")

def background_refresh():
    while True:
        refresh_tracker()
        refresh_leap()
        refresh_leverage()
        time.sleep(1800)

# ─── ROUTES ───────────────────────────────────────────────────────────

@app.route('/api/tracker')
def api_tracker():
    return jsonify({'data': cache['tracker'], 'updated': cache['tracker_updated']})

@app.route('/api/leap')
def api_leap():
    return jsonify({'data': cache['leap'], 'updated': cache['leap_updated'], 'loading': cache['leap_loading']})

@app.route('/api/refresh/leap', methods=['POST'])
def api_refresh_leap():
    threading.Thread(target=refresh_leap, daemon=True).start()
    return jsonify({'status': 'refreshing'})

@app.route('/api/leverage')
def api_leverage():
    return jsonify({'data': cache['leverage'], 'updated': cache['leverage_updated']})

HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Tracker</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; font-size: 13px; }
  header { background: #16213e; padding: 10px 16px; display: flex; align-items: center; gap: 12px; border-bottom: 2px solid #4A90E2; }
  header h1 { font-size: 16px; color: #4A90E2; }
  .tabs { display: flex; gap: 4px; }
  .tab { padding: 6px 16px; border-radius: 6px 6px 0 0; cursor: pointer; background: #2d2d44; color: #aaa; border: none; font-size: 13px; }
  .tab.active { background: #4A90E2; color: white; font-weight: bold; }
  .status { margin-left: auto; font-size: 11px; color: #888; }
  .alert-bar { background: #2C3E50; padding: 8px 16px; font-size: 12px; color: white; min-height: 32px; }
  .alert-bar .s3 { color: #ff6b6b; font-weight: bold; }
  .alert-bar .s2 { color: #ffd93d; font-weight: bold; }
  .legend { background: #1a1a2e; border-top: 1px solid #333; padding: 7px 16px; font-size: 11px; color: #A0C4FF; }
  .panel { display: none; }
  .panel.active { display: block; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; white-space: nowrap; }
  th { background: #4A90E2; color: white; padding: 8px 10px; text-align: center; position: sticky; top: 0; font-size: 12px; }
  td { padding: 7px 10px; text-align: center; border-bottom: 1px solid #2a2a3e; font-size: 12px; }
  tr.even { background: #1e1e30; }
  tr.odd  { background: #22223a; }
  tr.s3-alert { background: #3d1a1a !important; }
  tr.s2-alert { background: #3d3010 !important; }
  tr.strong   { background: #1a3d1a !important; }
  .pos { color: #00cc66; }
  .neg { color: #ff4444; }
  .arrow { color: #ffd93d; font-weight: bold; }
  .sig-s3 { color: #ff6b6b; font-weight: bold; }
  .sig-s2 { color: #ffd93d; font-weight: bold; }
  .sig-strong { color: #00cc66; font-weight: bold; }
  .sig-monitor { color: #aaa; }
  .detail-box { background: #16213e; border-top: 2px solid #4A90E2; padding: 12px 16px; font-family: monospace; font-size: 12px; color: #ccc; white-space: pre-wrap; min-height: 60px; }
  .refresh-btn { background: #4A90E2; color: white; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 12px; margin: 8px 16px; }
  .refresh-btn:hover { background: #357abd; }
  .loading { text-align: center; padding: 40px; color: #888; font-size: 14px; }
  @media (max-width: 600px) { td, th { font-size: 11px; padding: 5px 6px; } }
</style>
</head>
<body>
<header>
  <h1>📈 Stock Tracker</h1>
  <div class="tabs">
    <button class="tab active" onclick="showTab('tracker')">📈 Stock Tracker</button>
    <button class="tab" onclick="showTab('leap')">🎯 LEAP Scanner</button>
  </div>
  <span class="status" id="status">Loading...</span>
</header>
<div class="alert-bar" id="lev-bar" style="min-height:0;padding:5px 16px;font-size:11px;color:#A0C4FF;display:none"></div>

<!-- TRACKER PANEL -->
<div class="panel active" id="panel-tracker">
  <div class="table-wrap">
    <table id="tracker-table">
      <thead><tr>
        <th>Ticker</th><th>Price</th><th>Change %</th>
        <th>S3</th><th>S2</th><th>S1</th><th>R1</th><th>R2</th><th>R3</th>
        <th>Opinion</th><th>Strength</th><th>Direction</th>
        <th>Yesterday</th><th>Last Week</th><th>Last Month</th><th>Updated</th>
      </tr></thead>
      <tbody id="tracker-body"><tr><td colspan="16" class="loading">Loading stock data...</td></tr></tbody>
    </table>
  </div>
</div>

<!-- LEAP PANEL -->
<div class="panel" id="panel-leap">
  <div class="alert-bar" id="leap-alerts">⚡ Loading LEAP data...</div>
  <div class="legend">
    📊 <b>Prem Effcncy</b> = cents you pay per $1 of stock &nbsp;(8 = pay $8 to control $100 — lower is better) &nbsp;&nbsp;&nbsp;&nbsp;
    ⚡ <b>Leverage</b> = dollars of stock controlled per $1 spent &nbsp;(12 = your $10 moves like $120 — higher is better)
  </div>
  <button class="refresh-btn" onclick="refreshLeap()">🔄 Refresh LEAP Data</button>
  <div class="table-wrap">
    <table id="leap-table">
      <thead><tr>
        <th>Ticker</th><th>Price</th><th>52W High</th><th>% from 52W</th>
        <th>Prem Effcncy</th><th>Leverage</th>
        <th>Weekly S2</th><th>Weekly S3</th><th>vs S2</th><th>vs S3</th>
        <th>Furthest Exp</th><th>Furthest Prem</th>
        <th>LEAP Score /15</th><th>Signal</th>
      </tr></thead>
      <tbody id="leap-body"><tr><td colspan="14" class="loading">Loading LEAP data (may take 1-2 min)...</td></tr></tbody>
    </table>
  </div>
  <div class="detail-box" id="leap-detail">Click any row to see LEAP breakdown.</div>
</div>

<script>
let leapData = [];
let levState = null;

function loadLeverage() {
  fetch('/api/leverage').then(r => r.json()).then(d => {
    if (!d.data) return;
    levState = d.data;
    const m = d.data.margin, f = d.data.frenzy;
    let parts = ['⚖️ LEVERAGE — regime ' + d.data.regime];
    if (m) {
      parts.push(`margin YoY ${(m.yoy*100).toFixed(1)}% (streak ${m.streak}mo${m.decel ? ', DECELERATION' : ''})`);
      if (m.leap_multiplier < 1) parts.push(`<b style="color:#ffd93d">LEAP sizing ×${m.leap_multiplier}</b>`);
    }
    if (f && f.z != null) {
      parts.push(`L-ETF z ${f.z > 0 ? '+' : ''}${f.z}`);
      if (f.veto) parts.push('<b style="color:#ff6b6b">FRENZY VETO — bounce setups unreliable</b>');
    }
    const bar = document.getElementById('lev-bar');
    bar.innerHTML = parts.join(' &nbsp;·&nbsp; ');
    bar.style.display = 'block';
  }).catch(() => {});
}

function showTab(tab) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + tab).classList.add('active');
  event.target.classList.add('active');
}

function fmt(val, prefix='$') {
  return val != null ? prefix + val : 'N/A';
}

function signalClass(sig) {
  if (sig.includes('S3')) return 'sig-s3';
  if (sig.includes('S2')) return 'sig-s2';
  if (sig.includes('STRONG')) return 'sig-strong';
  if (sig.includes('MONITOR')) return 'sig-monitor';
  return '';
}

function rowClass(sig, score) {
  if (sig.includes('S3')) return 's3-alert';
  if (sig.includes('S2')) return 's2-alert';
  if (score >= 12) return 'strong';
  return '';
}

function loadTracker() {
  fetch('/api/tracker').then(r => r.json()).then(d => {
    const body = document.getElementById('tracker-body');
    if (!d.data.length) {
      body.innerHTML = '<tr><td colspan="16" class="loading">⏳ Fetching stock data... refreshing every 5 seconds</td></tr>';
      setTimeout(loadTracker, 5000);
      return;
    }
    body.innerHTML = d.data.map((r, i) => {
      const chgClass = r.chg_pct >= 0 ? 'pos' : 'neg';
      const rowCls = i % 2 === 0 ? 'even' : 'odd';
      function fmtLvl(val, name) {
        if (val == null) return 'N/A';
        const arrow = (name === r.below || name === r.above) ? ' ►' : '';
        return val + arrow;
      }
      return `<tr class="${rowCls}">
        <td><b>${r.ticker}</b></td>
        <td>$${r.price}</td>
        <td class="${chgClass}">${r.chg_pct > 0 ? '+' : ''}${r.chg_pct}%</td>
        <td>${fmtLvl(r.s3,'s3')}</td><td>${fmtLvl(r.s2,'s2')}</td><td>${fmtLvl(r.s1,'s1')}</td>
        <td>${fmtLvl(r.r1,'r1')}</td><td>${fmtLvl(r.r2,'r2')}</td><td>${fmtLvl(r.r3,'r3')}</td>
        <td>${r.opinion}</td><td>${r.strength}</td><td>${r.direction}</td>
        <td>${r.yesterday}</td><td>${r.last_week}</td><td>${r.last_month}</td>
        <td>${r.updated}</td>
      </tr>`;
    }).join('');
    document.getElementById('status').textContent = 'Updated: ' + d.updated;
    // Keep polling if not all tickers loaded yet
    if (d.data.length < 16) setTimeout(loadTracker, 5000);
  });
}

function loadLeap() {
  fetch('/api/leap').then(r => r.json()).then(d => {
    leapData = d.data;
    const body = document.getElementById('leap-body');
    if (d.loading || !d.data.length) {
      body.innerHTML = '<tr><td colspan="14" class="loading">⏳ Scanning options data... (1-2 min)</td></tr>';
      setTimeout(loadLeap, 5000);
      return;
    }
    // Alert bar
    const alerts = d.data.filter(r => r.signal.includes('S3') || r.signal.includes('S2'));
    const alertHtml = alerts.length
      ? '⚡ ALERTS: ' + alerts.map(r =>
          `<span class="${r.signal.includes('S3') ? 's3' : 's2'}">${r.signal.includes('S3') ? '🔴' : '🟡'} ${r.signal}: ${r.ticker}</span>`
        ).join(' &nbsp;|&nbsp; ')
      : '⚡ No S2/S3 alerts at this time';
    document.getElementById('leap-alerts').innerHTML = alertHtml;

    body.innerHTML = d.data.map((r, i) => {
      const cls = rowClass(r.signal, r.score);
      const rowCls = cls || (i % 2 === 0 ? 'even' : 'odd');
      const vs2 = r.vs_s2 != null ? (r.vs_s2 > 0 ? '+' : '') + r.vs_s2 + (r.closest === 's2' ? ' <span class="arrow">◄</span>' : '') : 'N/A';
      const vs3 = r.vs_s3 != null ? (r.vs_s3 > 0 ? '+' : '') + r.vs_s3 + (r.closest === 's3' ? ' <span class="arrow">◄</span>' : '') : 'N/A';
      const fl = r.furthest;
      return `<tr class="${rowCls}" onclick="showLeapDetail(${i})" style="cursor:pointer">
        <td><b>${r.ticker}</b></td>
        <td>$${r.price}</td>
        <td>${r.w52h ? '$'+r.w52h : 'N/A'}</td>
        <td>${r.pct_52w != null ? r.pct_52w : 'N/A'}</td>
        <td>${r.prem_pct != null ? r.prem_pct : 'N/A'}</td>
        <td>${r.leverage != null ? r.leverage : 'N/A'}</td>
        <td>${r.ws2 ? '$'+r.ws2 : 'N/A'}</td>
        <td>${r.ws3 ? '$'+r.ws3 : 'N/A'}</td>
        <td>${vs2}</td><td>${vs3}</td>
        <td>${fl ? fl.exp : 'N/A'}</td>
        <td>${fl ? '$'+fl.premium : 'N/A'}</td>
        <td><b>${r.score} / 15</b></td>
        <td class="${signalClass(r.signal)}">${r.signal}</td>
      </tr>`;
    }).join('');
  });
}

function showLeapDetail(idx) {
  const r = leapData[idx];
  if (!r) return;
  const leap = r.leap;
  const fl = r.furthest;
  const bd = r.breakdown || {};
  let txt = `${'─'.repeat(55)}\\n`;
  txt += `  ${r.ticker}   |   Price: $${r.price}   |   52W High: $${r.w52h}   |   From 52W: ${r.pct_52w}\\n`;
  txt += `${'─'.repeat(55)}\\n`;
  if (leap) {
    txt += `  Best LEAP:   $${leap.strike} Call  |  Expiry: ${leap.exp}  |  DTE: ${leap.dte} days\\n`;
    txt += `  Premium:     $${leap.premium}  |  Prem/Price: ${r.prem_pct}  |  Leverage: ${r.leverage}x\\n`;
    txt += `  IV:          ${leap.iv ? leap.iv + '%' : 'N/A'}\\n`;
  }
  if (fl) {
    txt += `${'─'.repeat(55)}\\n`;
    txt += `  Furthest LEAP: $${fl.strike} Call  |  Expiry: ${fl.exp}  |  DTE: ${fl.dte} days\\n`;
    txt += `  Furthest Premium: $${fl.premium}\\n`;
  }
  txt += `${'─'.repeat(55)}\\n`;
  txt += `  Weekly S2: $${r.ws2}  (${r.vs_s2 > 0 ? '+' : ''}${r.vs_s2} from price)\\n`;
  txt += `  Weekly S3: $${r.ws3}  (${r.vs_s3 > 0 ? '+' : ''}${r.vs_s3} from price)\\n`;
  txt += `${'─'.repeat(55)}\\n`;
  txt += `  SCORE BREAKDOWN:\\n`;
  txt += `    ATH Drawdown:    ${bd['ATH Drawdown'] || 0}/3\\n`;
  txt += `    Prem Efficiency: ${bd['Prem Efficiency'] || 0}/3\\n`;
  txt += `    Leverage:        ${bd['Leverage'] || 0}/3\\n`;
  txt += `    Time Horizon:    ${bd['Time Horizon'] || 0}/3\\n`;
  txt += `    S2/S3 Level:     ${bd['S2/S3 Level'] || 0}/3\\n`;
  txt += `    ${'─'.repeat(20)}\\n`;
  txt += `    TOTAL:           ${r.score}/15   ${r.signal}\\n`;
  if (levState && levState.margin && levState.margin.leap_multiplier < 1) {
    txt += `    SIZING:          ×${levState.margin.leap_multiplier}  (margin-debt ${levState.margin.decel ? 'deceleration' : 'froth'} — see docs/MARGIN_SHORT_BACKTEST)\\n`;
  }
  txt += `${'─'.repeat(55)}`;
  document.getElementById('leap-detail').textContent = txt;
}

function refreshLeap() {
  document.getElementById('leap-body').innerHTML = '<tr><td colspan="14" class="loading">⏳ Refreshing...</td></tr>';
  fetch('/api/refresh/leap', {method: 'POST'}).then(() => setTimeout(loadLeap, 3000));
}

// Initial load
loadTracker();
loadLeap();
loadLeverage();
setTimeout(loadLeverage, 60000);  // retry once after first background pass

// Auto refresh every 30 min
setInterval(() => { loadTracker(); loadLeap(); loadLeverage(); }, 1800000);
</script>
</body>
</html>'''

@app.route('/')
def index():
    return render_template_string(HTML)

import os

threading.Thread(target=background_refresh, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

