#!/usr/bin/env python3
"""POC backtest: do margin-debt YoY (market level) and short interest
(per stock) improve the swing program's oversold-bounce signature?

Signal proxy (per SWING_AUDIT dominant combo): RSI(3) <= 20 AND close <= lower
Bollinger(20, 2s).  Entry at NEXT day's open (audit flagged same-close entry as
optimistic).  Outcomes: +7 and +21 trading-day returns from entry open.
Signals deduped: skip if same ticker fired within prior 5 trading days.

Test A: bucket bounce outcomes by FINRA margin-debt YoY growth (publication
        lag applied: month M known at month-end + 25 calendar days).
Test B: SPY forward 12m return by margin YoY bucket (LEAP horizon).
Test C: bucket bounce outcomes by the stock's days-to-cover from FINRA
        consolidated short interest (settlement + 12 calendar days lag).
"""
import json, subprocess, sys, math
from datetime import date, timedelta
import openpyxl

SCRATCH = "/tmp/claude-0/-home-user-stock-tracker/8ddb1075-ecc4-570a-985f-e79c1beb460b/scratchpad"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# Long-history liquid set (Test A) + current 16-ticker watchlist (Test C)
LONG_HIST = ["AAPL", "MSFT", "AMZN", "NVDA", "AMD", "NFLX", "ORCL", "MU",
             "DE", "TSLA", "GOOGL", "META", "NOW", "CRWD"]
WATCHLIST = ["CRWD", "ORCL", "SNOW", "SSYS", "LMND", "PLTR", "BMNR", "TSLA",
             "NVDA", "GRNY", "DE", "MU", "NVMI", "SOFI", "HOOD", "NOW"]
ALL_TICKERS = sorted(set(LONG_HIST) | set(WATCHLIST))


def curl_json(url, post_body=None):
    cmd = ["curl", "-sS", "--max-time", "40", url, "-H", f"User-Agent: {UA}",
           "-H", "Accept: application/json"]
    if post_body is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "-d", json.dumps(post_body)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout)


