#!/usr/bin/env python3
"""Leveraged-ETF activity as a leverage gauge.

Part 1 (market, 2010-2026): dollar-volume share of index leveraged ETFs
  (TQQQ SQQQ QLD QID SSO SDS UPRO SPXL SPXS SPXU SOXL SOXS TECL) relative to
  SPY+QQQ+IWM dollar volume.  Detrended z vs trailing 252d (point-in-time).
  a) does it lead margin-debt YoY? (cross-correlation, monthly)
  b) SPY forward returns by z band
  c) bounce-signal outcomes by z band
Part 2 (per stock, 2022-2026): single-stock L-ETF dollar volume / underlying
  dollar volume ("leverage attention"): current ranking + signal overlay.
"""
import json, subprocess, math
from datetime import date, timedelta
import openpyxl

SCRATCH = "/tmp/claude-0/-home-user-stock-tracker/8ddb1075-ecc4-570a-985f-e79c1beb460b/scratchpad"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

MKT_LETF = ["TQQQ", "SQQQ", "QLD", "QID", "SSO", "SDS", "UPRO", "SPXL",
            "SPXS", "SPXU", "SOXL", "SOXS", "TECL"]
BENCH = ["SPY", "QQQ", "IWM"]
SS_LETF = {"TSLA": ["TSLL", "TSLT", "TSLR", "TSLQ", "TSLS"],
           "NVDA": ["NVDL", "NVDU", "NVDX", "NVD", "NVDS"],
           "PLTR": ["PLTU", "PLTD"], "HOOD": ["HOOG", "HOOX"],
           "CRWD": ["CRWL", "CRWU"], "SOFI": ["SOFX"], "MU": ["MUU"],
           "ORCL": ["ORCX"], "SNOW": ["SNOU"],
           "AAPL": ["AAPU", "AAPB"], "META": ["METU", "FBL"],
           "AMD": ["AMDL", "AMDS"], "GOOGL": ["GGLL", "GGLS"],
           "AMZN": ["AMZU", "AMZZ"], "MSFT": ["MSFU", "MSFL"],
           "NFLX": ["NFXL"]}


