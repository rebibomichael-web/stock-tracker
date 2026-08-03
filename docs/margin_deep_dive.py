#!/usr/bin/env python3
"""Margin-debt deep dive:
A. Episode anatomy - each YoY>=40% episode: length, YoY peak, SPY peak lag, drawdown
B. Streak-age effect - is month 6 of an extreme reading different from month 1?
C. Deceleration signal - forward returns after YoY drops back below 40%
D. Sector sensitivity - forward 12m by margin bucket per sector ETF
E. Per-stock froth beta - which names get hit hardest after extreme readings
All signals use publication-lagged data (month M known at month-end + 25d).
"""
import json, subprocess, math
from datetime import date, timedelta
import openpyxl

SCRATCH = "/tmp/claude-0/-home-user-stock-tracker/8ddb1075-ecc4-570a-985f-e79c1beb460b/scratchpad"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

SECTORS = {"XLK": "Tech", "SMH": "Semis", "XLY": "Cons Disc", "XLC": "Comm Svcs",
           "XLF": "Financials", "XLI": "Industrials", "IYT": "Transports",
           "XLE": "Energy", "XLB": "Materials", "XLV": "Health", "XLP": "Staples",
           "XLU": "Utilities", "IWM": "Small caps", "QQQ": "Nasdaq 100",
           "SPY": "S&P 500"}
STOCKS = ["AAPL", "MSFT", "AMZN", "NVDA", "AMD", "NFLX", "ORCL", "MU",
          "DE", "TSLA", "GOOGL", "META", "NOW", "CRWD", "SSYS", "NVMI"]


def fetch_prices(sym):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=536457600&period2=1785600000&interval=1d&events=div%2Csplit")
    out = subprocess.run(["curl", "-sS", "--max-time", "40", url,
                          "-H", f"User-Agent: {UA}"],
                         capture_output=True, text=True, timeout=60)
    r = json.loads(out.stdout)["chart"]["result"][0]
    ts, adj = r["timestamp"], r["indicators"]["adjclose"][0]["adjclose"]
    return [(date.fromtimestamp(t), a) for t, a in zip(ts, adj) if a is not None]


def load_margin_yoy():
    """[(data_month_end, yoy, known_date)] oldest first."""
    wb = openpyxl.load_workbook(f"{SCRATCH}/margin-statistics.xlsx")
    rows = []
    for r in wb.active.iter_rows(min_row=2, values_only=True):
        if not r[0] or r[1] is None: continue
        y, m = map(int, str(r[0]).split("-"))
        last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        rows.append((last, float(r[1])))
    rows.sort()
    return [(rows[i][0], rows[i][1] / rows[i - 12][1] - 1,
             rows[i][0] + timedelta(days=25)) for i in range(12, len(rows))]


def px_on_or_after(px, d, idx=None):
    if idx is None:
        idx = {dd: i for i, (dd, _) in enumerate(px)}
    for off in range(10):
        i = idx.get(d + timedelta(days=off))
        if i is not None: return i
    return None


