#!/usr/bin/env python3
"""
Swing flag tool
===============
Reads the journal's open-positions export, filters to Swing, fetches live prices
(and the price path since entry), computes current gain + days-held, applies
WATCH / ROT / HOLD rules, and renders an HTML report you can open in a browser.

The whole pipeline is code — no manual steps. Run it on a schedule (cron) or by hand:
    python3 swing_flag.py

It auto-finds the export at the stable path the journal writes to. Override with:
    python3 swing_flag.py /path/to/open_positions_export.csv

────────────────────────────────────────────────────────────────────────────────
THRESHOLDS — grounded in 82-trade backtest:

  • WATCH triage at worst-since-entry ≤ −8%: drawdown cliff confirmed (≤−5%
    win ~93%; −8..−12% → 20%). Fires when the WORST LOW since entry crossed
    −8%; current gain shown alongside but does not drive the flag. Earlier soft
    ring at worst ≤ −5% (WATCH?). Both are review states — NOT auto-sell
    triggers. WATCH fires even if the name has since recovered.

  • ROT at 22 days: the only losing time bucket (≥22d → win rate 37.5%, median
    −1.4%, negative return/day). ROT-deadweight (flat/down) is the loud signal;
    ROT-slow-winner (positive but sluggish) is softer — capital-inefficiency, not
    a loss. A position can carry both WATCH and ROT simultaneously.

  • TAKE removed: no static profit level derived from data.
  • Price-CUT removed: mechanical stops lost money in backtest.
  • SUSPECT: data-integrity guard only — |gain| ≥ 100% almost always means a
    bad live price or stale entry (caught HON's phantom +101.5%).

  ⚠ KNOWN GAP: fast-velocity takes (e.g. +6% on day 1) are NOT flagged — that
    threshold is not yet derived. Fast-takes remain Mike's manual call. The only
    "bank?" prompt this tool emits is ROT~ (held ≥22d, still positive).
────────────────────────────────────────────────────────────────────────────────

Requires: pip install yfinance pandas  (already in stock-tracker-env)
"""

import csv
import os
import sys
import webbrowser
from datetime import datetime, timedelta

# ── Stable input path (matches the journal's SWING_EXPORT_PATH) ──
DEFAULT_EXPORT = os.path.expanduser("~/Desktop/swing_project/open_positions_export.csv")
OUTPUT_HTML    = os.path.expanduser("~/Desktop/swing_project/swing_flags.html")

# ════════════════════════════════════════════════════════════════
#  RULE THRESHOLDS — grounded numbers only (see header)
# ════════════════════════════════════════════════════════════════
WATCH_UNDERWATER_PCT = -8.0    # triage: worst-since-entry ≤ this → WATCH (−8% cliff)
WATCH_SOFT_PCT       = -5.0    # early ring: worst in (−8, −5] → WATCH? (approaching cliff)
ROT_DAYS             = 22      # time cliff: held ≥ this → ROT (deadweight) or ROT~ (slow winner)
SUSPECT_GAIN_PCT     = 100.0   # |gain| ≥ this → data-integrity guard (never a real signal)
# ════════════════════════════════════════════════════════════════

# Sort priority: lower = more urgent. Positions with multiple flags sort by their best (lowest).
_FLAG_ORDER = {"SUSPECT": -1, "WATCH": 0, "ROT": 0, "WATCH?": 1, "ROT~": 1, "HOLD": 2}
_FLAG_COLORS = {
    "SUSPECT": "#c300ff",
    "WATCH":   "#ff8c00",
    "WATCH?":  "#e0a020",
    "ROT":     "#ff4d4d",
    "ROT~":    "#ffd700",
    "HOLD":    "#888888",
}


