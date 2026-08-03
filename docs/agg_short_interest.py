#!/usr/bin/env python3
"""Test 2: Rapach/Ringgenberg/Zhou-style aggregate short-interest predictor,
rebuilt from free FINRA data (2018-2026, bi-monthly).

Their SII: detrended log of equal-weighted aggregate short interest; HIGH
short interest -> LOW future market returns (JFE 2016, R^2 ~13% annual).
Proxy here (no shares-outstanding in FINRA feed): per-stock days-to-cover
(short shares / ADV), EW mean over a fixed 44-name liquid basket, log,
detrended with an EXPANDING (real-time, no look-ahead) linear trend.
Publication lag: settlement + 12 calendar days.  Outcome: SPY forward
returns at 1/3/6/12 months.
"""
import json, subprocess, math, bisect
from datetime import date, timedelta

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
BASKET = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "AMD",
          "NFLX", "ORCL", "CRM", "ADBE", "INTC", "CSCO", "QCOM", "MU",
          "AVGO", "TXN", "JPM", "BAC", "WFC", "GS", "MS", "C",
          "XOM", "CVX", "JNJ", "PG", "KO", "PEP", "WMT", "HD",
          "DIS", "BA", "CAT", "DE", "GE", "F", "GM", "T",
          "VZ", "PFE", "MRK", "UNH"]


def finra_si(sym):
    body = {"limit": 1000, "compareFilters": [
        {"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": sym}]}
    out = subprocess.run(
        ["curl", "-sS", "--max-time", "40", "-X", "POST",
         "https://api.finra.org/data/group/otcmarket/name/consolidatedShortInterest",
         "-H", "Content-Type: application/json", "-H", "Accept: application/json",
         "-d", json.dumps(body)], capture_output=True, text=True, timeout=60)
    rows = json.loads(out.stdout)
    res = {}
    for r in rows:
        try:
            sd = date.fromisoformat(r["settlementDate"])
            si = float(r["currentShortPositionQuantity"])
            adv = float(r["averageDailyVolumeQuantity"])
            if adv > 0: res[sd] = si / adv
        except (KeyError, TypeError, ValueError):
            continue
    return res


def fetch_spy():
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/SPY"
           "?period1=1483228800&period2=1785600000&interval=1d")
    out = subprocess.run(["curl", "-sS", "--max-time", "40", url,
                          "-H", f"User-Agent: {UA}"],
                         capture_output=True, text=True, timeout=60)
    r = json.loads(out.stdout)["chart"]["result"][0]
    adj = r["indicators"]["adjclose"][0]["adjclose"]
    return [(date.fromtimestamp(t), a) for t, a in zip(r["timestamp"], adj) if a]


def main():
    per = {s: finra_si(s) for s in BASKET}
    counts = {s: len(v) for s, v in per.items()}
    ok = [s for s in BASKET if counts[s] >= 150]
    print(f"basket coverage: {len(ok)}/{len(BASKET)} names with >=150 records")

    # settlement dates present for (almost) all names
    all_dates = sorted(set.union(*[set(per[s]) for s in ok]))
    agg = []  # (settlement, ln EW-mean DTC)
    for d in all_dates:
        vals = [per[s][d] for s in ok if d in per[s]]
        if len(vals) >= len(ok) * 0.8:
            agg.append((d, math.log(sum(vals) / len(vals))))
    print(f"aggregate series: {len(agg)} settlements, "
          f"{agg[0][0]} -> {agg[-1][0]}")

    # expanding detrend (real-time): residual of linear fit on data so far
    sii = []  # (known_date, residual)
    for i in range(24, len(agg)):
        xs = list(range(i + 1)); ys = [v for _, v in agg[:i + 1]]
        n = i + 1
        mx, my = sum(xs) / n, sum(ys) / n
        b = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) /
             sum((x - mx) ** 2 for x in xs))
        a = my - b * mx
        resid = ys[-1] - (a + b * i)
        sii.append((agg[i][0] + timedelta(days=12), resid))
    # standardize residuals with expanding stats
    zsii = []
    for i in range(8, len(sii)):
        hist = [r for _, r in sii[:i + 1]]
        mu = sum(hist) / len(hist)
        sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / len(hist)) or 1
        zsii.append((sii[i][0], (sii[i][1] - mu) / sd))
    print(f"real-time SII points: {len(zsii)} | latest: {zsii[-1][1]:+.2f} "
          f"(known {zsii[-1][0]})")

    spy = fetch_spy()
    sdates = [d for d, _ in spy]
    close = dict(spy)

    def fwd(d, k):
        i = bisect.bisect_left(sdates, d)
        if i >= len(sdates) or i + k >= len(sdates): return None
        return close[sdates[i + k]] / close[sdates[i]] - 1

    def corr(pairs):
        n = len(pairs)
        if n < 10: return None, n
        a = [p[0] for p in pairs]; b = [p[1] for p in pairs]
        ma, mb = sum(a) / n, sum(b) / n
        ca = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b)
        return (ca / math.sqrt(va * vb) if va and vb else 0), n

    print("\n== correlation: real-time SII vs SPY forward returns ==")
    print("   (Rapach prediction: NEGATIVE — high short interest, low returns)")
    for k, lbl in [(21, "1m"), (63, "3m"), (126, "6m"), (252, "12m")]:
        pairs = [(z, fwd(d, k)) for d, z in zsii if fwd(d, k) is not None]
        c, n = corr(pairs)
        if c is not None:
            print(f"  fwd {lbl:>3}: r={c:+.3f} (n={n})")

    print("\n== SPY forward returns by SII tercile ==")
    vals = sorted(z for _, z in zsii)
    t1, t2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    for lbl, lo, hi in [("LOW SII (few shorts)", -99, t1),
                        ("MID", t1, t2),
                        ("HIGH SII (heavy shorts)", t2, 99)]:
        for k, klbl in [(63, "3m"), (252, "12m")]:
            v = [fwd(d, k) for d, z in zsii if lo <= z < hi
                 and fwd(d, k) is not None]
            if v:
                m = sum(v) / len(v)
                pos = sum(x > 0 for x in v) / len(v)
                print(f"  {lbl:<24} fwd {klbl:>3}: avg {m*100:+6.1f}% "
                      f"pos {pos*100:3.0f}% (n={len(v)})")
        print()


if __name__ == "__main__":
    main()