def fwd_ret(px, idx, d, days):
    i = px_on_or_after(px, d, idx)
    if i is None or i + days >= len(px): return None
    return px[i + days][1] / px[i][1] - 1


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals: return None
    n = len(vals)
    mean = sum(vals) / n
    med = sorted(vals)[n // 2]
    pos = sum(v > 0 for v in vals) / n
    return n, mean, med, pos


def main():
    yoy = load_margin_yoy()
    spy = fetch_prices("SPY")
    spy_idx = {d: i for i, (d, _) in enumerate(spy)}

    # ---- A. Episode anatomy (on data months, gaps <=1 month merged) ----
    print("== A. EXTREME EPISODES (YoY >= 40%) ==")
    episodes, cur = [], []
    for i, (dm, y, kn) in enumerate(yoy):
        if y >= 0.40:
            if cur and (dm - cur[-1][0]).days > 70:
                episodes.append(cur); cur = []
            cur.append((dm, y, kn))
    if cur: episodes.append(cur)
    for ep in episodes:
        start, end = ep[0][0], ep[-1][0]
        pk = max(ep, key=lambda x: x[1])
        ongoing = end >= yoy[-1][0]
        # SPY peak: within window from episode start to 18 months after end
        i0 = px_on_or_after(spy, start, spy_idx)
        i1 = px_on_or_after(spy, end + timedelta(days=540), spy_idx) or len(spy) - 1
        seg = spy[i0:i1]
        sp = max(seg, key=lambda x: x[1])
        # drawdown from that peak over following 24 months
        j = spy_idx[sp[0]]
        tail = spy[j:j + 505]
        dd = min(p / sp[1] - 1 for _, p in tail)
        lag = round((sp[0] - pk[0]).days / 30.4)
        print(f"  {start} -> {end}{' (ongoing)' if ongoing else ''}: "
              f"{len(ep)} mo | YoY peak {pk[1]*100:.0f}% @ {pk[0]}")
        print(f"      SPY peak {sp[0]} ({lag:+d} mo vs YoY peak) | "
              f"max drawdown next 24mo: {dd*100:.1f}%"
              f"{'  << current episode, peak/dd not final' if ongoing else ''}")

    # ---- B. Streak age: month N of extreme reading (real-time observable) ----
    print("\n== B. STREAK AGE: forward SPY returns by month-count of extreme reading ==")
    streak = 0
    obs = []  # (known_date, streak_age)
    for dm, y, kn in yoy:
        streak = streak + 1 if y >= 0.40 else 0
        if streak: obs.append((kn, streak))
    bands = [("months 1-3", 1, 3), ("months 4-6", 4, 6),
             ("months 7-12", 7, 12), ("months 13+", 13, 99)]
    print(f"  {'streak age':<12} {'n':>3} | fwd 3m avg/med | fwd 6m avg/med |"
          f" fwd 12m avg/med (%pos)")
    for label, lo, hi in bands:
        r3 = stats([fwd_ret(spy, spy_idx, kn, 63) for kn, s in obs if lo <= s <= hi])
        r6 = stats([fwd_ret(spy, spy_idx, kn, 126) for kn, s in obs if lo <= s <= hi])
        r12 = stats([fwd_ret(spy, spy_idx, kn, 252) for kn, s in obs if lo <= s <= hi])
        if not r12:
            print(f"  {label:<12} (insufficient forward data)"); continue
        print(f"  {label:<12} {r12[0]:>3} | {r3[1]*100:+5.1f}/{r3[2]*100:+5.1f}%   |"
              f" {r6[1]*100:+5.1f}/{r6[2]*100:+5.1f}%   |"
              f" {r12[1]*100:+5.1f}/{r12[2]*100:+5.1f}% ({r12[3]*100:.0f}%)")
    live = [s for kn, s in obs if kn > yoy[-1][0]]
    cur_streak = obs[-1][1] if obs and obs[-1][0] == yoy[-1][2] else 0
    print(f"  NOW: current streak = {cur_streak} months (latest data month "
          f"{yoy[-1][0]}, YoY {yoy[-1][1]*100:.0f}%)")

    # ---- C. Deceleration: first month back below 40% after >=2 extreme months ----
    print("\n== C. DECELERATION SIGNAL: YoY drops back below 40% ==")
    dec = []
    for i in range(2, len(yoy)):
        if yoy[i][1] < 0.40 and yoy[i-1][1] >= 0.40 and yoy[i-2][1] >= 0.40:
            dec.append(yoy[i][2])
    for h, lbl in [(63, "3m"), (126, "6m"), (252, "12m"), (504, "24m")]:
        st = stats([fwd_ret(spy, spy_idx, kn, h) for kn in dec])
        if st:
            print(f"  fwd {lbl:>3}: n={st[0]} avg={st[1]*100:+6.1f}% "
                  f"med={st[2]*100:+6.1f}% pos={st[3]*100:.0f}%")
    print("  trigger dates:", ", ".join(str(d) for d in dec))

    # ---- D. Sector sensitivity ----
    print("\n== D. SECTOR forward 12m: extreme (YoY>=40%) vs all other months ==")
    print(f"  {'sector':<12} {'since':<6} {'extreme n/avg/med':>22} | "
          f"{'other avg':>9} | froth drag")
    rows_out = []
    for sym, name in SECTORS.items():
        try:
            px = fetch_prices(sym)
        except Exception:
            continue
        idx = {d: i for i, (d, _) in enumerate(px)}
        ext, oth = [], []
        for dm, y, kn in yoy:
            r = fwd_ret(px, idx, kn, 252)
            if r is None: continue
            (ext if y >= 0.40 else oth).append(r)
        se, so = stats(ext), stats(oth)
        if not se or se[0] < 5: continue
        drag = se[1] - so[1]
        rows_out.append((drag, f"  {name:<12} {px[0][0].year:<6} "
                         f"n={se[0]:<3} {se[1]*100:+6.1f}% / {se[2]*100:+6.1f}% | "
                         f"{so[1]*100:+8.1f}% | {drag*100:+6.1f}pp"))
    for _, line in sorted(rows_out):
        print(line)

    # ---- E. Per-stock froth beta ----
    print("\n== E. PER-STOCK forward 12m: extreme vs other (froth drag ranking) ==")
    out = []
    for sym in STOCKS:
        try:
            px = fetch_prices(sym)
        except Exception:
            continue
        idx = {d: i for i, (d, _) in enumerate(px)}
        ext, oth = [], []
        for dm, y, kn in yoy:
            r = fwd_ret(px, idx, kn, 252)
            if r is None: continue
            (ext if y >= 0.40 else oth).append(r)
        se, so = stats(ext), stats(oth)
        if not se or se[0] < 5: continue
        out.append((se[1] - so[1], sym, se, so))
    for drag, sym, se, so in sorted(out):
        print(f"  {sym:<6} extreme n={se[0]:<3} avg {se[1]*100:+7.1f}% "
              f"(pos {se[3]*100:3.0f}%) | other {so[1]*100:+7.1f}% | "
              f"drag {drag*100:+7.1f}pp")


if __name__ == "__main__":
    main()