def classify(current_gain_pct, underwater_pct, days_held):
    """WATCH keys off worst-since-entry (validated -8% metric); ROT off time; current gain shown for context."""
    # Data-integrity guard: implausible CURRENT gain OR worst-point = bad price/stale entry, not a real move.
    if abs(current_gain_pct) >= SUSPECT_GAIN_PCT or abs(underwater_pct) >= SUSPECT_GAIN_PCT:
        return ["SUSPECT"], (f"{current_gain_pct:+.0f}% gain / {underwater_pct:+.0f}% worst is implausibly large "
                             f"-- almost certainly bad price or stale entry. Verify; do NOT act on it.")

    flags, reasons = [], []

    # WATCH -- WORST-since-entry vs entry (the validated cliff). Current gain shown alongside.
    if underwater_pct <= WATCH_UNDERWATER_PCT:
        flags.append("WATCH")
        reasons.append(f"hit {underwater_pct:.1f}% at worst (past -8% cliff), now {current_gain_pct:+.1f}% -- scrutinize thesis")
    elif underwater_pct <= WATCH_SOFT_PCT:
        flags.append("WATCH?")
        reasons.append(f"worst {underwater_pct:.1f}% (-5 to -8 ring), now {current_gain_pct:+.1f}%")

    # ROT -- time signal (22-day cliff), split by current direction
    if days_held >= ROT_DAYS:
        if current_gain_pct <= 0:
            flags.append("ROT")
            reasons.append(f"{days_held}d held, flat/down {current_gain_pct:.1f}% -- deadweight (cut?)")
        else:
            flags.append("ROT~")
            reasons.append(f"{days_held}d held, up {current_gain_pct:.1f}% -- slow winner (bank?)")

    if not flags:
        return ["HOLD"], f"now {current_gain_pct:+.1f}%, worst {underwater_pct:.1f}%, held {days_held}d -- within normal range"

    return flags, " | ".join(reasons)


# ── Self-test (no network) ──────────────────────────────────────
def _selftest():
    # SUSPECT: implausible magnitude (current OR worst) exits before any trading flag
    assert classify(+101.5, -3.0, 5)[0]  == ["SUSPECT"], "SUSPECT +big current"
    assert classify(-150.0, -3.0, 10)[0] == ["SUSPECT"], "SUSPECT -big current"
    assert classify(-2.0, -150, 10)[0]   == ["SUSPECT"], "SUSPECT implausible worst"
    assert classify(+99.9, -3.0, 5)[0]   != ["SUSPECT"], "+99.9 is under threshold"
    # WATCH triage: keys off WORST-since-entry ≤ −8% (not current gain)
    assert classify(0.0,   -8.0, 5)[0]   == ["WATCH"],  "WATCH worst at exact -8% threshold"
    assert classify(+3.0, -12.0, 5)[0]   == ["WATCH"],  "WATCH worst deep, currently positive"
    assert classify(-2.0,  -9.0, 5)[0]   == ["WATCH"],  "WATCH: current only -2% but worst -9%"
    assert classify(+5.0,  -9.0, 5)[0]   == ["WATCH"],  "WATCH: breached -8 then recovered to +5"
    # WATCH soft ring: worst in (−8, −5]
    assert classify(0.0,  -5.0, 5)[0]    == ["WATCH?"], "WATCH? worst at exact -5% soft threshold"
    assert classify(0.0,  -6.5, 5)[0]    == ["WATCH?"], "WATCH? worst mid-band"
    assert classify(-1.0, -6.0, 5)[0]    == ["WATCH?"], "WATCH? soft ring keys off worst"
    # HOLD baseline: worst above -5%, short-held
    assert classify(+4.0, -2.0, 5)[0]    == ["HOLD"],   "HOLD positive short-held"
    assert classify(-4.9, -4.9, 5)[0]    == ["HOLD"],   "HOLD just under soft ring"
    # ROT deadweight (≥22d, gain ≤ 0) — use worst above -5% to isolate time signal
    assert classify(-2.0, -2.0, 22)[0]   == ["ROT"],    "ROT deadweight at 22d"
    assert classify(0.0,  -1.0, 25)[0]   == ["ROT"],    "ROT deadweight flat"
    # ROT slow winner (≥22d, gain > 0)
    assert classify(+5.0, -3.0, 22)[0]   == ["ROT~"],   "ROT~ slow winner at 22d"
    assert classify(+15.0, -3.0, 30)[0]  == ["ROT~"],   "ROT~ slow winner deep"
    # WATCH + ROT stack: worst ≤ -8% AND ≥22d AND flat/down
    f, _ = classify(-6.6, -9.4, 25)
    assert "WATCH" in f and "ROT" in f, f"AMZN case: expected WATCH+ROT, got {f}"
    f2, _ = classify(-9.0, -9.0, 25)
    assert "WATCH" in f2 and "ROT" in f2, f"expected WATCH+ROT stack, got {f2}"
    # WATCH? + ROT stack: worst in (-8, -5] AND ≥22d AND gain ≤ 0
    f3, _ = classify(-6.0, -6.0, 24)
    assert "WATCH?" in f3 and "ROT" in f3, f"expected WATCH?+ROT stack, got {f3}"
    # Confirm no TAKE or CUT flags exist in new logic
    for gain, worst, days in [(+25, -3.0, 5), (+30, -2.0, 10), (-16, -16.0, 3), (-20, -20.0, 5)]:
        flags, _ = classify(gain, worst, days)
        assert "TAKE" not in flags and "CUT" not in flags, f"unexpected TAKE/CUT at gain={gain}"
    print("  self-test: rule logic OK ✓")


