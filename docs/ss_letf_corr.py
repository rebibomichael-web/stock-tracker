#!/usr/bin/env python3
"""Per-name leveraged-ETF correlation tests for TSLA / NVDA / PLTR.

Daily frequency (hundreds of obs per name, vs the tiny bounce-signal overlaps):
  1. IC: attention z vs underlying forward 1d/5d/21d returns
  2. attention z vs forward 5d realized volatility
  3. overnight-reversal amplification: big-move days (|ret|>=3%), does the
     next overnight gap reverse the move more when L-ETF attention is high?
  4. after attention spikes (z>=2): forward returns vs baseline
  5. per-name bull/bear $vol ratio (crowding): high-crowding forward returns
"""
import json, subprocess, math
from datetime import date

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
NAMES = {
    "TSLA": (["TSLL", "TSLT", "TSLR"], ["TSLQ", "TSLS"]),
    "NVDA": (["NVDL", "NVDU", "NVDX"], ["NVD", "NVDS"]),
    "PLTR": (["PLTU"], ["PLTD"]),
}


def fetch(sym):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=1577836800&period2=1785600000&interval=1d")
    out = subprocess.run(["curl", "-sS", "--max-time", "40", url,
                          "-H", f"User-Agent: {UA}"],
                         capture_output=True, text=True, timeout=60)
    r = json.loads(out.stdout)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    for i, t in enumerate(r["timestamp"]):
        c, v, o = q["close"][i], q["volume"][i], q["open"][i]
        if None in (c, v, o) or adj[i] is None: continue
        rows.append((date.fromtimestamp(t), c * v, adj[i], o * (adj[i] / c)))
    return rows  # (date, $vol, adjclose, adjopen)


def corr(a, b):
    n = len(a)
    if n < 20: return None, n
    ma, mb = sum(a) / n, sum(b) / n
    ca = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b)
    return (ca / math.sqrt(va * vb) if va and vb else 0), n


def zroll(vals, i, win=126, sm=5):
    if i < win + sm: return None
    cur = sum(vals[i - sm + 1:i + 1]) / sm
    hist = [sum(vals[j - sm + 1:j + 1]) / sm for j in range(i - win, i, 3)]
    mu = sum(hist) / len(hist)
    sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / len(hist))
    return (cur - mu) / sd if sd > 0 else None


for und, (bulls, bears) in NAMES.items():
    u = fetch(und)
    udv = {d: v for d, v, _, _ in u}
    bd, rd = {}, {}
    for e in bulls:
        try:
            for d, v, _, _ in fetch(e): bd[d] = bd.get(d, 0) + v
        except Exception: pass
    for e in bears:
        try:
            for d, v, _, _ in fetch(e): rd[d] = rd.get(d, 0) + v
        except Exception: pass
    # build aligned daily series where ETFs exist
    ds = [d for d, v, _, _ in u if d in bd and udv[d] > 0]
    att = [ (bd[d] + rd.get(d, 0)) / udv[d] for d in ds]
    bb = [math.log((bd[d] + 1) / (rd.get(d, 0) + 1)) for d in ds]
    close = {d: c for d, _, c, _ in u}
    aopen = {d: o for d, _, _, o in u}
    all_ds = [d for d, _, _, _ in u]
    di = {d: i for i, d in enumerate(all_ds)}

    def fwd(d, k):
        i = di[d]
        if i + k >= len(all_ds): return None
        return close[all_ds[i + k]] / close[d] - 1

    def fvol(d, k=5):
        i = di[d]
        if i + k >= len(all_ds): return None
        rets = [close[all_ds[j + 1]] / close[all_ds[j]] - 1 for j in range(i, i + k)]
        m = sum(rets) / k
        return math.sqrt(sum((r - m) ** 2 for r in rets) / k) * math.sqrt(252)

    az = {}
    bz = {}
    for i, d in enumerate(ds):
        z1 = zroll(att, i); z2 = zroll(bb, i)
        if z1 is not None: az[d] = z1
        if z2 is not None: bz[d] = z2

    print(f"\n===== {und} (obs with z: {len(az)}) =====")
    # 1. IC vs forward returns
    for k, lbl in [(1, "1d"), (5, "5d"), (21, "21d")]:
        pairs = [(az[d], fwd(d, k)) for d in az if fwd(d, k) is not None]
        c, n = corr([p[0] for p in pairs], [p[1] for p in pairs])
        print(f"  attention z vs fwd {lbl:>3}: r={c:+.3f} (n={n})")
    # 2. vs forward realized vol
    pairs = [(az[d], fvol(d)) for d in az if fvol(d) is not None]
    c, n = corr([p[0] for p in pairs], [p[1] for p in pairs])
    print(f"  attention z vs fwd 5d realized vol: r={c:+.3f} (n={n})")
    # 3. overnight reversal on big-move days
    for regime, lo, hi in [("low att (z<0)", -99, 0), ("high att (z>=1)", 1, 99)]:
        gaps = []
        for d in az:
            if not (lo <= az[d] < hi): continue
            i = di[d]
            if i + 1 >= len(all_ds) or i < 1: continue
            ret = close[d] / close[all_ds[i - 1]] - 1
            if abs(ret) < 0.03: continue
            gap = aopen[all_ds[i + 1]] / close[d] - 1
            gaps.append(gap * (1 if ret > 0 else -1))  # + = continuation
        if len(gaps) >= 10:
            m = sum(gaps) / len(gaps)
            pos = sum(g > 0 for g in gaps) / len(gaps)
            print(f"  big-move overnight follow-through, {regime:<16}: "
                  f"avg {m*100:+.2f}% cont-rate {pos*100:.0f}% (n={len(gaps)})")
    # 4. attention spikes
    base5 = [fwd(d, 5) for d in az if fwd(d, 5) is not None]
    spk5 = [fwd(d, 5) for d in az if az[d] >= 2 and fwd(d, 5) is not None]
    spk21 = [fwd(d, 21) for d in az if az[d] >= 2 and fwd(d, 21) is not None]
    if spk5:
        print(f"  after attention spike z>=2: avg fwd5 {sum(spk5)/len(spk5)*100:+.2f}%"
              f" (n={len(spk5)}) vs all-days {sum(base5)/len(base5)*100:+.2f}%"
              f" | fwd21 {sum(spk21)/len(spk21)*100:+.2f}%")
    # 5. bull/bear crowding
    for lbl, lo, hi in [("bear-tilt z<-1", -99, -1), ("neutral", -1, 1),
                        ("bull-crowded z>=1", 1, 99)]:
        v5 = [fwd(d, 5) for d in bz if lo <= bz[d] < hi and fwd(d, 5) is not None]
        v21 = [fwd(d, 21) for d in bz if lo <= bz[d] < hi and fwd(d, 21) is not None]
        if len(v5) >= 15:
            print(f"  bull/bear crowding {lbl:<18}: fwd5 {sum(v5)/len(v5)*100:+.2f}%"
                  f" fwd21 {sum(v21)/len(v21)*100:+.2f}% (n={len(v5)})")
