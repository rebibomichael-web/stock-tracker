#!/usr/bin/env python3
"""Leverage Monitor — margin-debt dimmer, L-ETF frenzy flag, nightly logger.

Backtest basis: docs/MARGIN_SHORT_BACKTEST_2026-08-02.md and
docs/STEELMAN_2026-08-02.md (validated 2026-08-02).

Three jobs:

1. MARGIN DIMMER (LEAP sizing).  FINRA monthly margin debt, YoY growth.
   Historically, sustained YoY >= 40% preceded negative SPY 12-month returns
   (2000/2007/2021).  Policy implemented here:
     yoy >= 40%                                  -> leap_multiplier 0.75
     deceleration (fresh drop below 40% after 2+ extreme months)
       AND regime CAUTION/DANGER                 -> leap_multiplier 0.50
     otherwise                                   -> 1.00
   Never gates swing trades (extreme-margin months were the BEST bounce
   months in backtest).

2. FRENZY FLAG (swing veto).  Dollar-volume share of 13 index leveraged ETFs
   vs SPY+QQQ+IWM, 21d-smoothed, z-scored vs trailing 252d.  Validated:
   bounce WR7 33.7% when z >= 2 while regime is BULL/NORMAL (vs 56.5%
   baseline); do NOT veto when regime is already CAUTION/DANGER (frenzy
   there = capitulation, bounces WORK: +2.86% avg7).
   swing_adjustment() returns score_multiplier 0.75 only in the veto cell.

3. NIGHTLY LOG (--nightly).  Per watchlist name: L-ETF attention ratio,
   heavy signed-volume flag, FINRA days-to-cover — appended as one JSON line
   for the indicator profiler to correlate with outcomes later.

Data: FINRA xlsx + FINRA API (no auth) + Yahoo v8 chart via plain requests.
CLI:  --check | --nightly [--out PATH] | --selftest
"""
import io
import json
import math
import os
import sys
from datetime import date, datetime, timedelta

MARGIN_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
FINRA_SI = "https://api.finra.org/data/group/otcmarket/name/consolidatedShortInterest"
YCHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

MKT_LETF = ["TQQQ", "SQQQ", "QLD", "QID", "SSO", "SDS", "UPRO", "SPXL",
            "SPXS", "SPXU", "SOXL", "SOXS", "TECL"]
BENCH = ["SPY", "QQQ", "IWM"]
# single-stock leveraged ETFs per watchlist name (verified live 2026-08-02)
SS_LETF = {"TSLA": ["TSLL", "TSLT", "TSLR", "TSLQ", "TSLS"],
           "NVDA": ["NVDL", "NVDU", "NVDX", "NVD", "NVDS"],
           "PLTR": ["PLTU", "PLTD"], "HOOD": ["HOOG", "HOOX"],
           "CRWD": ["CRWL", "CRWU"], "SOFI": ["SOFX"], "MU": ["MUU"],
           "ORCL": ["ORCX"], "SNOW": ["SNOU"]}

DEFAULT_LOG = os.path.expanduser("~/.michael_leverage_log.jsonl")

MARGIN_EXTREME = 0.40   # YoY threshold, per backtest
FRENZY_Z = 2.0          # validated at 1.5/2.0/2.5; 2.0 is the tested spec
VETO_MULTIPLIER = 0.75
DECEL_MULTIPLIER = 0.50


# ─── HTTP ─────────────────────────────────────────────────────────────

def _http_get(url, post_json=None, timeout=30):
    import requests
    verify = True
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"
    if os.path.exists(ca):
        verify = ca
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if post_json is not None:
        r = requests.post(url, json=post_json, headers=headers,
                          timeout=timeout, verify=verify)
    else:
        r = requests.get(url, headers=headers, timeout=timeout, verify=verify)
    r.raise_for_status()
    return r


def fetch_daily(sym, days=750):
    """Daily bars from Yahoo v8: (date, adj_close, dollar_volume)."""
    p2 = int(datetime.now().timestamp()) + 86400
    p1 = p2 - days * 86400
    url = YCHART.format(sym=sym) + f"?period1={p1}&period2={p2}&interval=1d"
    res = _http_get(url).json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    out = []
    for i, t in enumerate(res["timestamp"]):
        c, v = q["close"][i], q["volume"][i]
        if c is None or adj[i] is None:
            continue
        out.append((date.fromtimestamp(t), adj[i], c * (v or 0)))
    return out


# ─── PURE COMPUTATIONS (covered by --selftest) ────────────────────────

def margin_metrics(monthly):
    """monthly: [(\"YYYY-MM\", debit)] any order.  Returns yoy series stats.

    streak  = consecutive latest months with yoy >= MARGIN_EXTREME
    decel   = latest month dropped below threshold after >= 2 extreme months
    """
    rows = sorted(monthly)
    yoy = []
    vals = {m: v for m, v in rows}
    for m, v in rows[12:]:
        y, mo = int(m[:4]), int(m[5:7])
        prev = f"{y-1:04d}-{mo:02d}"
        if prev in vals and vals[prev] > 0:
            yoy.append((m, v / vals[prev] - 1))
    if not yoy:
        return None
    streak = 0
    for _, g in reversed(yoy):
        if g >= MARGIN_EXTREME:
            streak += 1
        else:
            break
    decel = (len(yoy) >= 3 and yoy[-1][1] < MARGIN_EXTREME
             and yoy[-2][1] >= MARGIN_EXTREME and yoy[-3][1] >= MARGIN_EXTREME)
    return {"month": yoy[-1][0], "yoy": round(yoy[-1][1], 4),
            "streak": streak, "decel": decel}