def find_input():
    # positional arg = input path, but skip option flags like --no-open
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a
    if os.path.isfile(DEFAULT_EXPORT):
        return DEFAULT_EXPORT
    return None


def fetch(ticker, buy_date, yf):
    """Fetch daily OHLC from buy_date to today. Returns DataFrame or None after 3 attempts."""
    for attempt in range(3):
        try:
            df = yf.Ticker(ticker).history(
                start=buy_date.strftime("%Y-%m-%d"),
                end=(datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
                interval="1d", auto_adjust=False, actions=False)
            if df is not None and len(df) > 0 and "Close" in df.columns:
                return df
        except Exception:
            pass
    return None


def main():
    print("Swing flag tool\n")
    _selftest()

    try:
        import yfinance as yf
    except ImportError:
        print("\nERROR: yfinance not found:\n  pip install yfinance pandas --break-system-packages")
        sys.exit(1)

    path = find_input()
    if not path or not os.path.isfile(path):
        print(f"\nERROR: no export found at {DEFAULT_EXPORT}\n"
              f"Run the journal's Export Open first, or pass a path explicitly.")
        sys.exit(1)

    # Export freshness — show mtime so a stale snapshot is immediately visible
    export_mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")

    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f)]
    swing = [r for r in rows if r.get("Strategy", "").strip() == "Swing Trader"]
    print(f"Read {len(rows)} open positions from {path}")
    print(f"  export snapshot: {export_mtime}")
    print(f"  filtered to Swing Trader: {len(swing)}\n")

    results, failed = [], []
    for i, r in enumerate(swing, 1):
        tkr = r["Ticker"].strip().upper()
        try:
            buy_price = float(r["Buy Price"])
            buy_date  = datetime.strptime(r["Buy Date"], "%Y-%m-%d")
            qty       = float(r["Quantity"])
        except (ValueError, KeyError):
            failed.append((tkr, "bad row")); continue

        print(f"  [{i}/{len(swing)}] {tkr}")
        df = fetch(tkr, buy_date, yf)
        if df is None:
            failed.append((tkr, "price unavailable (fetch failed)")); continue

        # Drop rows without usable prices — a NaN here would produce a bogus flag
        df = df.dropna(subset=["Close", "Low"])
        if len(df) == 0:
            failed.append((tkr, "price unavailable (no usable rows)")); continue

        current   = float(df["Close"].iloc[-1])
        worst_low = float(df["Low"].min())
        if not (current > 0) or not (worst_low > 0):
            failed.append((tkr, "price unavailable (non-positive/NaN)")); continue

        gain_pct       = (current - buy_price) / buy_price * 100.0
        underwater_pct = (worst_low - buy_price) / buy_price * 100.0
        # Explicit NaN guard on computed values (float arithmetic edge cases)
        if gain_pct != gain_pct or underwater_pct != underwater_pct:
            failed.append((tkr, "price unavailable (NaN after compute)")); continue

        days_held    = (datetime.now() - buy_date).days
        gain_per_day = gain_pct / days_held if days_held > 0 else None
        flags, reason = classify(gain_pct, underwater_pct, days_held)

        results.append({
            "ticker": tkr, "qty": qty, "buy_price": buy_price, "current": current,
            "gain_pct": gain_pct, "underwater_pct": underwater_pct,
            "days": days_held, "gain_per_day": gain_per_day,
            "value": current * qty, "unrealized": (current - buy_price) * qty,
            "flags": flags, "reason": reason,
        })

    # Sort: most-urgent flag wins; within same priority, worst gain first
    def _sort_key(x):
        return (min(_FLAG_ORDER.get(f, 2) for f in x["flags"]), x["gain_pct"])
    results.sort(key=_sort_key)

    _render_html(results, failed, OUTPUT_HTML, export_mtime)
    _print_terminal(results, failed, export_mtime)

    if "--no-open" not in sys.argv:
        try:
            webbrowser.open(f"file://{OUTPUT_HTML}")
        except Exception:
            pass
    print(f"\nReport: {OUTPUT_HTML}")