def fetch(sym):
    """[(date, raw_close*volume, adjclose)] — dollar volume + adj price."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=536457600&period2=1785600000&interval=1d")
    out = subprocess.run(["curl", "-sS", "--max-time", "40", url,
                          "-H", f"User-Agent: {UA}"],
                         capture_output=True, text=True, timeout=60)
    r = json.loads(out.stdout)["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    for i, t in enumerate(ts):
        c, v = q["close"][i], q["volume"][i]
        if c is None or v is None or adj[i] is None: continue
        rows.append((date.fromtimestamp(t), c * v, adj[i]))
    return rows


def load_margin_yoy():
    wb = openpyxl.load_workbook(f"{SCRATCH}/margin-statistics.xlsx")
    rows = []
    for r in wb.active.iter_rows(min_row=2, values_only=True):
        if not r[0] or r[1] is None: continue
        y, m = map(int, str(r[0]).split("-"))
        rows.append(((y, m), float(r[1])))
    rows.sort()
    return {rows[i][0]: rows[i][1] / rows[i - 12][1] - 1
            for i in range(12, len(rows))}


def corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    ca = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b)
    return ca / math.sqrt(va * vb) if va and vb else 0


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals: return None
    n = len(vals); mean = sum(vals) / n
    med = sorted(vals)[n // 2]; pos = sum(v > 0 for v in vals) / n
    return n, mean, med, pos


def main():
    dv = {}   # sym -> {date: $vol}
    px = {}   # sym -> [(date, adjclose)]
    for s in MKT_LETF + BENCH:
        rows = fetch(s)
        dv[s] = {d: v for d, v, _ in rows}
        px[s] = [(d, a) for d, _, a in rows]

    # ---- daily L-share ratio (index complex) ----
    spy_dates = [d for d, _ in px["SPY"] if d >= date(2010, 4, 1)]
    ratio = []  # (date, letf$/bench$)
    for d in spy_dates:
        num = sum(dv[s].get(d, 0) for s in MKT_LETF)
        den = sum(dv[s].get(d, 0) for s in BENCH)
        if den > 0: ratio.append((d, num / den))
    # 21d smooth + trailing 252d z (point-in-time)
    sm, z = {}, {}
    vals = [r for _, r in ratio]
    for i, (d, _) in enumerate(ratio):
        if i < 21: continue
        m21 = sum(vals[i - 20:i + 1]) / 21
        sm[d] = m21
        if i >= 273:
            hist = [sum(vals[j - 20:j + 1]) / 21 for j in range(i - 252, i, 5)]
            mu = sum(hist) / len(hist)
            sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / len(hist))
            if sd > 0: z[d] = (m21 - mu) / sd
    zs = sorted(z.items())
    print(f"L-ETF $vol share of SPY+QQQ+IWM: now {sm[zs[-1][0]]*100:.0f}%"
          f" | z={zs[-1][1]:+.1f} (as of {zs[-1][0]})")

    # ---- (a) does L-share lead margin YoY? monthly cross-correlation ----
    margin = load_margin_yoy()
    monthly = {}
    for d, r in ratio:
        monthly.setdefault((d.year, d.month), []).append(r)
    mkeys = sorted(monthly)
    mratio = {k: sum(v) / len(v) for k, v in monthly.items()}
    ryoy = {}
    for i, k in enumerate(mkeys):
        if i >= 12:
            prev = mkeys[i - 12]
            if mratio[prev] > 0: ryoy[k] = mratio[k] / mratio[prev] - 1
    print("\n== (a) cross-correlation: L-ETF share YoY vs margin-debt YoY ==")
    common = [k for k in sorted(ryoy) if k in margin]
    for lag in range(-6, 7):
        pairs = []
        for k in common:
            y, m = k
            m2 = m + lag; y2 = y + (m2 - 1) // 12; m2 = (m2 - 1) % 12 + 1
            if (y2, m2) in margin:
                pairs.append((ryoy[k], margin[(y2, m2)]))
        if len(pairs) > 24:
            c = corr([p[0] for p in pairs], [p[1] for p in pairs])
            bar = "#" * int(abs(c) * 40)
            print(f"  ETF leads margin by {lag:+d} mo: r={c:+.2f} {bar}")

    # ---- (b) SPY forward returns by z band (month-end sampling) ----
    print("\n== (b) SPY forward returns by L-ETF intensity z (month-end samples) ==")
    spy_px = px["SPY"]
    spy_idx = {d: i for i, (d, _) in enumerate(spy_px)}
    samples = []
    seen = set()
    for d, zz in zs:
        k = (d.year, d.month)
        if k in seen: continue
        seen.add(k)  # first obs each month
        i = spy_idx.get(d)
        if i is None: continue
        f3 = spy_px[i + 63][1] / spy_px[i][1] - 1 if i + 63 < len(spy_px) else None
        f12 = spy_px[i + 252][1] / spy_px[i][1] - 1 if i + 252 < len(spy_px) else None
        samples.append((zz, f3, f12))
    for lbl, lo, hi in [("z < 0 (quiet)", -9, 0), ("0..1", 0, 1),
                        ("1..2 (hot)", 1, 2), ("z >= 2 (frenzy)", 2, 9)]:
        s3 = stats([f3 for zz, f3, _ in samples if lo <= zz < hi])
        s12 = stats([f12 for zz, _, f12 in samples if lo <= zz < hi])
        if s3:
            f12s = (f"avg12m={s12[1]*100:+6.1f}% med={s12[2]*100:+6.1f}%"
                    f" pos={s12[3]*100:3.0f}%") if s12 else "12m n/a"
            print(f"  {lbl:<16} n={s3[0]:<4} avg3m={s3[1]*100:+5.1f}% | {f12s}")

    # ---- (c) bounce outcomes by z band ----
    print("\n== (c) bounce-signal fwd7 by L-ETF intensity z at signal (2011+) ==")
    sigs = []
    with open(f"{SCRATCH}/bounce_signals.csv") as f:
        next(f)
        for line in f:
            s, d, f7, f21 = line.strip().split(",")
            sigs.append((s, date.fromisoformat(d), float(f7), float(f21)))
    zdates = sorted(z)
    def z_at(d):
        import bisect
        i = bisect.bisect_right(zdates, d) - 1
        return z[zdates[i]] if i >= 0 else None
    bands = {}
    for s, d, f7, f21 in sigs:
        if d < date(2011, 6, 1): continue
        zz = z_at(d)
        if zz is None: continue
        b = ("z<0" if zz < 0 else "0..1" if zz < 1 else "1..2" if zz < 2 else ">=2")
        bands.setdefault(b, ([], []))[0].append(f7); bands[b][1].append(f21)
    for b in ["z<0", "0..1", "1..2", ">=2"]:
        v = bands.get(b, ([], []))
        s7, s21 = stats(v[0]), stats(v[1])
        if s7:
            print(f"  {b:<6} n={s7[0]:<4} WR7={s7[3]*100:5.1f}% avg7={s7[1]*100:+5.2f}%"
                  f" | avg21={s21[1]*100:+5.2f}%")

    # ---- Part 2: per-stock leverage attention ----
    print("\n== PER-STOCK L-ETF attention (L-ETF $vol / underlying $vol) ==")
    print(f"  {'name':<6} {'ETFs':<5} {'20d avg':>8} {'1y avg':>8} {'z(1y)':>6}   trend")
    rank = []
    und_dv = {}
    for und, etfs in SS_LETF.items():
        try:
            u = fetch(und)
        except Exception:
            continue
        und_dv[und] = {d: v for d, v, _ in u}
        edv = {}
        cnt = 0
        for e in etfs:
            try:
                for d, v, _ in fetch(e):
                    edv[d] = edv.get(d, 0) + v
                cnt += 1
            except Exception:
                continue
        ds = sorted(d for d in edv if d in und_dv[und])
        if len(ds) < 60: continue
        att = [edv[d] / und_dv[und][d] for d in ds if und_dv[und][d] > 0]
        a20 = sum(att[-21:]) / 21
        a252 = sum(att[-252:]) / min(252, len(att))
        hist = att[-252:]
        mu = sum(hist) / len(hist)
        sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / len(hist)) or 1
        zz = (a20 - mu) / sd
        rank.append((a20, und, cnt, a252, zz))
    for a20, und, cnt, a252, zz in sorted(rank, reverse=True):
        trend = "rising" if zz > 0.5 else "falling" if zz < -0.5 else "flat"
        print(f"  {und:<6} {cnt:<5} {a20*100:7.1f}% {a252*100:7.1f}% {zz:+6.1f}   {trend}")

    # signal overlay for names with 2022-era ETFs
    print("\n== bounce fwd7 by per-stock attention z at signal (TSLA/NVDA/AAPL/GOOGL/AMD/META, 2023+) ==")
    for und in ["TSLA", "NVDA", "AAPL", "GOOGL", "AMD", "META"]:
        etfs = SS_LETF[und]
        edv = {}
        for e in etfs:
            try:
                for d, v, _ in fetch(e):
                    edv[d] = edv.get(d, 0) + v
            except Exception:
                continue
        ds = sorted(d for d in edv if d in und_dv.get(und, {}))
        att = {d: edv[d] / und_dv[und][d] for d in ds if und_dv[und][d] > 0}
        ads = sorted(att)
        hi_v, lo_v = [], []
        for s, d, f7, f21 in sigs:
            if s != und or d.year < 2023: continue
            import bisect
            i = bisect.bisect_right(ads, d) - 1
            if i < 130: continue
            cur = sum(att[ads[j]] for j in range(i - 20, i + 1)) / 21
            base = sum(att[ads[j]] for j in range(i - 126, i + 1)) / 127
            (hi_v if cur > base else lo_v).append(f7)
        sh, sl = stats(hi_v), stats(lo_v)
        if sh and sl:
            print(f"  {und:<6} attention HIGH: n={sh[0]:<3} avg7={sh[1]*100:+5.2f}% | "
                  f"LOW: n={sl[0]:<3} avg7={sl[1]*100:+5.2f}%")


if __name__ == "__main__":
    main()