def leap_multiplier(yoy, decel, regime):
    if decel and regime in ("CAUTION", "DANGER"):
        return DECEL_MULTIPLIER
    if yoy is not None and yoy >= MARGIN_EXTREME:
        return VETO_MULTIPLIER
    return 1.0


def classify_regime(spy_close, spy_ma50, vix_now, vix_5d_ago):
    """Per michael-swing-ta spec."""
    rising = vix_now > vix_5d_ago * 1.05
    if spy_close < spy_ma50:
        return "DANGER" if rising else "CAUTION"
    return "BULL" if (vix_now < 18 and not rising) else "NORMAL"


def intensity_z(ratios):
    """z of 21d-smoothed L-ETF share vs trailing 252d (5d-sampled) history.
    ratios: chronological list of daily letf$/bench$ values.  None if short.
    Identical construction to the validated backtest."""
    n = len(ratios)
    if n < 274:
        return None
    m21 = sum(ratios[-21:]) / 21
    hist = [sum(ratios[j - 20:j + 1]) / 21 for j in range(n - 253, n - 1, 5)]
    mu = sum(hist) / len(hist)
    sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / len(hist))
    if sd <= abs(mu) * 1e-9:   # degenerate/flat history -> no meaningful z
        return None
    return (m21 - mu) / sd


def frenzy_veto(z, regime):
    """Veto ONLY when frenzy fires in a tape the regime filter still allows."""
    return z is not None and z >= FRENZY_Z and regime in ("BULL", "NORMAL")


# ─── LIVE STATES ──────────────────────────────────────────────────────

def margin_state(regime=None):
    import openpyxl
    raw = _http_get(MARGIN_XLSX).content
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
    monthly = []
    for r in wb.active.iter_rows(min_row=2, values_only=True):
        if r and r[0] and r[1] is not None:
            monthly.append((str(r[0]), float(r[1])))
    m = margin_metrics(monthly)
    if m is None:
        return None
    m["leap_multiplier"] = leap_multiplier(m["yoy"], m["decel"], regime)
    return m


def regime_state():
    spy = fetch_daily("SPY", 400)
    vix = fetch_daily("^VIX", 60)
    closes = [c for _, c, _ in spy]
    ma50 = sum(closes[-50:]) / 50
    return classify_regime(closes[-1], ma50, vix[-1][1], vix[-6][1])


def frenzy_state(regime):
    dv = {}
    for s in MKT_LETF + BENCH:
        dv[s] = {d: v for d, _, v in fetch_daily(s, 480)}
    ratios = []
    for d in sorted(dv["SPY"]):
        num = sum(dv[s].get(d, 0) for s in MKT_LETF)
        den = sum(dv[s].get(d, 0) for s in BENCH)
        if den > 0:
            ratios.append(num / den)
    z = intensity_z(ratios)
    return {"z": round(z, 2) if z is not None else None,
            "share": round(sum(ratios[-21:]) / 21, 4) if len(ratios) >= 21 else None,
            "veto": frenzy_veto(z, regime),
            "swing_multiplier": VETO_MULTIPLIER if frenzy_veto(z, regime) else 1.0}


def swing_adjustment():
    """Drop-in for the swing trader: apply score_multiplier after the
    regime multiplier.  Only penalizes frenzy-in-healthy-tape."""
    regime = regime_state()
    f = frenzy_state(regime)
    return {"regime": regime, "frenzy_z": f["z"], "veto": f["veto"],
            "score_multiplier": f["swing_multiplier"]}


def days_to_cover(sym):
    body = {"limit": 500, "compareFilters": [
        {"compareType": "EQUAL", "fieldName": "symbolCode",
         "fieldValue": sym}]}
    try:
        rows = _http_get(FINRA_SI, post_json=body).json()
        r = max(rows, key=lambda r: r.get("settlementDate", ""))
        return {"dtc": r.get("daysToCoverQuantity"),
                "settlement": r.get("settlementDate")}
    except Exception:
        return {"dtc": None, "settlement": None}


