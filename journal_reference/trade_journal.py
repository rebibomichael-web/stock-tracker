#!/usr/bin/env python3
"""
Trade Journal — Fidelity Tracker v2.0
======================================
A Linux desktop app for analyzing Fidelity brokerage trade history.

Changes in v2.0:
  - Dropdown calendar date pickers (no more YYYY-MM-DD text fields)
  - Remembers last uploaded CSV path, auto-loads on launch
  - Methods renamed: A → Swing Trader, B → LEAP Strategy, Both → removed
  - New "Excluded" designation — excluded trades skipped from calculations
  - Dropdown combo in main table to change designation per trade
  - Charts have toggle: Closed Only / All (incl open positions)
  - Drag-and-drop fixed (fallback to file dialog always works)

Dependencies:
  pip3 install pandas matplotlib tkcalendar --break-system-packages
  sudo apt install python3-tk -y

Optional (for native drag-and-drop):
  pip3 install tkinterdnd2 --break-system-packages

Run:
  python3 trade_journal.py
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import sqlite3
import csv
import os
import re
import sys
import subprocess
import json
import math
from datetime import datetime, timedelta
from collections import defaultdict
from io import StringIO

# Calendar dropdown
try:
    from tkcalendar import DateEntry
    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

# Optional drag-and-drop support
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# Matplotlib with TkAgg backend
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.dates as mdates


# ─── Color Palette ──────────────────────────────────────────────
BG       = "#0f1117"
PANEL    = "#1a1f2e"
HEADER   = "#161b27"
BORDER   = "#2a3246"
GREEN    = "#00ff9d"
RED      = "#ff4d4d"
BLUE     = "#00b8ff"
PURPLE   = "#c300ff"
ORANGE   = "#ff8c00"
TEXT     = "#e0e0e0"
DIM      = "#888888"
WHITE    = "#ffffff"
EXCL_DIM = "#555555"
ORPHAN   = "#ff6b6b"   # sell-with-no-buy marker — softer red than RED so it reads as warning, not loss

# Method labels and colors
METHODS = ["Swing Trader", "LEAP Strategy", "Excluded"]
METHOD_COLORS = {
    "Swing Trader": GREEN,
    "LEAP Strategy": BLUE,
    "Excluded": EXCL_DIM,
}
# Short display names for table column
METHOD_SHORT = {
    "Swing Trader": "Swing",
    "LEAP Strategy": "LEAP",
    "Excluded": "Excl",
}
METHOD_FROM_SHORT = {v: k for k, v in METHOD_SHORT.items()}

# Performance benchmark — Swing strategy monthly return target, measured against
# ALLOCATED swing-program capital (the entire sum set aside for swing), NOT deployed
# capital and NOT total brokerage value. This captures idle-cash drag: if the sleeve
# is mostly uninvested, return-on-allocation falls below return-on-deployed.
SWING_MONTHLY_TARGET_PCT = 3.0

# Default swing allocation = peak concurrent deployment observed in the data
# (the conservative "never capital-blocked" figure). User-editable in the UI and
# persisted to config; override with your real sleeve size. Analysis showed the
# honest return crosses the 3% target around the P90 of concurrent demand (~$3,100),
# so tuning this down from peak is expected.
DEFAULT_SWING_ALLOCATION = 5582.0

# Legacy migration map
LEGACY_METHOD_MAP = {
    "A": "Swing Trader",
    "B": "LEAP Strategy",
    "Both": "Swing Trader",  # migrate Both → Swing Trader
}

# Option symbols in Fidelity format look like "-NOW280121C200" or "SOFI280616C32":
# (optional leading dash) ticker + YYMMDD + C/P + strike. This is a RELIABLE,
# symbol-based test — unlike the CALL/PUT keyword heuristic, it never misfires on
# plain tickers (RGTI, REGN, AA all correctly return False).
OPTION_SYMBOL_RE = re.compile(r"^-?[A-Z]{1,6}\d{6}[CP]\d")

def looks_like_option_symbol(sym):
    return bool(OPTION_SYMBOL_RE.match(str(sym).strip().upper()))

def default_method_for(leg):
    """Smart default for an UNTAGGED leg, so options/assignment byproducts never
    silently land in the Swing bucket (which previously skewed swing analytics):
      • real option symbol   → 'LEAP Strategy'  (it's an option, not a swing trade)
      • assignment-derived   → 'Excluded'       (wheel byproduct, not a directional bet)
      • otherwise            → 'Swing Trader'
    Saved tags always take precedence over this — it only fills the default.
    NOTE: a regular stock buy on an options-play ticker (e.g. a fractional BMNR
    remnant) is indistinguishable from a genuine swing trade by transaction data
    alone; that case is left as Swing and needs an explicit user tag."""
    if looks_like_option_symbol(leg.get("ticker", "")):
        return "LEAP Strategy"
    if leg.get("from_assignment"):
        return "Excluded"
    return "Swing Trader"

# ─── Config file for remembering last CSV ───────────────────────
CONFIG_PATH = os.path.expanduser("~/.trade_journal_config.json")

# Stable path the open-positions export auto-writes to, so the downstream "swing"
# flag tool can always find it with zero configuration. Directory is created if
# missing. If swing runs on a DIFFERENT machine than the journal, point this at a
# synced folder (Dropbox/Drive/network share) — that's the only line to change.
SWING_EXPORT_PATH = os.path.expanduser("~/Desktop/swing_project/open_positions_export.csv")

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def get_swing_allocation():
    """Swing-program capital the 3% target is measured against. Persisted to config."""
    cfg = load_config()
    try:
        v = float(cfg.get("swing_allocation", DEFAULT_SWING_ALLOCATION))
        return v if v > 0 else DEFAULT_SWING_ALLOCATION
    except (TypeError, ValueError):
        return DEFAULT_SWING_ALLOCATION

def set_swing_allocation(val):
    cfg = load_config()
    cfg["swing_allocation"] = float(val)
    save_config(cfg)


# ─── Database ────────────────────────────────────────────────────
DB_PATH = os.path.expanduser("~/.trade_journal_tags.db")

def init_db():
    """Create tags table if not exists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            trade_key TEXT PRIMARY KEY,
            method TEXT DEFAULT 'Swing Trader'
        )
    """)
    conn.commit()
    conn.close()

def load_tags():
    """Load all saved tags, migrating legacy A/B/Both on the fly."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT trade_key, method FROM tags").fetchall()
    conn.close()
    result = {}
    for k, v in rows:
        if v in LEGACY_METHOD_MAP:
            migrated = LEGACY_METHOD_MAP[v]
            save_tag(k, migrated)
            result[k] = migrated
        else:
            result[k] = v
    return result

def save_tag(trade_key, method):
    """Upsert a single tag."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO tags (trade_key, method) VALUES (?, ?)",
        (trade_key, method)
    )
    conn.commit()
    conn.close()


# ─── CSV Parsing & FIFO Matching ─────────────────────────────────

def _norm_col(c):
    """Normalize a column name: lowercase, collapse any non-alphanumeric run to _."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", c.strip().lower())).strip("_")


