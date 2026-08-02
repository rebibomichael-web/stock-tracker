#!/usr/bin/env python3
"""Test 1: is the L-ETF frenzy flag redundant with the swing regime filter?

Regime filter reconstructed per michael-swing-ta spec:
  SPY > 50MA and VIX < 18 and VIX not rising          -> BULL
  SPY > 50MA otherwise                                 -> NORMAL
  SPY < 50MA                                           -> CAUTION
  SPY < 50MA and VIX rising (>5% over 5d)              -> DANGER
Cross-tab bounce fwd7 by (regime allows vs warns) x (frenzy vs not),
plus flag/regime overlap and threshold robustness (z>=1.5 / 2.0 / 2.5).
"""
import json, subprocess, math, bisect
from datetime import date

SCRATCH = "/tmp/claude-0/-home-user-stock-tracker/8ddb1075-ecc4-570a-985f-e79c1beb460b/scratchpad"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
LETF = ["TQQQ", "SQQQ", "QLD", "QID", "SSO", "SDS", "UPRO", "SPXL",
        "SPXS", "SPXU", "SOXL", "SOXS", "TECL"]
BENCH = ["SPY", "QQQ", "IWM"]


def fetch(sym):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=536457600&period2=1785600000&interval=1d")
    out = subprocess.run(["curl", "-sS", "--max-time", "40", url,
                          "-H", f"User-Agent: {UA}"],
                         capture_output=True, text=True, timeout=60)
    r = json.loads(out.stdout)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    for i, t in enumerate(r["timestamp"]):
        c, v = q["close"][i], q["volume"][i]
        if c is None or adj[i] is None: continue
        rows.append((date.fromtimestamp(t), (c * v) if v else 0.0, adj[i]))
    return rows


def main():
    dv, px = {}, {}
    for s in LETF + BENCH + ["^VIX"]:
        rows = fetch(s)
        dv[s] = {d: v for d, v, _ in rows}
        px[s] = [(d, a) for d, _, a in rows]

    # frenzy z series (same construction as letf_leverage.py)
    dates = [d for d, _ in px["SPY"] if d >= date(2010, 4, 1)]
    ratio = []
    for d in dates:
        num = sum(dv[s].get(d, 0) for s in LETF)
        den = sum(dv[s].get(d, 0) for s in BENCH)
        if den > 0: ratio.append((d, num / den))
    vals = [r for _, r in ratio]
    z = {}
    for i, (d, _) in enumerate(ratio):
        if i < 273: continue
        m21 = sum(vals[i - 20:i + 1]) / 21
        hist = [sum(vals[j - 20:j + 1]) / 21 for j in range(i - 252, i, 5)]
        mu = sum(hist) / len(hist)
        sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / len(hist))
        if sd > 0: z[d] = (m21 - mu) / sd
    zdates = sorted(z)

    # regime per date
    spy = px["SPY"]; vix = {d: c for d, c in px["^VIX"]}
    vdates = sorted(vix)
    regime = {}
    closes = [c for _, c in spy]
    for i, (d, c) in enumerate(spy):
        if i < 50: continue
        ma50 = sum(closes[i - 49:i + 1]) / 50
        j = bisect.bisect_right(vdates, d) - 1
        if j < 5: continue
        v_now, v_5 = vix[vdates[j]], vix[vdates[j - 5]]
        rising = v_now > v_5 * 1.05
        if c < ma50:
            regime[d] = "DANGER" if rising else "CAUTION"
        else:
            regime[d] = "BULL" if (v_now < 18 and not rising) else "NORMAL"

    rdates = sorted(regime)

    def at(dmap, sorted_keys, d):
        i = bisect.bisect_right(sorted_keys, d) - 1
        return dmap[sorted_keys[i]] if i >= 0 else None

    sigs = []
    with open(f"{SCRATCH}/bounce_signals.csv") as f:
        next(f)
        for line in f:
            s, d, f7, f21 = line.strip().split(",")
            d = date.fromisoformat(d)
            if d < date(2011, 6, 1): continue
            zz = at(z, zdates, d); rg = at(regime, rdates, d)
            if zz is None or rg is None: continue
            sigs.append((zz, rg, float(f7), float(f21)))

    def cell(rows):
        if not rows: return "  (none)"
        n = len(rows)
        wr = sum(f7 > 0 for f7, _ in rows) / n
        a7 = sum(f7 for f7, _ in rows) / n
        a21 = sum(f21 for _, f21 in rows) / n
        return f"n={n:<4} WR7={wr*100:5.1f}% avg7={a7*100:+5.2f}% avg21={a21*100:+5.2f}%"

    print(f"signals with both flags: {len(sigs)}")
    for thr in [1.5, 2.0, 2.5]:
        print(f"\n== frenzy threshold z>={thr} ==")
        # overlap: how often is a frenzy day already regime-warned?
        fr_days = [d for d in zdates if z[d] >= thr and at(regime, rdates, d)]
        warned = sum(at(regime, rdates, d) in ("CAUTION", "DANGER") for d in fr_days)
        print(f"  frenzy DAYS regime-warned already: {warned}/{len(fr_days)}"
              f" ({warned/len(fr_days)*100:.0f}%)")
        grid = {}
        for zz, rg, f7, f21 in sigs:
            allow = rg in ("BULL", "NORMAL")
            fr = zz >= thr
            grid.setdefault((allow, fr), []).append((f7, f21))
        print(f"  regime ALLOWS + no frenzy : {cell(grid.get((True, False), []))}")
        print(f"  regime ALLOWS + FRENZY    : {cell(grid.get((True, True), []))}   << incremental cell")
        print(f"  regime WARNS  + no frenzy : {cell(grid.get((False, False), []))}")
        print(f"  regime WARNS  + FRENZY    : {cell(grid.get((False, True), []))}")

    # regime baseline sanity
    print("\n== baseline: signals by regime alone ==")
    byrg = {}
    for zz, rg, f7, f21 in sigs:
        byrg.setdefault(rg, []).append((f7, f21))
    for rg in ["BULL", "NORMAL", "CAUTION", "DANGER"]:
        print(f"  {rg:<8}: {cell(byrg.get(rg, []))}")


if __name__ == "__main__":
    main()