def attention(sym, etfs):
    """L-ETF attention ratio + heavy signed-volume flag for one name."""
    und = fetch_daily(sym, 260)
    edv = {}
    for e in etfs:
        try:
            for d, _, v in fetch_daily(e, 260):
                edv[d] = edv.get(d, 0) + v
        except Exception:
            continue
    rows = [(d, edv[d] / v) for d, _, v in und if d in edv and v > 0]
    if len(rows) < 40:
        return None
    ratios = [r for _, r in rows]
    a_now, a20 = ratios[-1], sum(ratios[-21:]) / 21
    med = sorted(ratios[-127:])[len(ratios[-127:]) // 2]
    heavy = None
    if a_now > 1.5 * med and len(und) >= 2:
        heavy = "down" if und[-1][1] <= und[-2][1] else "up"
    return {"attention": round(a_now, 4), "attention20": round(a20, 4),
            "heavy": heavy}


def nightly_log(path=DEFAULT_LOG):
    regime = regime_state()
    rec = {"date": date.today().isoformat(),
           "regime": regime}
    try:
        rec["margin"] = margin_state(regime)
    except Exception as e:
        rec["margin"] = {"error": str(e)[:120]}
    try:
        rec["frenzy"] = frenzy_state(regime)
    except Exception as e:
        rec["frenzy"] = {"error": str(e)[:120]}
    names = {}
    for sym, etfs in SS_LETF.items():
        entry = {}
        try:
            att = attention(sym, etfs)
            if att:
                entry.update(att)
        except Exception:
            pass
        entry.update(days_to_cover(sym))
        names[sym] = entry
    rec["names"] = names
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


# ─── SELFTEST (pure functions only, no network) ───────────────────────

def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    # margin_metrics: 12 flat months then +50% YoY for 3 months
    base = [(f"2024-{m:02d}", 100.0) for m in range(1, 13)]
    hot = base + [("2025-01", 150.0), ("2025-02", 150.0), ("2025-03", 150.0)]
    m = margin_metrics(hot)
    check("yoy", abs(m["yoy"] - 0.5) < 1e-9)
    check("streak", m["streak"] == 3)
    check("no-decel", m["decel"] is False)
    # deceleration: 2 extreme months then a cool print
    cool = base + [("2025-01", 150.0), ("2025-02", 150.0), ("2025-03", 110.0)]
    m2 = margin_metrics(cool)
    check("decel", m2["decel"] is True and m2["streak"] == 0)
    # multipliers
    check("mult-extreme", leap_multiplier(0.45, False, "BULL") == VETO_MULTIPLIER)
    check("mult-decel", leap_multiplier(0.30, True, "DANGER") == DECEL_MULTIPLIER)
    check("mult-decel-needs-regime", leap_multiplier(0.30, True, "BULL") == 1.0)
    check("mult-normal", leap_multiplier(0.10, False, "BULL") == 1.0)
    # regime
    check("regime-bull", classify_regime(100, 90, 15, 15) == "BULL")
    check("regime-normal", classify_regime(100, 90, 25, 25) == "NORMAL")
    check("regime-caution", classify_regime(80, 90, 15, 15) == "CAUTION")
    check("regime-danger", classify_regime(80, 90, 20, 15) == "DANGER")
    # intensity z: flat history then a spike must give large positive z
    flat = [0.10] * 300
    spiky = [0.10] * 279 + [0.30] * 21
    check("z-short", intensity_z([0.1] * 100) is None)
    z_flat = intensity_z(flat)
    check("z-flat", z_flat is None or abs(z_flat) < 1e-6)
    zs = intensity_z(spiky)
    check("z-spike", zs is not None and zs > 3)
    # veto truth table
    check("veto-on", frenzy_veto(2.5, "BULL") is True)
    check("veto-normal", frenzy_veto(2.5, "NORMAL") is True)
    check("veto-warned", frenzy_veto(2.5, "DANGER") is False)
    check("veto-calm", frenzy_veto(1.0, "BULL") is False)
    check("veto-none", frenzy_veto(None, "BULL") is False)

    if fails:
        print("SELFTEST FAIL:", ", ".join(fails))
        return 1
    print("SELFTEST OK (18 checks)")
    return 0


# ─── CLI ──────────────────────────────────────────────────────────────

def check():
    regime = regime_state()
    m = margin_state(regime)
    f = frenzy_state(regime)
    print(f"Regime:  {regime}")
    if m:
        print(f"Margin:  {m['month']}  YoY {m['yoy']*100:+.1f}%  "
              f"streak {m['streak']}mo  decel {'YES' if m['decel'] else 'no'}"
              f"  -> LEAP sizing x{m['leap_multiplier']}")
    print(f"Frenzy:  L-ETF share {f['share']*100:.0f}%  z {f['z']:+.2f}  "
          f"veto {'ON — degrade bounce scores x%.2f' % VETO_MULTIPLIER if f['veto'] else 'off'}")
    top = []
    for sym, etfs in SS_LETF.items():
        try:
            a = attention(sym, etfs)
            if a:
                top.append((a["attention20"], sym, a))
        except Exception:
            continue
    top.sort(reverse=True)
    print("Attention (L-ETF $vol / underlying $vol, 20d):")
    for a20, sym, a in top[:5]:
        hv = f"  heavy-{a['heavy']}-volume today" if a["heavy"] else ""
        print(f"  {sym:<6} {a20*100:5.1f}%{hv}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    elif "--nightly" in sys.argv:
        out = DEFAULT_LOG
        if "--out" in sys.argv:
            out = sys.argv[sys.argv.index("--out") + 1]
        rec = nightly_log(out)
        print(f"logged {rec['date']} -> {out}")
    else:
        check()