def _print_terminal(results, failed, export_mtime):
    from collections import Counter
    # Primary flag = most urgent per position (for tile counts only)
    c = Counter(min(r["flags"], key=lambda f: _FLAG_ORDER.get(f, 2)) for r in results)
    print("\n" + "=" * 80)
    print(f"FLAGS — SUSPECT:{c.get('SUSPECT',0)}  WATCH:{c.get('WATCH',0)}  "
          f"WATCH?:{c.get('WATCH?',0)}  ROT:{c.get('ROT',0)}  "
          f"ROT~:{c.get('ROT~',0)}  HOLD:{c.get('HOLD',0)}")
    print(f"Export snapshot: {export_mtime}")
    print("=" * 80)
    print(f"  {'flags':<16}{'ticker':<8}{'gain':>8}{'worst':>8}{'days':>6}{'g/d':>8}  reason")
    for r in results:
        flag_str = "+".join(r["flags"])
        gpd = f"{r['gain_per_day']:+.2f}" if r["gain_per_day"] is not None else "—"
        print(f"  {flag_str:<16}{r['ticker']:<8}{r['gain_pct']:>+7.1f}%"
              f"{r['underwater_pct']:>+7.1f}%{r['days']:>5}d{gpd:>7}%/d  {r['reason']}")
    if failed:
        print(f"\n  ! price unavailable (excluded from flagging): "
              f"{', '.join(t for t, _ in failed)}")
    print(f"\n  Known gap: fast-velocity takes (e.g. +6% day 1) are NOT flagged — "
          f"threshold not yet derived. Fast-takes remain manual.")