def _parse_num(val):
    """Parse a numeric cell that may contain $, commas, or parentheses for negatives."""
    if pd.isna(val) or str(val).strip() in ("", "--", "nan", "n/a", "N/A"):
        return 0.0
    s = str(val).replace("$", "").replace(",", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return 0.0


def _find_header_row(lines, *required_phrases, max_scan=20):
    """
    Return the index of the first line (within max_scan non-empty lines) whose
    lowercased text contains every phrase in required_phrases.
    Falls back to scanning the full file if not found in the first pass.
    """
    def matches(line):
        low = line.strip().lower()
        return all(ph in low for ph in required_phrases)

    checked = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if matches(line):
            return i
        checked += 1
        if checked >= max_scan:
            break

    # Full-file fallback
    for i, line in enumerate(lines):
        if matches(line):
            return i
    return None


def _load_df(lines, header_idx):
    """Build a DataFrame from lines starting at header_idx, skipping blank rows."""
    data_lines = [l for l in lines[header_idx:] if l.strip()]
    df = pd.read_csv(StringIO("\n".join(data_lines)), dtype=str, skipinitialspace=True)
    df.columns = [_norm_col(c) for c in df.columns]
    return df


def _parse_trade_history(lines, header_idx):
    """
    Parse a Fidelity trade history CSV (Run Date / Action / Symbol format).
    Returns (closed_legs, open_positions, orphan_sells) after FIFO matching.
    """
    df = _load_df(lines, header_idx)

    required = {"run_date", "action", "symbol"}
    if not required.issubset(set(df.columns)):
        raise ValueError(
            f"Trade history header found but missing columns.\n"
            f"Found: {list(df.columns)}\nNeed: {sorted(required)}"
        )

    skip_keywords = ["DIVIDEND", "REINVEST", "TRANSFER", "MARGIN INTEREST",
                     "JOURNALED", "ELECTRONIC FUNDS", "DIRECT DEBIT",
                     "SHORT TERM CAP GAIN", "LONG TERM CAP GAIN"]

    transactions = []
    for _, row in df.iterrows():
        action = str(row.get("action", "")).strip().upper()
        symbol = str(row.get("symbol", "")).strip().upper()

        if not symbol or symbol == "NAN" or len(symbol) > 20:
            continue
        if any(kw in action for kw in skip_keywords):
            continue

        is_buy  = "BOUGHT" in action or "BUY" in action
        is_sell = "SOLD"   in action or "SELL" in action
        if not is_buy and not is_sell:
            continue

        desc = str(row.get("description", "")).strip()
        is_option = (any(kw in action for kw in ("CALL", "PUT", "OPTION")) or
                     any(kw in desc.upper() for kw in ("CALL", "PUT")))

        # Assignment origin: shares acquired via option assignment (e.g.
        # "YOU BOUGHT ASSIGNED PUTS AS OF ...") are a wheel/options byproduct,
        # NOT a directional swing decision. Flag so they don't default to Swing.
        from_assignment = "ASSIGNED" in action

        qty        = abs(_parse_num(row.get("quantity",   0)))
        price      = abs(_parse_num(row.get("price",      0)))
        amount     =     _parse_num(row.get("amount",     0))
        commission = abs(_parse_num(row.get("commission", 0)))
        fees       = abs(_parse_num(row.get("fees",       0)))

        date_str = str(row.get("run_date", "")).strip()
        dt = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            continue

        ticker_label = f"{symbol} ({desc[:40]})" if is_option and desc else symbol

        amount_per_unit = abs(amount) / qty if qty > 0 else price
        transactions.append({
            "date":            dt,
            "action":          "BUY" if is_buy else "SELL",
            "symbol":          symbol,
            "ticker_label":    ticker_label,
            "qty":             qty,
            "price":           price,
            "amount":          abs(amount),
            "amount_per_unit": amount_per_unit,
            "commission":      commission,
            "fees":            fees,
            "is_option":       is_option,
            "description":     desc,
            "from_assignment": from_assignment,
        })

    transactions.sort(key=lambda x: x["date"])
    return fifo_match(transactions)


def _parse_positions(lines, header_idx):
    """
    Parse a Fidelity portfolio positions CSV (Symbol / Quantity / Last Price format).
    Returns ([], open_positions) — no FIFO matching possible from a snapshot.
    """
    df = _load_df(lines, header_idx)

    def col(*keywords):
        """Return the first column name that contains every keyword."""
        for c in df.columns:
            if all(kw in c for kw in keywords):
                return c
        return None

    col_qty       = col("quantity")
    col_last      = col("last_price") or col("last", "price")
    col_cost_tot  = col("cost_basis_total") or col("cost", "basis", "total") or col("cost", "total")
    col_avg_cost  = col("average_cost_basis") or col("average", "cost") or col("avg", "cost")
    col_gl_dollar = col("total_gain_loss_dollar") or col("total", "gain") or col("total", "loss")
    col_gl_pct    = col("total_gain_loss_percent") or col("total", "gain", "percent")
    col_desc      = "description" if "description" in df.columns else None
    col_type      = "type"        if "type"        in df.columns else None

    # ── Validation gate (fail loud; do NOT fabricate) ───────────────
    # Detects column misalignment before any record is built. The known
    # trigger: Fidelity quotes the entire header as one string and appends a
    # trailing comma to every data row, so data rows carry one more field than
    # the header and every value lands one column to the left. Symptom: the
    # quantity column holds currency-formatted strings ($, %) instead of bare
    # numbers. We refuse rather than emit $0 fabricated positions.
    #
    # NOTE: this gate only detects. The dialect itself is fixed in a separate,
    # later change — keeping detection and parsing as independent variables.
    def _looks_like_quantity(series):
        """True if the column reads like real share/contract counts."""
        checked = parsed = 0
        for raw in series:
            s = str(raw).strip()
            if s in ("", "--", "nan", "n/a", "N/A"):
                continue
            checked += 1
            # A genuine quantity has no currency or percent markers.
            if "$" in s or "%" in s:
                continue
            try:
                float(s.replace(",", ""))
                parsed += 1
            except ValueError:
                pass
        if checked == 0:
            return True   # nothing to judge — let downstream emptiness handling deal with it
        return (parsed / checked) >= 0.8

    if col_qty is not None and not _looks_like_quantity(df[col_qty]):
        sample_vals = [str(v).strip() for v in df[col_qty].head(4)
                       if str(v).strip() not in ("", "nan")]
        raise ValueError(
            "Positions CSV column misalignment detected — refusing to parse.\n\n"
            f"The '{col_qty}' column should contain share/contract counts, but it holds "
            f"currency/percent values like: {sample_vals}\n\n"
            "Likely cause: this Fidelity export quotes the entire header row as a single "
            "string and adds a trailing comma to each data row, so every value is shifted "
            "one column. The app will not emit fabricated $0 positions from misaligned data.\n\n"
            "This is a known format issue; a dialect fix is planned. For now, re-export "
            "without the quoted header, or load a trade-history CSV instead."
        )

    open_positions = []

    for _, row in df.iterrows():
        symbol = str(row.get("symbol", "")).strip().upper()

        # Skip totals, blanks, pending-activity sentinel rows
        if (not symbol or symbol == "NAN" or
                symbol.startswith("**") or symbol.startswith("--") or
                "PENDING" in symbol or "TOTAL" in symbol):
            continue

        qty = abs(_parse_num(row.get(col_qty, 0))) if col_qty else 0.0
        if qty == 0.0:
            continue

        last_price = _parse_num(row.get(col_last,     0)) if col_last     else 0.0
        buy_cost   = _parse_num(row.get(col_cost_tot, 0)) if col_cost_tot else 0.0
        avg_cost   = _parse_num(row.get(col_avg_cost, 0)) if col_avg_cost else (buy_cost / qty if qty else 0.0)
        gl_dollar  = _parse_num(row.get(col_gl_dollar,0)) if col_gl_dollar else 0.0
        gl_pct     = _parse_num(row.get(col_gl_pct,  0)) if col_gl_pct   else 0.0

        desc       = str(row.get(col_desc, "")).strip() if col_desc else ""
        asset_type = str(row.get(col_type, "")).strip().upper() if col_type else ""

        is_option = (
            "OPTION" in asset_type or
            any(kw in desc.upper() for kw in ("CALL", "PUT")) or
            (len(symbol) > 10 and any(c.isdigit() for c in symbol))
        )

        if is_option:
            qty_str      = f"{int(qty)} contract{'s' if qty != 1 else ''}"
            ticker_label = f"{symbol} ({desc[:40]})" if desc else symbol
        elif qty == int(qty):
            qty_str      = f"{int(qty)} sh"
            ticker_label = symbol
        else:
            qty_str      = f"{qty:.4f} sh"
            ticker_label = symbol

        trade_key = f"{symbol}-POSITIONS-{qty:.4f}"

        open_positions.append({
            "ticker":       symbol,
            "ticker_label": ticker_label,
            "buy_date":     None,          # snapshot — no purchase date available
            "sell_date":    None,
            "qty":          qty,
            "qty_str":      qty_str,
            "buy_price":    avg_cost,
            "sell_price":   last_price,
            "buy_cost":     buy_cost,
            "sell_proceeds":0.0,
            "commission":   0.0,
            "pl_dollar":    gl_dollar,
            "pl_pct":       gl_pct,
            "hold_days":    0,
            "is_option":    is_option,
            "description":  desc,
            "trade_key":    trade_key,
            "is_open":      True,
        })

    return [], open_positions, []


def parse_fidelity_csv(filepath):
    """
    Auto-detect Fidelity CSV format and parse.
    Supports:
      • Trade history  (Run Date / Action / Symbol / Price / Amount …)
      • Positions snapshot  (Symbol / Quantity / Last Price / Cost Basis …)
    Returns (closed_legs, open_positions, orphan_sells).
    """
    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    lines = raw.splitlines()

    # ── Trade history format ──
    idx = _find_header_row(lines, "run date", "action", "symbol")
    if idx is not None:
        return _parse_trade_history(lines, idx)

    # ── Positions / portfolio format ──
    idx = _find_header_row(lines, "symbol", "quantity")
    if idx is not None:
        # Extra guard: must also have a price-like or cost-like column nearby
        header_low = lines[idx].lower()
        if any(kw in header_low for kw in ("last price", "cost basis", "current value")):
            return _parse_positions(lines, idx)

    sample = "\n".join(f"  [{i}] {l}" for i, l in enumerate(lines[:5]))
    raise ValueError(
        "Unrecognized Fidelity CSV format.\n"
        "Expected either:\n"
        "  • Trade history  — columns: Run Date, Action, Symbol, Price, Amount …\n"
        "  • Positions CSV  — columns: Symbol, Quantity, Last Price, Cost Basis …\n\n"
        f"First 5 rows of your file:\n{sample}"
    )


def fifo_match(transactions):
    """
    FIFO matching engine.
    Returns (closed_legs, open_positions, orphan_sells).
    Orphan sells = SELLs with no matching BUY in this CSV.
    """
    by_symbol = defaultdict(list)
    for tx in transactions:
        by_symbol[tx["symbol"]].append(tx)

    closed_legs = []
    open_positions = []
    orphan_sells = []   # SELLs with no prior BUY in this CSV — preserved instead of silently dropped

    for symbol, txs in by_symbol.items():
        buy_queue = []
        seen_keys = {}

        for tx in txs:
            if tx["action"] == "BUY":
                comm_per = tx["commission"] / tx["qty"] if tx["qty"] > 0 else 0
                fees_per = tx["fees"] / tx["qty"] if tx["qty"] > 0 else 0
                buy_queue.append({
                    "date": tx["date"],
                    "qty_remaining": tx["qty"],
                    "price": tx["price"],
                    "amount_per_unit": tx["amount_per_unit"],
                    "comm_per": comm_per,
                    "fees_per": fees_per,
                    "ticker_label": tx["ticker_label"],
                    "is_option": tx["is_option"],
                    "description": tx["description"],
                    "symbol": symbol,
                    "from_assignment": tx.get("from_assignment", False),
                })

            elif tx["action"] == "SELL":
                sell_qty = tx["qty"]
                sell_price = tx["price"]
                sell_comm = tx["commission"]
                sell_fees = tx["fees"]
                sell_comm_per = sell_comm / sell_qty if sell_qty > 0 else 0
                sell_fees_per = sell_fees / sell_qty if sell_qty > 0 else 0

                while sell_qty > 1e-8 and buy_queue:
                    lot = buy_queue[0]
                    match_qty = min(sell_qty, lot["qty_remaining"])

                    buy_cost = match_qty * lot["amount_per_unit"]
                    buy_comm = match_qty * lot["comm_per"]
                    buy_fees_total = match_qty * lot["fees_per"]
                    sell_proceeds = match_qty * tx["amount_per_unit"]
                    sell_comm_portion = match_qty * sell_comm_per
                    sell_fees_portion = match_qty * sell_fees_per

                    total_costs = buy_comm + buy_fees_total + sell_comm_portion + sell_fees_portion
                    pl_dollar = sell_proceeds - buy_cost - total_costs
                    pl_pct = (pl_dollar / buy_cost * 100) if buy_cost > 0 else 0.0
                    hold_days = (tx["date"] - lot["date"]).days

                    if lot["is_option"]:
                        qty_str = f"{int(match_qty)} contract{'s' if match_qty > 1 else ''}"
                    elif match_qty == int(match_qty):
                        qty_str = f"{int(match_qty)} sh"
                    else:
                        qty_str = f"{match_qty:.4f} sh"

                    base_key = f"{symbol}-{lot['date'].strftime('%Y%m%d')}-{tx['date'].strftime('%Y%m%d')}-{match_qty:.4f}"
                    seen_keys[base_key] = seen_keys.get(base_key, 0) + 1
                    trade_key = base_key if seen_keys[base_key] == 1 else f"{base_key}-{seen_keys[base_key]}"

                    closed_legs.append({
                        "ticker": symbol,
                        "ticker_label": lot["ticker_label"],
                        "buy_date": lot["date"],
                        "sell_date": tx["date"],
                        "qty": match_qty,
                        "qty_str": qty_str,
                        "buy_price": lot["price"],
                        "sell_price": sell_price,
                        "buy_cost": buy_cost,
                        "sell_proceeds": sell_proceeds,
                        "commission": total_costs,
                        "pl_dollar": round(pl_dollar, 2),
                        "pl_pct": round(pl_pct, 2),
                        "hold_days": hold_days,
                        "is_option": lot["is_option"],
                        "description": lot["description"],
                        "trade_key": trade_key,
                        "is_open": False,
                        "from_assignment": lot.get("from_assignment", False),
                    })

                    lot["qty_remaining"] -= match_qty
                    sell_qty -= match_qty
                    if lot["qty_remaining"] < 1e-8:
                        buy_queue.pop(0)

                # If sell quantity remains after the buy_queue was exhausted,
                # this SELL has no matching BUY in this CSV. Preserve as orphan
                # — do NOT silently drop. (Defect A fix.)
                if sell_qty > 1e-8:
                    if tx["is_option"]:
                        orphan_qty_str = f"{int(sell_qty)} contract{'s' if sell_qty > 1 else ''}"
                    elif sell_qty == int(sell_qty):
                        orphan_qty_str = f"{int(sell_qty)} sh"
                    else:
                        orphan_qty_str = f"{sell_qty:.4f} sh"

                    orphan_proceeds = sell_qty * tx["amount_per_unit"]
                    orphan_key = (f"{symbol}-ORPHAN-{tx['date'].strftime('%Y%m%d')}-"
                                  f"{sell_qty:.4f}")
                    seen_keys[orphan_key] = seen_keys.get(orphan_key, 0) + 1
                    if seen_keys[orphan_key] > 1:
                        orphan_key = f"{orphan_key}-{seen_keys[orphan_key]}"

                    orphan_sells.append({
                        "ticker":        symbol,
                        "ticker_label":  tx["ticker_label"],
                        "buy_date":      None,
                        "sell_date":     tx["date"],
                        "qty":           sell_qty,
                        "qty_str":       orphan_qty_str,
                        "buy_price":     0.0,
                        "sell_price":    sell_price,
                        "buy_cost":      0.0,
                        "sell_proceeds": orphan_proceeds,
                        "commission":    0.0,
                        "pl_dollar":     0.0,
                        "pl_pct":        0.0,
                        "hold_days":     0,
                        "is_option":     tx["is_option"],
                        "description":   tx["description"],
                        "trade_key":     orphan_key,
                        "is_open":       False,
                        "is_orphan":     True,
                    })

        # Remaining buy lots = open positions
        for lot in buy_queue:
            if lot["qty_remaining"] > 1e-8:
                qty = lot["qty_remaining"]
                if lot["is_option"]:
                    qty_str = f"{int(qty)} contract{'s' if qty > 1 else ''}"
                elif qty == int(qty):
                    qty_str = f"{int(qty)} sh"
                else:
                    qty_str = f"{qty:.4f} sh"

                buy_cost = qty * lot["amount_per_unit"]
                base_key = f"{lot['symbol']}-{lot['date'].strftime('%Y%m%d')}-OPEN-{qty:.4f}"
                seen_keys[base_key] = seen_keys.get(base_key, 0) + 1
                trade_key = base_key if seen_keys[base_key] == 1 else f"{base_key}-{seen_keys[base_key]}"

                open_positions.append({
                    "ticker": lot["symbol"],
                    "ticker_label": lot["ticker_label"],
                    "buy_date": lot["date"],
                    "sell_date": None,
                    "qty": qty,
                    "qty_str": qty_str,
                    "buy_price": lot["price"],
                    "sell_price": 0.0,
                    "buy_cost": buy_cost,
                    "sell_proceeds": 0.0,
                    "commission": 0.0,
                    "pl_dollar": 0.0,
                    "pl_pct": 0.0,
                    "hold_days": (datetime.now() - lot["date"]).days,
                    "is_option": lot["is_option"],
                    "description": lot["description"],
                    "trade_key": trade_key,
                    "is_open": True,
                    "from_assignment": lot.get("from_assignment", False),
                })

    closed_legs.sort(key=lambda x: x["sell_date"])
    open_positions.sort(key=lambda x: x["buy_date"])
    orphan_sells.sort(key=lambda x: x["sell_date"])
    return closed_legs, open_positions, orphan_sells


def monthly_equivalent(total_return_frac, window_days):
    """Geometric monthly-equivalent of a total fractional return over window_days.
    Scale-invariant: the same underlying performance gives the same monthly figure
    regardless of window length. Falls back to linear if capital is more than wiped
    out (1 + r <= 0) to avoid a math domain error."""
    if window_days <= 0:
        return 0.0
    if 1.0 + total_return_frac > 0.0:
        return ((1.0 + total_return_frac) ** (30.0 / window_days) - 1.0) * 100.0
    return total_return_frac * (30.0 / window_days) * 100.0


def compute_time_weighted_return(legs, window_start, window_end):
    """
    Return (avg_capital_deployed, monthly_return_pct, turnover_x) for a list of
    closed legs over a window [window_start, window_end].

    The honest answer to "what % am I earning?" for a strategy that recycles
    capital. Summed buy-cost double-counts the same dollars across consecutive
    trades — this metric does not.

    Method:
      • For each leg, clip [buy_date, sell_date] to [window_start, window_end].
      • capital_days = Σ (buy_cost × days_held_INSIDE_window).  Floor at 1 day
        per leg so same-day trades still count.
      • avg_capital   = capital_days / window_days
      • monthly_pct   = ((1 + total_pl/avg_capital) ** (30/window_days) - 1) × 100
                       (geometric — compounding-aware; scale-invariant across windows so
                        the same underlying performance yields the same monthly figure
                        whether you look at 30d, 90d, or YTD)
      • turnover      = Σ buy_cost / avg_capital   (how many times $1 of deployed
                         capital was "spent" on new positions over the window)

    Returns (0.0, 0.0, 0.0) when there's nothing to measure.
    """
    if not legs or window_start is None or window_end is None or window_end <= window_start:
        return 0.0, 0.0, 0.0

    window_days = (window_end - window_start).days
    if window_days <= 0:
        return 0.0, 0.0, 0.0

    capital_days = 0.0
    total_cost = 0.0
    total_pl = 0.0
    for leg in legs:
        buy  = leg.get("buy_date")
        sell = leg.get("sell_date")
        if buy is None or sell is None:
            continue
        eff_start = max(buy,  window_start)
        eff_end   = min(sell, window_end)
        days_in_window = max((eff_end - eff_start).days, 1)
        capital_days += leg["buy_cost"] * days_in_window
        total_cost   += leg["buy_cost"]
        total_pl     += leg["pl_dollar"]

    if capital_days <= 0:
        return 0.0, 0.0, 0.0

    avg_capital = capital_days / window_days
    period_return_frac = total_pl / avg_capital
    # Guard the geometric formula against (1 + r) ≤ 0  (i.e. capital wiped out
    # and then some). Fall back to linear in that pathological case; never let
    # the app crash on a math domain error.
    if 1.0 + period_return_frac > 0.0:
        monthly_pct = ((1.0 + period_return_frac) ** (30.0 / window_days) - 1.0) * 100.0
    else:
        monthly_pct = period_return_frac * (30.0 / window_days) * 100.0
    turnover    = total_cost / avg_capital if avg_capital > 0 else 0.0
    return avg_capital, monthly_pct, turnover


# ─── Main Application ───────────────────────────────────────────

class TradeJournalApp:
    def __init__(self):
        if HAS_DND:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()

        self.root.title("Trade Journal — Fidelity Tracker v2")
        self.root.geometry("1440x900")
        self.root.configure(bg=BG)
        self.root.minsize(1100, 700)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # State
        self.all_legs = []          # All closed legs from CSV
        self.open_positions = []    # Open (unmatched) positions
        self.orphan_sells = []      # SELLs with no matching BUY (defect-A surfaced, not silenced)
        self.filtered_legs = []     # After date + method filter
        self.tags = load_tags()
        self.is_grouped = True
        self.date_filter = "all"
        self.custom_from = None
        self.custom_to = None
        self.chart_mode = "closed"  # "closed" or "all"

        # Window bounds for time-weighted return calc (set by _apply_filters)
        self.window_start = None
        self.window_end = None

        # Treeview sort state
        self.sort_col = None
        self.sort_rev = False

        # Combo popup tracker
        self._active_combo = None

        self._build_ui()
        init_db()

        # Auto-load last CSV
        self.root.after(200, self._auto_load_last_csv)

    def _auto_load_last_csv(self):
        cfg = load_config()
        last = cfg.get("last_csv_path")
        if last and os.path.isfile(last):
            self._load_csv(last)

    # ── UI Construction ──────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_main()
        self._build_footer()

    def _build_header(self):
        header = tk.Frame(self.root, bg=PANEL, pady=10, padx=20)
        header.pack(fill="x")

        tk.Label(header, text="Trade Journal — Fidelity Tracker",
                 font=("Helvetica", 18, "bold"), bg=PANEL, fg=WHITE).pack(side="left")

        # Date filter buttons (right side)
        date_frame = tk.Frame(header, bg=PANEL)
        date_frame.pack(side="right")

        self.date_buttons = {}
        for label, key in [("30d", "30"), ("90d", "90"), ("YTD", "ytd"), ("All", "all")]:
            btn = tk.Button(date_frame, text=label, font=("Helvetica", 11),
                            bg=BORDER, fg=TEXT, bd=0, padx=12, pady=4,
                            activebackground=GREEN, activeforeground=BG,
                            command=lambda k=key: self._set_date_filter(k))
            btn.pack(side="left", padx=2)
            self.date_buttons[key] = btn

        # Dropdown calendar date pickers
        tk.Label(date_frame, text="  From:", bg=PANEL, fg=DIM,
                 font=("Helvetica", 10)).pack(side="left")

        if HAS_CALENDAR:
            self.from_cal = DateEntry(date_frame, width=10,
                                       background=BORDER, foreground=TEXT,
                                       headersbackground=HEADER, headersforeground=TEXT,
                                       selectbackground=GREEN, selectforeground=BG,
                                       normalbackground=PANEL, normalforeground=TEXT,
                                       weekendbackground=PANEL, weekendforeground=TEXT,
                                       borderwidth=0, font=("Helvetica", 10),
                                       date_pattern="yyyy-mm-dd",
                                       state="readonly")
            self.from_cal.pack(side="left", padx=2)

            tk.Label(date_frame, text="To:", bg=PANEL, fg=DIM,
                     font=("Helvetica", 10)).pack(side="left")

            self.to_cal = DateEntry(date_frame, width=10,
                                     background=BORDER, foreground=TEXT,
                                     headersbackground=HEADER, headersforeground=TEXT,
                                     selectbackground=GREEN, selectforeground=BG,
                                     normalbackground=PANEL, normalforeground=TEXT,
                                     weekendbackground=PANEL, weekendforeground=TEXT,
                                     borderwidth=0, font=("Helvetica", 10),
                                     date_pattern="yyyy-mm-dd",
                                     state="readonly")
            self.to_cal.pack(side="left", padx=2)
        else:
            # Fallback text entries if tkcalendar not installed
            self.from_cal = tk.Entry(date_frame, width=10, bg=BORDER, fg=TEXT,
                                      insertbackground=TEXT, bd=0, font=("Helvetica", 10))
            self.from_cal.pack(side="left", padx=2)
            self.from_cal.insert(0, "YYYY-MM-DD")

            tk.Label(date_frame, text="To:", bg=PANEL, fg=DIM,
                     font=("Helvetica", 10)).pack(side="left")

            self.to_cal = tk.Entry(date_frame, width=10, bg=BORDER, fg=TEXT,
                                    insertbackground=TEXT, bd=0, font=("Helvetica", 10))
            self.to_cal.pack(side="left", padx=2)
            self.to_cal.insert(0, "YYYY-MM-DD")

        tk.Button(date_frame, text="Apply", font=("Helvetica", 10, "bold"),
                  bg=GREEN, fg=BG, bd=0, padx=10, pady=3,
                  command=self._apply_custom_date).pack(side="left", padx=4)

        self._highlight_date_btn("all")

        # Drop zone
        drop_frame = tk.Frame(self.root, bg=BG, pady=6, padx=20)
        drop_frame.pack(fill="x")

        self.drop_label = tk.Label(
            drop_frame,
            text="📂  Drag & Drop Fidelity CSV here  —  or click to browse",
            font=("Helvetica", 13), bg=BG, fg=GREEN,
            relief="groove", bd=2, pady=16, padx=20,
            cursor="hand2"
        )
        self.drop_label.pack(fill="x")
        self.drop_label.bind("<Button-1>", lambda e: self._browse_csv())

        # Drag-and-drop binding
        if HAS_DND:
            try:
                self.drop_label.drop_target_register(DND_FILES)
                self.drop_label.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass  # DND init failure — file dialog still works

    def _build_main(self):
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Left panel: Trades table (~62%) ──
        left = tk.Frame(main, bg=PANEL)
        left.place(relx=0, rely=0, relwidth=0.62, relheight=1.0)

        # Table toolbar
        toolbar = tk.Frame(left, bg=HEADER, pady=8, padx=14)
        toolbar.pack(fill="x")

        self.trade_count_label = tk.Label(
            toolbar, text="CLOSED TRADES (0)",
            font=("Helvetica", 11, "bold"), bg=HEADER, fg=TEXT
        )
        self.trade_count_label.pack(side="left")

        # Group toggle
        self.group_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text="Group by Ticker", variable=self.group_var,
                        bg=HEADER, fg=TEXT, selectcolor=BORDER, activebackground=HEADER,
                        activeforeground=TEXT, font=("Helvetica", 10),
                        command=self._refresh_table).pack(side="left", padx=20)

        tk.Button(toolbar, text="Export CSV", font=("Helvetica", 10),
                  bg=BORDER, fg=TEXT, bd=0, padx=10, pady=2,
                  command=self._export_csv).pack(side="right")

        tk.Button(toolbar, text="Export Open", font=("Helvetica", 10),
                  bg=BORDER, fg=TEXT, bd=0, padx=10, pady=2,
                  command=self._export_open_positions).pack(side="right", padx=(0, 6))

        # Treeview
        tree_frame = tk.Frame(left, bg=PANEL)
        tree_frame.pack(fill="both", expand=True, padx=0)

        columns = ("ticker", "buy_date", "sell_date", "amount", "hold",
                    "pl_dollar", "pl_pct", "method")
        col_headers = ("Ticker", "Buy Date", "Sell Date", "Amount",
                       "Hold", "$ P/L", "% P/L", "Strategy")
        col_widths = (80, 95, 95, 100, 60, 85, 70, 80)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Trade.Treeview",
                         background=PANEL, foreground=TEXT, fieldbackground=PANEL,
                         borderwidth=0, font=("Helvetica", 11), rowheight=30)
        style.configure("Trade.Treeview.Heading",
                         background=HEADER, foreground=DIM,
                         font=("Helvetica", 10, "bold"), borderwidth=0)
        style.map("Trade.Treeview",
                   background=[("selected", BORDER)],
                   foreground=[("selected", WHITE)])

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                  style="Trade.Treeview", selectmode="browse")
        for col, hdr, w in zip(columns, col_headers, col_widths):
            self.tree.heading(col, text=hdr,
                              command=lambda c=col: self._sort_by_column(c))
            anchor = "e" if col in ("pl_dollar", "pl_pct", "hold") else "w"
            self.tree.column(col, width=w, minwidth=50, anchor=anchor)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        # Tag colors
        self.tree.tag_configure("pos", foreground=GREEN)
        self.tree.tag_configure("neg", foreground=RED)
        self.tree.tag_configure("excluded", foreground=EXCL_DIM)
        self.tree.tag_configure("open_pos", foreground=ORANGE)
        self.tree.tag_configure("orphan", foreground=ORPHAN)
        self.tree.tag_configure("group_header", background="#252b3d",
                                 font=("Helvetica", 11, "bold"))

        # Single-click on method column → show dropdown
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)

        # ── Right panel: Summary + Charts (~38%) ──
        right_outer = tk.Frame(main, bg=PANEL)
        right_outer.place(relx=0.62, rely=0, relwidth=0.38, relheight=1.0)

        right_canvas = tk.Canvas(right_outer, bg=PANEL, highlightthickness=0)
        right_sb = ttk.Scrollbar(right_outer, orient="vertical", command=right_canvas.yview)
        self.right_panel = tk.Frame(right_canvas, bg=PANEL)

        self.right_panel.bind("<Configure>",
            lambda e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.create_window((0, 0), window=self.right_panel, anchor="nw")
        right_canvas.configure(yscrollcommand=right_sb.set)

        right_sb.pack(side="right", fill="y")
        right_canvas.pack(fill="both", expand=True)

        right_canvas.bind_all("<Button-4>", lambda e: right_canvas.yview_scroll(-3, "units"))
        right_canvas.bind_all("<Button-5>", lambda e: right_canvas.yview_scroll(3, "units"))

        self._build_summary_cards()
        self._build_chart_slots()

    def _build_summary_cards(self):
        # ── Swing allocation input (Option 1): the 3% target is measured against
        # this declared sleeve size, not deployed capital. Editable + persisted. ──
        alloc_frame = tk.Frame(self.right_panel, bg=PANEL, padx=12, pady=8)
        alloc_frame.pack(fill="x", pady=(8, 0))
        tk.Label(alloc_frame, text="Swing program capital  $",
                 bg=PANEL, fg=DIM, font=("Helvetica", 9)).pack(side="left")
        self.alloc_var = tk.StringVar(value=f"{get_swing_allocation():,.0f}")
        alloc_entry = tk.Entry(alloc_frame, textvariable=self.alloc_var, width=10,
                               bg=BORDER, fg=TEXT, insertbackground=TEXT, bd=0,
                               font=("Helvetica", 9, "bold"))
        alloc_entry.pack(side="left", padx=(2, 6))
        tk.Label(alloc_frame, text="← 3% target measured against this",
                 bg=PANEL, fg=DIM, font=("Helvetica", 8)).pack(side="left")

        def _on_alloc_change(event=None):
            raw = self.alloc_var.get().replace(",", "").replace("$", "").strip()
            try:
                v = float(raw)
            except ValueError:
                return
            if v > 0:
                set_swing_allocation(v)
                self.alloc_var.set(f"{v:,.0f}")
                self._refresh_summary()
        alloc_entry.bind("<Return>", _on_alloc_change)
        alloc_entry.bind("<FocusOut>", _on_alloc_change)

        grid = tk.Frame(self.right_panel, bg=PANEL, padx=12, pady=12)
        grid.pack(fill="x")

        self.cards = {}
        card_defs = [
            ("total_pl", "TOTAL P/L"),
            ("win_rate", "WIN RATE"),
            ("method_swing", "SWING TRADER"),
            ("method_leap", "LEAP STRATEGY"),
        ]

        for i, (key, label) in enumerate(card_defs):
            r, c = divmod(i, 2)
            card = tk.Frame(grid, bg=HEADER, padx=14, pady=12)
            card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            grid.columnconfigure(c, weight=1)

            tk.Label(card, text=label, font=("Helvetica", 10),
                     bg=HEADER, fg=DIM).pack(anchor="w")
            val_label = tk.Label(card, text="—", font=("Helvetica", 20, "bold"),
                                  bg=HEADER, fg=TEXT)
            val_label.pack(anchor="w")
            sub_label = tk.Label(card, text="", font=("Helvetica", 9),
                                  bg=HEADER, fg=DIM)
            sub_label.pack(anchor="w")
            self.cards[key] = (val_label, sub_label)

            # Capital-efficiency sub-block — only on the two strategy cards.
            # Shows avg deployed capital + turnover, and monthly-equivalent return
            # on deployed capital (the honest "% per month" figure, not the
            # double-counted sum-of-cost %). Swing card also gets a ✓/✗ badge
            # against the SWING_MONTHLY_TARGET_PCT benchmark.
            if key == "method_swing":
                self.swing_deployed_label = tk.Label(
                    card, text="", font=("Helvetica", 9), bg=HEADER, fg=DIM)
                self.swing_deployed_label.pack(anchor="w", pady=(6, 0))
                self.swing_monthly_label = tk.Label(
                    card, text="", font=("Helvetica", 10, "bold"), bg=HEADER, fg=TEXT)
                self.swing_monthly_label.pack(anchor="w")
            elif key == "method_leap":
                self.leap_deployed_label = tk.Label(
                    card, text="", font=("Helvetica", 9), bg=HEADER, fg=DIM)
                self.leap_deployed_label.pack(anchor="w", pady=(6, 0))
                self.leap_monthly_label = tk.Label(
                    card, text="", font=("Helvetica", 10, "bold"), bg=HEADER, fg=TEXT)
                self.leap_monthly_label.pack(anchor="w")

    def _build_chart_slots(self):
        # Chart mode toggle
        toggle_frame = tk.Frame(self.right_panel, bg=PANEL, padx=12, pady=8)
        toggle_frame.pack(fill="x")

        tk.Label(toggle_frame, text="Charts:", font=("Helvetica", 10, "bold"),
                 bg=PANEL, fg=DIM).pack(side="left")

        self.chart_mode_var = tk.StringVar(value="closed")
        self.btn_closed = tk.Button(toggle_frame, text="Closed Only",
                                     font=("Helvetica", 10), bg=GREEN, fg=BG,
                                     bd=0, padx=10, pady=3,
                                     command=lambda: self._set_chart_mode("closed"))
        self.btn_closed.pack(side="left", padx=(8, 2))

        self.btn_all = tk.Button(toggle_frame, text="All (incl Open)",
                                  font=("Helvetica", 10), bg=BORDER, fg=TEXT,
                                  bd=0, padx=10, pady=3,
                                  command=lambda: self._set_chart_mode("all"))
        self.btn_all.pack(side="left", padx=2)

        self.fig = Figure(figsize=(5, 17), dpi=90, facecolor=PANEL)
        self.fig.subplots_adjust(hspace=0.5, left=0.15, right=0.92, top=0.97, bottom=0.03)

        self.ax_pie   = self.fig.add_subplot(5, 1, 1)
        self.ax_bar   = self.fig.add_subplot(5, 1, 2)
        self.ax_daily = self.fig.add_subplot(5, 1, 3)
        self.ax_wr    = self.fig.add_subplot(5, 1, 4)
        self.ax_cum   = self.fig.add_subplot(5, 1, 5)

        for ax in [self.ax_pie, self.ax_bar, self.ax_daily, self.ax_wr, self.ax_cum]:
            ax.set_facecolor(HEADER)
            ax.tick_params(colors=DIM, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(BORDER)

        chart_frame = tk.Frame(self.right_panel, bg=PANEL, padx=12)
        chart_frame.pack(fill="x", pady=(0, 12))

        self.chart_canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.chart_canvas.get_tk_widget().pack(fill="x")
        self.chart_canvas.get_tk_widget().bind("<Double-Button-1>", lambda e: self._expand_bar_chart())

    def _set_chart_mode(self, mode):
        self.chart_mode = mode
        if mode == "closed":
            self.btn_closed.configure(bg=GREEN, fg=BG)
            self.btn_all.configure(bg=BORDER, fg=TEXT)
        else:
            self.btn_closed.configure(bg=BORDER, fg=TEXT)
            self.btn_all.configure(bg=GREEN, fg=BG)
        self._refresh_charts()

    def _build_footer(self):
        footer = tk.Frame(self.root, bg=PANEL, pady=6, padx=20)
        footer.pack(fill="x", side="bottom")
        tk.Label(footer, text="Click Strategy column to change  •  Tags saved to SQLite  •  FIFO matching  •  Excluded trades skip calculations",
                 font=("Helvetica", 9), bg=PANEL, fg=DIM).pack(side="left")
        self.status_label = tk.Label(footer, text="No CSV loaded",
                                      font=("Helvetica", 9), bg=PANEL, fg=DIM)
        self.status_label.pack(side="right")

    # ── Data Loading ─────────────────────────────────────────────

    def _browse_csv(self):
        cfg = load_config()
        initial_dir = os.path.dirname(cfg.get("last_csv_path", "")) or None
        path = filedialog.askopenfilename(
            title="Select Fidelity CSV",
            initialdir=initial_dir,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self._load_csv(path)

    def _on_drop(self, event):
        path = event.data.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        # Handle multiple files — take first CSV
        if " " in path and not os.path.exists(path):
            for p in path.split():
                p = p.strip("{}")
                if p.lower().endswith(".csv") and os.path.exists(p):
                    path = p
                    break
        if path.lower().endswith(".csv"):
            self._load_csv(path)
        else:
            messagebox.showwarning("Invalid File", "Please drop a .csv file.")

    def _load_csv(self, filepath):
        try:
            closed, opens, orphans = parse_fidelity_csv(filepath)
            if not closed and not opens and not orphans:
                messagebox.showinfo("No Trades", "No trades or positions found in this CSV.")
                return

            self.all_legs = closed
            self.open_positions = opens
            self.orphan_sells = orphans

            # Apply saved tags (orphans included — they can be tagged but skip P/L).
            # Untagged legs use default_method_for(), which keeps options/assignment
            # byproducts out of the Swing bucket. Count auto-routed legs to surface
            # them (fail loud) instead of silently reclassifying.
            auto_leap = auto_excl = 0
            for leg in self.all_legs + self.open_positions + self.orphan_sells:
                saved = self.tags.get(leg["trade_key"])
                if saved:
                    if saved in LEGACY_METHOD_MAP:
                        saved = LEGACY_METHOD_MAP[saved]
                        save_tag(leg["trade_key"], saved)
                    leg["method"] = saved
                else:
                    dm = default_method_for(leg)
                    leg["method"] = dm
                    if dm == "LEAP Strategy":
                        auto_leap += 1
                    elif dm == "Excluded":
                        auto_excl += 1

            fname = os.path.basename(filepath)
            parts = []
            if closed:
                parts.append(f"{len(closed)} closed")
            if opens:
                parts.append(f"{len(opens)} open")
            if orphans:
                parts.append(f"⚠ {len(orphans)} orphan sell{'s' if len(orphans) != 1 else ''}")
            if not parts:
                parts.append(f"{len(opens)} positions (snapshot)")
            summary = ", ".join(parts)

            # Fail loud: note any legs auto-routed out of Swing by default rules
            auto_bits = []
            if auto_leap:
                auto_bits.append(f"{auto_leap} option→LEAP")
            if auto_excl:
                auto_bits.append(f"{auto_excl} assigned→Excl")
            auto_note = f"  [auto: {', '.join(auto_bits)}]" if auto_bits else ""

            self.drop_label.configure(text=f"✅  {fname}  —  {summary}{auto_note}")
            self.status_label.configure(text=f"Loaded: {fname} ({summary}){auto_note}")

            # Auto-refresh the swing export at the stable path (silent, non-fatal)
            self._auto_export_open_positions()

            # Save last path
            cfg = load_config()
            cfg["last_csv_path"] = os.path.abspath(filepath)
            save_config(cfg)

            self._apply_filters()

        except Exception as e:
            messagebox.showerror("CSV Error", f"Failed to parse CSV:\n\n{e}")

    # ── Filtering ────────────────────────────────────────────────

    def _set_date_filter(self, key):
        self.date_filter = key
        self._highlight_date_btn(key)
        self._apply_filters()

    def _highlight_date_btn(self, active_key):
        for key, btn in self.date_buttons.items():
            if key == active_key:
                btn.configure(bg=GREEN, fg=BG)
            else:
                btn.configure(bg=BORDER, fg=TEXT)

    def _apply_custom_date(self):
        try:
            if HAS_CALENDAR:
                f = datetime.combine(self.from_cal.get_date(), datetime.min.time())
                t = datetime.combine(self.to_cal.get_date(), datetime.min.time())
            else:
                f = datetime.strptime(self.from_cal.get().strip(), "%Y-%m-%d")
                t = datetime.strptime(self.to_cal.get().strip(), "%Y-%m-%d")
            self.custom_from = f
            self.custom_to = t
            self.date_filter = "custom"
            self._highlight_date_btn(None)
            self._apply_filters()
        except (ValueError, AttributeError):
            messagebox.showwarning("Date Format", "Select valid dates from the calendar.")

    def _apply_filters(self):
        now = datetime.now()

        if self.date_filter == "30":
            cutoff = now - timedelta(days=30)
            self.filtered_legs = [l for l in self.all_legs if l["sell_date"] >= cutoff]
            self.window_start, self.window_end = cutoff, now
        elif self.date_filter == "90":
            cutoff = now - timedelta(days=90)
            self.filtered_legs = [l for l in self.all_legs if l["sell_date"] >= cutoff]
            self.window_start, self.window_end = cutoff, now
        elif self.date_filter == "ytd":
            jan1 = datetime(now.year, 1, 1)
            self.filtered_legs = [l for l in self.all_legs if l["sell_date"] >= jan1]
            self.window_start, self.window_end = jan1, now
        elif self.date_filter == "custom" and self.custom_from and self.custom_to:
            self.filtered_legs = [
                l for l in self.all_legs
                if self.custom_from <= l["sell_date"] <= self.custom_to
            ]
            self.window_start, self.window_end = self.custom_from, self.custom_to
        else:
            self.filtered_legs = list(self.all_legs)
            # "All" — window spans from the earliest trade activity to now.
            # Use the earlier of (first buy_date, first sell_date) so capital-days
            # accounting starts when money first went out, not when it first came back.
            if self.filtered_legs:
                first_sell = min(l["sell_date"] for l in self.filtered_legs)
                first_buys = [l["buy_date"] for l in self.filtered_legs if l.get("buy_date")]
                first_buy = min(first_buys) if first_buys else first_sell
                self.window_start = min(first_buy, first_sell)
                self.window_end = now
            else:
                self.window_start, self.window_end = None, None

        self._refresh_table()
        self._refresh_summary()
        self._refresh_charts()

    # ── Table ────────────────────────────────────────────────────

    def _refresh_table(self):
        self._destroy_active_combo()
        self.is_grouped = self.group_var.get()
        self.tree.delete(*self.tree.get_children())

        legs = self.filtered_legs
        opens = self.open_positions
        orphans = self.orphan_sells

        count_text = f"CLOSED TRADES ({len(legs)})"
        if opens:
            count_text += f"  •  OPEN POSITIONS ({len(opens)})"
        if orphans:
            count_text += f"  •  ⚠ ORPHAN SELLS ({len(orphans)})"
        self.trade_count_label.configure(text=count_text)

        # ── Closed trades ──
        if self.is_grouped and legs:
            groups = defaultdict(list)
            for leg in legs:
                groups[leg["ticker"]].append(leg)

            for ticker in sorted(groups.keys()):
                group = groups[ticker]
                active = [l for l in group if l.get("method") != "Excluded"]
                total_pl = sum(l["pl_dollar"] for l in active)

                self.tree.insert("", "end", values=(
                    f"▼ {ticker} ({len(group)} legs)", "", "", "",
                    "", f"${total_pl:+.2f}", "", ""
                ), tags=("group_header",))

                for leg in sorted(group, key=lambda x: x["buy_date"] or datetime.min):
                    self._insert_leg_row(leg)
        else:
            for leg in legs:
                self._insert_leg_row(leg)

        # ── Open positions separator + rows ──
        if opens:
            self.tree.insert("", "end", values=(
                f"── OPEN POSITIONS ({len(opens)}) ──", "", "", "",
                "", "", "", ""
            ), tags=("group_header",))
            for leg in opens:
                self._insert_leg_row(leg)

        # ── Orphan sells separator + rows ──
        # Sells with no matching buy in this CSV. Surfaced, not dropped.
        # Excluded from all P/L aggregates.
        if orphans:
            self.tree.insert("", "end", values=(
                f"── ⚠ ORPHAN SELLS ({len(orphans)}) — missing buy lots ──",
                "", "", "", "", "", "", ""
            ), tags=("group_header",))
            for leg in orphans:
                self._insert_leg_row(leg)

    def _insert_leg_row(self, leg):
        method = leg.get("method", "Swing Trader")
        short = METHOD_SHORT.get(method, method)

        is_orphan = leg.get("is_orphan", False)
        is_open = leg.get("is_open", False)

        if is_orphan:
            tag = "orphan"
        elif method == "Excluded":
            tag = "excluded"
        elif leg["pl_dollar"] >= 0:
            tag = "pos"
        else:
            tag = "neg"

        if is_open:
            tag = "open_pos"

        sell_str = leg["sell_date"].strftime("%Y-%m-%d") if leg["sell_date"] else "OPEN"
        if is_orphan:
            buy_str = "MISSING"
        else:
            buy_str = leg["buy_date"].strftime("%Y-%m-%d") if leg["buy_date"] else "—"

        if is_orphan:
            pl_col = f"${leg['sell_proceeds']:,.2f} proc"
            pct_col = "n/a"
            hold_col = "—"
        elif is_open:
            pl_col = "—"
            pct_col = "—"
            hold_col = f"{leg['hold_days']}d"
        else:
            pl_col = f"${leg['pl_dollar']:+.2f}"
            pct_col = f"{leg['pl_pct']:+.1f}%"
            hold_col = f"{leg['hold_days']}d"

        self.tree.insert("", "end", iid=leg["trade_key"], values=(
            leg["ticker"],
            buy_str,
            sell_str,
            leg["qty_str"],
            hold_col,
            pl_col,
            pct_col,
            short,
        ), tags=(tag,))

    # ── Strategy Dropdown on Click ───────────────────────────────

    def _destroy_active_combo(self):
        if self._active_combo:
            try:
                self._active_combo.destroy()
            except Exception:
                pass
            self._active_combo = None

    def _on_tree_click(self, event):
        """Show dropdown when clicking the Strategy column."""
        self._destroy_active_combo()

        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        col = self.tree.identify_column(event.x)
        if col != "#8":
            return

        item = self.tree.identify_row(event.y)
        if not item:
            return

        values = self.tree.item(item, "values")
        if not values:
            return
        first = str(values[0])
        if first.startswith("▼") or first.startswith("──"):
            return

        leg = self._find_leg_by_key(item)
        if not leg:
            return

        current_short = values[7]

        bbox = self.tree.bbox(item, column="method")
        if not bbox:
            return

        x, y, w, h = bbox

        var = tk.StringVar(value=current_short)
        combo = ttk.Combobox(self.tree, textvariable=var,
                              values=["Swing", "LEAP", "Excl"],
                              state="readonly", width=8, font=("Helvetica", 10))
        combo.place(x=x, y=y, width=w, height=h)
        combo.focus_set()

        self._active_combo = combo

        def on_var_change(*args):
            new_short = var.get()
            if new_short == current_short:
                return
            new_method = METHOD_FROM_SHORT.get(new_short, "Swing Trader")
            leg["method"] = new_method
            save_tag(leg["trade_key"], new_method)
            self.tags[leg["trade_key"]] = new_method
            for al in self.all_legs + self.open_positions + self.orphan_sells:
                if al["trade_key"] == leg["trade_key"]:
                    al["method"] = new_method
            self._destroy_active_combo()
            self._refresh_table()
            self._refresh_summary()
            self._refresh_charts()

        var.trace_add("write", on_var_change)
        combo.bind("<Escape>", lambda e: self._destroy_active_combo())

    def _find_leg_by_key(self, trade_key):
        """Find a leg by its trade_key (which is also the treeview iid)."""
        for leg in self.filtered_legs + self.open_positions + self.orphan_sells:
            if leg["trade_key"] == trade_key:
                return leg
        return None

    def _sort_by_column(self, col):
        if self.sort_col == col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col = col
            self.sort_rev = False

        col_map = {
            "ticker": "ticker", "buy_date": "buy_date", "sell_date": "sell_date",
            "amount": "qty", "hold": "hold_days", "pl_dollar": "pl_dollar",
            "pl_pct": "pl_pct", "method": "method"
        }
        key_field = col_map.get(col, col)

        try:
            self.filtered_legs.sort(key=lambda l: l.get(key_field, ""),
                                     reverse=self.sort_rev)
        except TypeError:
            pass
        self._refresh_table()

    # ── Summary Cards ────────────────────────────────────────────

    def _refresh_summary(self):
        # Only count non-excluded closed trades
        legs = [l for l in self.filtered_legs
                if l.get("method") != "Excluded" and not l.get("is_open", False)]

        if not legs:
            for key in self.cards:
                self.cards[key][0].configure(text="—", fg=TEXT)
                self.cards[key][1].configure(text="")
            # Capital-efficiency labels live outside self.cards — clear them too.
            for lbl in (self.swing_deployed_label, self.swing_monthly_label,
                        self.leap_deployed_label,  self.leap_monthly_label):
                lbl.configure(text="")
            return

        total_pl = sum(l["pl_dollar"] for l in legs)
        total_cost = sum(l["buy_cost"] for l in legs)
        total_pct = (total_pl / total_cost * 100) if total_cost > 0 else 0
        wins = sum(1 for l in legs if l["pl_dollar"] > 0)
        win_rate = (wins / len(legs) * 100) if legs else 0

        method_pl = defaultdict(float)
        method_count = defaultdict(int)
        method_wins = defaultdict(int)
        for l in legs:
            m = l.get("method", "Swing Trader")
            method_pl[m] += l["pl_dollar"]
            method_count[m] += 1
            if l["pl_dollar"] > 0:
                method_wins[m] += 1

        # Total P/L card — now shows % gain prominently
        color = GREEN if total_pl >= 0 else RED
        self.cards["total_pl"][0].configure(
            text=f"{'+'if total_pl>=0 else ''}${total_pl:,.2f}  ({total_pct:+.2f}%)", fg=color)
        self.cards["total_pl"][1].configure(
            text=f"{len(legs)} trades on ${total_cost:,.0f} cost basis")

        # Win Rate
        self.cards["win_rate"][0].configure(text=f"{win_rate:.0f}%", fg=GREEN)
        self.cards["win_rate"][1].configure(text=f"{wins}W / {len(legs)-wins}L")

        # Swing Trader
        sw_pl = method_pl.get("Swing Trader", 0)
        sw_color = GREEN if sw_pl >= 0 else RED
        sw_count = method_count.get("Swing Trader", 0)
        sw_wr = (method_wins.get("Swing Trader", 0) / sw_count * 100) if sw_count > 0 else 0
        self.cards["method_swing"][0].configure(text=f"${sw_pl:+,.2f}", fg=sw_color)
        self.cards["method_swing"][1].configure(text=f"{sw_count} trades • {sw_wr:.0f}% win rate")

        # LEAP Strategy
        lp_pl = method_pl.get("LEAP Strategy", 0)
        lp_color = GREEN if lp_pl >= 0 else RED
        lp_count = method_count.get("LEAP Strategy", 0)
        lp_wr = (method_wins.get("LEAP Strategy", 0) / lp_count * 100) if lp_count > 0 else 0
        self.cards["method_leap"][0].configure(text=f"${lp_pl:+,.2f}", fg=lp_color)
        self.cards["method_leap"][1].configure(text=f"{lp_count} trades • {lp_wr:.0f}% win rate")

        # ── Capital efficiency + return display ──
        # Swing card (Layout A): headline = monthly return on CAPITAL AT RISK
        #   (deployed) with the 3% badge — the 3% target is a trading-quality bar
        #   (return per dollar at risk). Context line shows the allocated-sleeve
        #   return + deployed avg + turnover, so the idle-cash drag stays visible.
        # LEAP card: deployed-based monthly, no allocation, no badge (per spec).
        # Window comes from _apply_filters via self.window_start/window_end.
        def _set_eff(deployed_lbl, monthly_lbl, method_legs, allocation=None):
            if self.window_start is None or self.window_end is None or not method_legs:
                deployed_lbl.configure(text="")
                monthly_lbl.configure(text="")
                return
            avg_cap, mo_dep, turnover = compute_time_weighted_return(
                method_legs, self.window_start, self.window_end)
            if avg_cap <= 0:
                deployed_lbl.configure(text="")
                monthly_lbl.configure(text="")
                return

            if allocation is not None and allocation > 0:
                # 3% target measures TRADING QUALITY = return on capital at risk
                # (deployed). Headline (bold) carries the deployed monthly return +
                # badge. Context line keeps the allocated-sleeve return beside it so
                # the idle-cash drag stays visible — both numbers shown, none hidden.
                pl = sum(l["pl_dollar"] for l in method_legs)
                wd = (self.window_end - self.window_start).days
                mo_alloc = monthly_equivalent(pl / allocation, wd)

                # Headline: deployed return (capital at risk) with the 3% badge
                if mo_dep >= SWING_MONTHLY_TARGET_PCT:
                    msg = (f"{mo_dep:+.2f}%/mo on capital at risk  "
                           f"✓ ≥ {SWING_MONTHLY_TARGET_PCT:g}%")
                    color = GREEN
                elif mo_dep >= 0:
                    msg = (f"{mo_dep:+.2f}%/mo on capital at risk  "
                           f"✗ below {SWING_MONTHLY_TARGET_PCT:g}%")
                    color = ORANGE
                else:
                    msg = f"{mo_dep:+.2f}%/mo on capital at risk  ✗ losing"
                    color = RED
                monthly_lbl.configure(text=msg, fg=color)

                # Context: deployed avg • turnover  ·  sleeve return (cash-drag reality)
                deployed_lbl.configure(
                    text=f"${avg_cap:,.0f} deployed · {turnover:.1f}× turn  ·  "
                         f"${allocation:,.0f} sleeve → {mo_alloc:+.2f}%/mo")
            else:
                # LEAP — deployed-based, unchanged
                deployed_lbl.configure(
                    text=f"Deployed: ${avg_cap:,.0f}  •  {turnover:.1f}× turnover")
                msg = f"{mo_dep:+.2f}%/mo deployed"
                color = GREEN if mo_dep >= 0 else RED
                monthly_lbl.configure(text=msg, fg=color)

        swing_legs = [l for l in legs if l.get("method") == "Swing Trader"]
        leap_legs  = [l for l in legs if l.get("method") == "LEAP Strategy"]
        _set_eff(self.swing_deployed_label, self.swing_monthly_label,
                 swing_legs, allocation=get_swing_allocation())
        _set_eff(self.leap_deployed_label,  self.leap_monthly_label,
                 leap_legs,  allocation=None)

    # ── Charts ───────────────────────────────────────────────────

    def _refresh_charts(self):
        # Determine which legs to chart
        if self.chart_mode == "all":
            chart_legs = [l for l in self.filtered_legs
                          if l.get("method") != "Excluded"]
            # Add open positions too
            chart_legs += [l for l in self.open_positions
                           if l.get("method") != "Excluded"]
        else:
            chart_legs = [l for l in self.filtered_legs
                          if l.get("method") != "Excluded" and not l.get("is_open", False)]

        for ax in [self.ax_pie, self.ax_bar, self.ax_daily, self.ax_wr, self.ax_cum]:
            ax.clear()
            ax.set_facecolor(HEADER)

        if not chart_legs:
            self.chart_canvas.draw()
            return

        # ── 1. Pie: P/L by Strategy ──
        method_pl = defaultdict(float)
        for l in chart_legs:
            method_pl[l.get("method", "Swing Trader")] += l["pl_dollar"]

        labels = [m for m in ["Swing Trader", "LEAP Strategy"] if m in method_pl]
        sizes = [abs(method_pl[m]) for m in labels]
        colors = [METHOD_COLORS.get(m, DIM) for m in labels]
        short_labels = [METHOD_SHORT.get(m, m) for m in labels]

        if sum(sizes) > 0:
            self.ax_pie.pie(sizes, labels=short_labels, colors=colors, autopct="%1.0f%%",
                            textprops={"color": TEXT, "fontsize": 9},
                            startangle=90, pctdistance=0.75)
            title = "P/L by Strategy"
            if self.chart_mode == "all":
                title += " (incl open)"
            self.ax_pie.set_title(title, color=TEXT, fontsize=10, pad=8)
        else:
            self.ax_pie.text(0.5, 0.5, "No data", ha="center", va="center",
                             color=DIM, fontsize=11, transform=self.ax_pie.transAxes)

        # ── 2. Stacked Bar: Monthly P/L ──
        monthly = defaultdict(lambda: defaultdict(float))
        for l in chart_legs:
            if l.get("is_open"):
                if l["buy_date"] is None:
                    continue   # positions snapshot — no date to bucket
                month_key = l["buy_date"].strftime("%Y-%m")
            else:
                month_key = l["sell_date"].strftime("%Y-%m")
            monthly[month_key][l.get("method", "Swing Trader")] += l["pl_dollar"]

        months = sorted(monthly.keys())
        if months:
            sw_vals = [monthly[m].get("Swing Trader", 0) for m in months]
            lp_vals = [monthly[m].get("LEAP Strategy", 0) for m in months]
            x = range(len(months))

            self.ax_bar.bar(x, sw_vals, label="Swing", color=GREEN, alpha=0.9)
            self.ax_bar.bar(x, lp_vals, bottom=sw_vals, label="LEAP", color=BLUE, alpha=0.9)

            self.ax_bar.set_xticks(list(x))
            self.ax_bar.set_xticklabels(months, rotation=45, ha="right", fontsize=8)
            self.ax_bar.legend(fontsize=8, facecolor=HEADER, edgecolor=BORDER, labelcolor=TEXT)
            self.ax_bar.set_title("Monthly P/L by Strategy", color=TEXT, fontsize=10, pad=8)
            self.ax_bar.axhline(y=0, color=DIM, linewidth=0.5)

        # ── 3. Daily / Weekly P/L bar chart ──
        # Bucket closed legs by sell date. Adaptive: daily when ≤60 unique trade
        # days, weekly otherwise. Open positions have no sell_date; skip them.
        closed_legs_only = [l for l in chart_legs if not l.get("is_open")]

        day_pl   = defaultdict(float)
        day_cost = defaultdict(float)
        for l in closed_legs_only:
            d = l["sell_date"].date()
            day_pl[d]   += l["pl_dollar"]
            day_cost[d] += l["buy_cost"]

        sorted_days = sorted(day_pl.keys())

        if sorted_days:
            USE_WEEKLY = len(sorted_days) > 60

            if USE_WEEKLY:
                week_pl   = defaultdict(float)
                week_cost = defaultdict(float)
                for d, pl in day_pl.items():
                    iso = d.isocalendar()
                    wk  = f"{iso[0]}-W{iso[1]:02d}"
                    week_pl[wk]   += pl
                    week_cost[wk] += day_cost[d]
                keys   = sorted(week_pl.keys())
                values = [week_pl[k]   for k in keys]
                costs  = [week_cost[k] for k in keys]
                bar_title = "Weekly P/L"
            else:
                keys   = [d.strftime("%m/%d") for d in sorted_days]
                values = [day_pl[d]   for d in sorted_days]
                costs  = [day_cost[d] for d in sorted_days]
                bar_title = "Daily P/L"

            bar_colors = [GREEN if v >= 0 else RED for v in values]
            x = range(len(keys))
            self.ax_daily.bar(x, values, color=bar_colors, alpha=0.88, width=0.6)
            self.ax_daily.set_xticks(list(x))
            self.ax_daily.set_xticklabels(keys, rotation=45, ha="right", fontsize=7)
            self.ax_daily.axhline(y=0, color=DIM, linewidth=0.8)
            self.ax_daily.set_title(bar_title, color=TEXT, fontsize=10, pad=8)

            n = len(keys)
            label_fs = 7 if n <= 15 else 6 if n <= 30 else 5
            for xi, val, cost in zip(x, values, costs):
                va     = "bottom" if val >= 0 else "top"
                offset = max(abs(val) * 0.03, 0.5)
                pct    = (val / cost * 100) if cost > 0 else 0.0
                self.ax_daily.text(
                    xi, val + (offset if val >= 0 else -offset),
                    f"${val:+.0f}\n({pct:+.1f}%)", ha="center", va=va,
                    color=TEXT, fontsize=label_fs, clip_on=True,
                    linespacing=1.2
                )

        # ── 4. Win Rate by Strategy ──
        method_wins = defaultdict(int)
        method_total = defaultdict(int)
        for l in chart_legs:
            if l.get("is_open"):
                continue  # open positions have no win/loss
            m = l.get("method", "Swing Trader")
            method_total[m] += 1
            if l["pl_dollar"] > 0:
                method_wins[m] += 1

        methods = [m for m in ["Swing Trader", "LEAP Strategy"] if method_total[m] > 0]
        wr_vals = [(method_wins[m] / method_total[m] * 100) if method_total[m] > 0 else 0
                   for m in methods]
        wr_colors = [METHOD_COLORS.get(m, DIM) for m in methods]
        wr_labels = [METHOD_SHORT.get(m, m) for m in methods]

        if methods:
            bars = self.ax_wr.bar(wr_labels, wr_vals, color=wr_colors, alpha=0.9)
            for bar, val in zip(bars, wr_vals):
                self.ax_wr.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                                f"{val:.0f}%", ha="center", va="bottom",
                                color=TEXT, fontsize=9)
            self.ax_wr.set_ylim(0, 110)
            self.ax_wr.set_title("Win Rate % by Strategy", color=TEXT, fontsize=10, pad=8)

        # ── 4. Cumulative P/L Line ──
        closed_chart = [l for l in chart_legs if not l.get("is_open")]
        sorted_legs = sorted(closed_chart, key=lambda l: l["sell_date"])
        cum = {"Swing Trader": [], "LEAP Strategy": []}
        running = {"Swing Trader": 0.0, "LEAP Strategy": 0.0}
        dates_seen = {"Swing Trader": [], "LEAP Strategy": []}

        for l in sorted_legs:
            m = l.get("method", "Swing Trader")
            if m not in cum:
                continue
            running[m] += l["pl_dollar"]
            cum[m].append(running[m])
            dates_seen[m].append(l["sell_date"])

        for m, color in [("Swing Trader", GREEN), ("LEAP Strategy", BLUE)]:
            if cum[m]:
                self.ax_cum.plot(dates_seen[m], cum[m], color=color, linewidth=1.5,
                                  label=METHOD_SHORT[m], marker=".", markersize=3)

        if any(cum[m] for m in cum):
            self.ax_cum.legend(fontsize=8, facecolor=HEADER, edgecolor=BORDER, labelcolor=TEXT)
            self.ax_cum.set_title("Cumulative P/L", color=TEXT, fontsize=10, pad=8)
            self.ax_cum.axhline(y=0, color=DIM, linewidth=0.5)
            self.ax_cum.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            self.ax_cum.tick_params(axis="x", rotation=45)

        self.fig.tight_layout(pad=1.5)
        self.chart_canvas.draw()

    def _expand_bar_chart(self):
        """Open a large popup window showing just the daily/weekly P/L bar chart."""
        chart_legs = [l for l in self.filtered_legs
                      if l.get("method") != "Excluded" and not l.get("is_open", False)]
        if self.chart_mode == "all":
            chart_legs += [l for l in self.open_positions
                           if l.get("method") != "Excluded"]

        closed_only = [l for l in chart_legs if not l.get("is_open")]
        if not closed_only:
            return

        from collections import defaultdict
        day_pl   = defaultdict(float)
        day_cost = defaultdict(float)
        for l in closed_only:
            d = l["sell_date"].date()
            day_pl[d]   += l["pl_dollar"]
            day_cost[d] += l["buy_cost"]
        sorted_days = sorted(day_pl.keys())

        USE_WEEKLY = len(sorted_days) > 60
        if USE_WEEKLY:
            week_pl   = defaultdict(float)
            week_cost = defaultdict(float)
            for d, pl in day_pl.items():
                iso = d.isocalendar()
                wk  = f"{iso[0]}-W{iso[1]:02d}"
                week_pl[wk]   += pl
                week_cost[wk] += day_cost[d]
            keys   = sorted(week_pl.keys())
            values = [week_pl[k]   for k in keys]
            costs  = [week_cost[k] for k in keys]
            title  = "Weekly P/L"
        else:
            keys   = [d.strftime("%b %d") for d in sorted_days]
            values = [day_pl[d]   for d in sorted_days]
            costs  = [day_cost[d] for d in sorted_days]
            title  = "Daily P/L"

        popup = tk.Toplevel(self.root)
        popup.title(f"Trade Journal — {title} (double-click to close)")
        popup.configure(bg=PANEL)
        popup.geometry("1100x520")

        fig_big = Figure(figsize=(13, 5.2), dpi=96, facecolor=PANEL)
        fig_big.subplots_adjust(left=0.06, right=0.97, top=0.88, bottom=0.18)
        ax = fig_big.add_subplot(1, 1, 1)
        ax.set_facecolor(HEADER)
        ax.tick_params(colors=DIM, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(BORDER)

        bar_colors = [GREEN if v >= 0 else RED for v in values]
        x = range(len(keys))
        ax.bar(x, values, color=bar_colors, alpha=0.88, width=0.6)
        ax.set_xticks(list(x))
        ax.set_xticklabels(keys, rotation=45, ha="right", fontsize=9)
        ax.axhline(y=0, color=DIM, linewidth=0.8)
        ax.set_title(title, color=TEXT, fontsize=13, pad=10)

        # Dollar + % labels on every bar
        n = len(keys)
        label_fs = 9 if n <= 15 else 8 if n <= 30 else 7 if n <= 50 else 6
        for xi, val, cost in zip(x, values, costs):
            va     = "bottom" if val >= 0 else "top"
            offset = max(abs(val) * 0.03, 0.5)
            pct    = (val / cost * 100) if cost > 0 else 0.0
            ax.text(xi, val + (offset if val >= 0 else -offset),
                    f"${val:+.0f}\n({pct:+.1f}%)", ha="center", va=va,
                    color=TEXT, fontsize=label_fs, clip_on=True,
                    linespacing=1.2)

        # Total in top-right corner
        total = sum(values)
        color = GREEN if total >= 0 else RED
        ax.text(0.99, 0.97, f"Total: ${total:+,.2f}",
                transform=ax.transAxes, ha="right", va="top",
                color=color, fontsize=11, fontweight="bold")

        canvas_big = FigureCanvasTkAgg(fig_big, master=popup)
        canvas_big.get_tk_widget().pack(fill="both", expand=True)
        canvas_big.draw()

        popup.bind("<Double-Button-1>", lambda e: popup.destroy())
        tk.Button(popup, text="✕  Close", bg=BORDER, fg=TEXT,
                  bd=0, padx=14, pady=4, font=("Helvetica", 10),
                  command=popup.destroy).pack(pady=(0, 8))

    # ── Export ───────────────────────────────────────────────────

    def _export_csv(self):
        if not self.filtered_legs:
            messagebox.showinfo("No Data", "Load a CSV first.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="trade_journal_export.csv"
        )
        if not path:
            return

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Ticker", "Buy Date", "Sell Date", "Quantity",
                              "Hold Days", "$ P/L", "% P/L", "Strategy",
                              "Buy Price", "Sell Price", "Commission"])
            for leg in self.filtered_legs:
                sell_str = leg["sell_date"].strftime("%Y-%m-%d") if leg["sell_date"] else "OPEN"
                writer.writerow([
                    leg["ticker"],
                    leg["buy_date"].strftime("%Y-%m-%d"),
                    sell_str,
                    leg["qty_str"],
                    leg["hold_days"],
                    f"{leg['pl_dollar']:.2f}",
                    f"{leg['pl_pct']:.1f}",
                    leg.get("method", "Swing Trader"),
                    f"{leg['buy_price']:.2f}",
                    f"{leg['sell_price']:.2f}",
                    f"{leg['commission']:.2f}",
                ])

        messagebox.showinfo("Exported", f"Saved {len(self.filtered_legs)} trades to:\n{path}")

    def _write_open_positions(self, path):
        """Write open positions to `path` (no dialog). Creates the directory if
        needed. Returns the count written. Option symbols skipped at source.
        Columns (exact, ordered): Ticker,Buy Date,Quantity,Buy Price,Buy Cost,
        Strategy,Hold Days — per the swing flag-tool spec."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        n = 0
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Ticker", "Buy Date", "Quantity", "Buy Price",
                              "Buy Cost", "Strategy", "Hold Days"])
            for p in self.open_positions:
                if p.get("is_option") or looks_like_option_symbol(p["ticker"]):
                    continue
                buy_str = p["buy_date"].strftime("%Y-%m-%d") if p.get("buy_date") else ""
                writer.writerow([
                    str(p["ticker"]).strip().upper(),
                    buy_str,
                    f"{p['qty']:g}",                 # numeric, no " sh"
                    f"{p['buy_price']:.4f}",         # per-share entry
                    f"{p['buy_cost']:.2f}",          # total dollars in
                    p.get("method", "Swing Trader"),
                    p.get("hold_days", 0),
                ])
                n += 1
        return n

    def _auto_export_open_positions(self):
        """Silently refresh the swing export at the stable path on every load.
        Never fatal — an export failure must not break loading."""
        try:
            if self.open_positions:
                n = self._write_open_positions(SWING_EXPORT_PATH)
                self.status_label.configure(
                    text=f"{self.status_label.cget('text')}  ·  {n}→swing")
        except Exception:
            pass  # disk/permission issue — silent; manual button still available

    def _export_open_positions(self):
        """Toolbar button: write open positions to the stable swing path
        automatically (no save dialog). Confirms where it landed."""
        if not self.open_positions:
            messagebox.showinfo("No Open Positions",
                                "There are no open positions to export.\n"
                                "(Load a trade-history or positions CSV first.)")
            return
        try:
            n = self._write_open_positions(SWING_EXPORT_PATH)
        except Exception as e:
            messagebox.showerror("Export Failed",
                                 f"Couldn't write to:\n{SWING_EXPORT_PATH}\n\n{e}")
            return
        messagebox.showinfo("Exported",
                            f"Saved {n} open stock positions to:\n{SWING_EXPORT_PATH}")

    # ── Run ──────────────────────────────────────────────────────

    def _on_close(self):
        """On close: freshen export, regenerate flags synchronously (best-effort, 30s cap), then exit.
        Force-quit (pkill -f trade_journal.py) always available if it hangs."""
        note = None
        try:
            note = tk.Toplevel(self.root)
            note.title("Closing")
            note.configure(bg=PANEL)
            tk.Label(note, text="Updating flags before close…",
                     bg=PANEL, fg=TEXT, padx=24, pady=16).pack()
            note.update()          # force paint before we block
        except Exception:
            pass
        try:
            self._auto_export_open_positions()     # freshen CSV (fast, no network)
        except Exception as e:
            print("close: export failed:", e)
        try:
            flag_script = os.path.expanduser("~/Downloads/swing_flag.py")
            subprocess.run([sys.executable, flag_script, "--no-open"], timeout=30)
        except subprocess.TimeoutExpired:
            print("close: flag regen timed out (30s) — closing anyway")
        except Exception as e:
            print("close: flag regen failed:", e)
        try:
            if note is not None:
                note.destroy()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app = TradeJournalApp()
    app.run()