def fetch_prices(sym):
    """Yahoo v8 chart, full history, split/div-adjusted OHLC."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=536457600&period2=1785600000&interval=1d&events=div%2Csplit")
    r = curl_json(url)["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    for i, t in enumerate(ts):
        c, o = q["close"][i], q["open"][i]
        if c is None or o is None or adj[i] is None:
            continue
        f = adj[i] / c  # adjustment factor
        rows.append((date.fromtimestamp(t), o * f, adj[i]))
    return rows  # [(date, adj_open, adj_close)]


def rsi(closes, period=3):
    """Wilder RSI; returns list aligned to closes (None until warm)."""
    out = [None] * len(closes)
    gains = losses = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if i <= period:
            gains += g; losses += l
            if i == period:
                ag, al = gains / period, losses / period
                out[i] = 100 - 100 / (1 + (ag / al if al else 1e9))
        else:
            ag = (ag * (period - 1) + g) / period
            al = (al * (period - 1) + l) / period
            out[i] = 100 - 100 / (1 + (ag / al if al else 1e9))
    return out


def bounce_signals(rows):
    """Indices i where RSI3<=20 and close<=lower BB(20,2); deduped 5d."""
    closes = [c for _, _, c in rows]
    r3 = rsi(closes, 3)
    sigs, last = [], -99
    for i in range(20, len(rows) - 22):  # need BB warmup + 21d forward
        if r3[i] is None or i - last <= 5:
            continue
        win = closes[i - 19:i + 1]
        m = sum(win) / 20
        sd = math.sqrt(sum((x - m) ** 2 for x in win) / 20)
        if r3[i] <= 20 and closes[i] <= m - 2 * sd:
            sigs.append(i); last = i
    return sigs


def load_margin():
    """[(month_last_day, debit)] oldest first, plus lookup by 'known date'."""
    wb = openpyxl.load_workbook(f"{SCRATCH}/margin-statistics.xlsx")
    rows = []
    for r in wb.active.iter_rows(min_row=2, values_only=True):
        if not r[0] or r[1] is None:
            continue
        y, m = map(int, str(r[0]).split("-"))
        last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        rows.append((last, float(r[1])))
    rows.sort()
    yoy = {}  # known_date -> yoy
    for i in range(12, len(rows)):
        known = rows[i][0] + timedelta(days=25)
        yoy[known] = rows[i][1] / rows[i - 12][1] - 1
    return sorted(yoy.items())


def latest_before(sorted_pairs, d):
    lo, hi, best = 0, len(sorted_pairs) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if sorted_pairs[mid][0] <= d:
            best = sorted_pairs[mid][1]; lo = mid + 1
        else:
            hi = mid - 1
    return best


def margin_bucket(y):
    if y is None: return None
    if y >= 0.40: return ">=40% (extreme)"
    if y >= 0.20: return "20-40% (hot)"
    if y >= 0.00: return "0-20% (normal)"
    return "<0% (delever)"


def fetch_short_interest(sym):
    body = {"limit": 1000, "compareFilters": [
        {"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": sym}]}
    try:
        rows = curl_json("https://api.finra.org/data/group/otcmarket/name/"
                         "consolidatedShortInterest", body)
        if not isinstance(rows, list): return []
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            sd = date.fromisoformat(r["settlementDate"])
            out.append((sd + timedelta(days=12), float(r["daysToCoverQuantity"])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort()
    return out


def stats(vals):
    n = len(vals)
    if not n: return None
    mean = sum(vals) / n
    wr = sum(v > 0 for v in vals) / n
    med = sorted(vals)[n // 2]
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / max(n - 1, 1))
    se = sd / math.sqrt(n) if n else 0
    return n, wr, mean, med, se


def prow(label, s7, s21):
    if not s7:
        print(f"  {label:<18} (no signals)"); return
    n, wr, mean, med, se = s7
    m21 = f"{s21[2]*100:+6.2f}%" if s21 else "  n/a"
    print(f"  {label:<18} n={n:<5} WR7={wr*100:5.1f}%  avg7={mean*100:+6.2f}%"
          f" (se {se*100:.2f})  med7={med*100:+6.2f}%  avg21={m21}")


def main():
    margin = load_margin()
    print("margin series months:", len(margin),
          "| latest YoY:", f"{margin[-1][1]*100:.1f}%")

    px, si = {}, {}
    for s in ALL_TICKERS:
        try:
            px[s] = fetch_prices(s)
        except Exception as e:
            print(f"  price fetch failed {s}: {e}"); continue
    for s in WATCHLIST:
        si[s] = fetch_short_interest(s)
    print("prices:", {s: len(v) for s, v in sorted(px.items())})
    print("short-interest rows:", {s: len(v) for s, v in sorted(si.items())})

    # ---- collect signals with outcomes ----
    def outcomes(rows, i):
        e = rows[i + 1][1]  # next open
        return (rows[i + 8][2] / e - 1, rows[i + 22][2] / e - 1)

    all_sigs = []  # (sym, date, fwd7, fwd21)
    for s, rows in px.items():
        for i in bounce_signals(rows):
            f7, f21 = outcomes(rows, i)
            all_sigs.append((s, rows[i][0], f7, f21))

    # ---- Test A: margin YoY buckets (long-history set, 1998+) ----
    print("\n== TEST A: bounce outcomes by margin-debt YoY (publication-lagged),"
          " long-history set 1998-2026 ==")
    buckets = {}
    for s, d, f7, f21 in all_sigs:
        if s not in LONG_HIST or d.year < 1998: continue
        b = margin_bucket(latest_before(margin, d))
        if b: buckets.setdefault(b, ([], []))[0].append(f7); buckets[b][1].append(f21)
    for b in [">=40% (extreme)", "20-40% (hot)", "0-20% (normal)", "<0% (delever)"]:
        v = buckets.get(b, ([], []))
        prow(b, stats(v[0]), stats(v[1]))

    # ---- Test B: SPY forward 12m by margin bucket (LEAP horizon) ----
    print("\n== TEST B: SPY forward 12-month return by margin-debt YoY bucket ==")
    spy = fetch_prices("SPY")
    by_date = {d: i for i, (d, _, _) in enumerate(spy)}
    spy_b = {}
    for known, y in margin:
        idx = None
        for off in range(7):
            idx = by_date.get(known + timedelta(days=off))
            if idx is not None: break
        if idx is None or idx + 252 >= len(spy): continue
        fwd = spy[idx + 252][2] / spy[idx][2] - 1
        spy_b.setdefault(margin_bucket(y), []).append(fwd)
    for b in [">=40% (extreme)", "20-40% (hot)", "0-20% (normal)", "<0% (delever)"]:
        v = spy_b.get(b, [])
        st = stats(v)
        if st:
            n, wr, mean, med, se = st
            print(f"  {b:<18} n={n:<4} avg12m={mean*100:+6.2f}%  med12m={med*100:+6.2f}%"
                  f"  %positive={wr*100:5.1f}%")

    # ---- Test C: days-to-cover buckets (watchlist, SI era) ----
    print("\n== TEST C: bounce outcomes by days-to-cover at signal"
          " (16-ticker watchlist, FINRA SI era) ==")
    dtc_b = {}
    for s, d, f7, f21 in all_sigs:
        if s not in WATCHLIST or not si.get(s): continue
        dtc = latest_before(si[s], d)
        if dtc is None: continue
        b = ("<1.5d (light)" if dtc < 1.5 else "1.5-3d" if dtc < 3
             else "3-6d (heavy)" if dtc < 6 else ">=6d (crowded)")
        dtc_b.setdefault(b, ([], []))[0].append(f7); dtc_b[b][1].append(f21)
    for b in ["<1.5d (light)", "1.5-3d", "3-6d (heavy)", ">=6d (crowded)"]:
        v = dtc_b.get(b, ([], []))
        prow(b, stats(v[0]), stats(v[1]))

    # save raw signals for reuse
    with open(f"{SCRATCH}/bounce_signals.csv", "w") as f:
        f.write("symbol,date,fwd7,fwd21\n")
        for s, d, f7, f21 in sorted(all_sigs, key=lambda x: x[1]):
            f.write(f"{s},{d},{f7:.5f},{f21:.5f}\n")
    print(f"\nsaved {len(all_sigs)} signals -> bounce_signals.csv")


if __name__ == "__main__":
    main()