def _render_html(results, failed, out, export_mtime):
    from collections import Counter
    c = Counter(min(r["flags"], key=lambda f: _FLAG_ORDER.get(f, 2)) for r in results)

    rows_html = ""
    for r in results:
        badges = "".join(
            f'<span style="background:{_FLAG_COLORS.get(f,"#888")};color:#0f1117;'
            f'padding:2px 6px;border-radius:4px;font-weight:700;margin-right:3px">{f}</span>'
            for f in r["flags"]
        )
        g   = f"{r['gain_pct']:+.1f}%"
        u   = f"{r['underwater_pct']:+.1f}%"
        gpd = f"{r['gain_per_day']:+.2f}%/d" if r["gain_per_day"] is not None else "—"
        rows_html += f"""<tr>
          <td>{badges}</td>
          <td style="font-weight:600">{r['ticker']}</td>
          <td style="text-align:right;color:{'#00ff9d' if r['gain_pct']>=0 else '#ff4d4d'}">{g}</td>
          <td style="text-align:right;color:#ff8c00">{u}</td>
          <td style="text-align:right">{r['days']}d</td>
          <td style="text-align:right;color:#aaa;font-size:12px">{gpd}</td>
          <td style="text-align:right">${r['value']:,.0f}</td>
          <td style="text-align:right;color:{'#00ff9d' if r['unrealized']>=0 else '#ff4d4d'}">${r['unrealized']:+,.0f}</td>
          <td style="color:#aaa;font-size:13px">{r['reason']}</td></tr>"""

    fail_html = ""
    if failed:
        fail_html = ("<p style='color:#ff8c00'>Price unavailable — excluded from flagging: "
                     + ", ".join(t for t, _ in failed) + "</p>")

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Swing Flags</title><style>
body{{background:#0f1117;color:#e0e0e0;font-family:-apple-system,Helvetica,Arial,sans-serif;margin:0;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:18px}}
.tiles{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}}
.tile{{background:#1a1f2e;border-radius:10px;padding:12px 18px;min-width:70px}}
.tile .n{{font-size:26px;font-weight:700}} .tile .l{{font-size:12px;color:#888}}
.gap-note{{color:#666;font-size:12px;margin-bottom:14px;border-left:3px solid #2a3246;padding:6px 10px}}
table{{width:100%;border-collapse:collapse;background:#1a1f2e;border-radius:10px;overflow:hidden}}
th{{text-align:left;color:#888;font-size:12px;font-weight:600;padding:10px 12px;border-bottom:1px solid #2a3246}}
td{{padding:9px 12px;border-bottom:1px solid #20263a;font-size:14px}}
tr:last-child td{{border-bottom:none}}
</style></head><body>
<h1>Swing position flags</h1>
<div class="sub">{datetime.now():%Y-%m-%d %H:%M} · {len(results)} swing positions priced · export snapshot: {export_mtime}</div>
<div class="tiles">
  <div class="tile"><div class="n" style="color:#c300ff">{c.get('SUSPECT',0)}</div><div class="l">SUSPECT</div></div>
  <div class="tile"><div class="n" style="color:#ff8c00">{c.get('WATCH',0)}</div><div class="l">WATCH</div></div>
  <div class="tile"><div class="n" style="color:#e0a020">{c.get('WATCH?',0)}</div><div class="l">WATCH?</div></div>
  <div class="tile"><div class="n" style="color:#ff4d4d">{c.get('ROT',0)}</div><div class="l">ROT</div></div>
  <div class="tile"><div class="n" style="color:#ffd700">{c.get('ROT~',0)}</div><div class="l">ROT~</div></div>
  <div class="tile"><div class="n" style="color:#888">{c.get('HOLD',0)}</div><div class="l">HOLD</div></div>
</div>
<div class="gap-note">⚠ Known gap: fast-velocity takes (e.g. +6% day 1) are not flagged — threshold not yet derived. Fast-takes remain manual. The only "bank?" prompt here is ROT~ (held ≥{ROT_DAYS}d, currently positive).</div>
{fail_html}
<table>
  <tr><th>Flag(s)</th><th>Ticker</th><th>Gain</th><th>Worst</th><th>Held</th>
  <th>Gain/day</th><th>Value</th><th>Unreal.</th><th>Why</th></tr>
  {rows_html}
</table>
<p style="color:#555;font-size:12px;margin-top:16px">
WATCH = worst-since-entry ≤ −8% (triage, not auto-sell) · WATCH? = worst −5..−8% early ring ·
ROT = held ≥{ROT_DAYS}d flat/down (deadweight) · ROT~ = held ≥{ROT_DAYS}d but positive (slow winner, capital-inefficient) ·
Positions can carry multiple flags. Gain/day is display-only — no velocity threshold derived yet.
</p>
</body></html>"""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(html)


if __name__ == "__main__":
    main()
