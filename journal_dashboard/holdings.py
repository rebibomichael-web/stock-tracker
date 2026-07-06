#!/usr/bin/env python3
"""
holdings.py — cross-account share counts + adjusted cost basis.

Answers: "TSLA across all accounts = X shares at $Y basis" — something the
trade journal's FIFO engine cannot do because it drops the Account column.

Reads Fidelity trade-history CSVs (Accounts_History*.csv), keyed by ACCOUNT
NUMBER (account *names* are not unique — two different accounts are both
labeled "Joint WROS - TOD"), and builds per-account and combined open
positions with the adjusted basis of the remaining shares:

  • BUY / YOU BOUGHT ......... adds a lot (basis = |Amount|, i.e. cost
                               including commission+fees, per IRS basis rules)
  • REINVESTMENT ............. adds a lot (reinvested dividends raise basis)
  • SELL / YOU SOLD .......... consumes lots FIFO
  • TRANSFERRED TO/FROM ...... moves lots between accounts — basis and
                               acquisition date carry over (transfers between
                               your own accounts are not taxable events)
  • EXPIRED / ASSIGNED ....... option lifecycle rows (options are tracked
                               separately, in contracts, never as shares)

Multiple overlapping exports are unioned with max-multiplicity dedup: a row's
count = the MAX number of times it appears in any single file, so re-exported
duplicates collapse while two genuinely identical same-day fills survive.

Limitations (reported as flags/warnings, never silently):
  • Shares bought before the earliest export row are invisible; if sells or
    transfers exceed known lots the symbol is flagged and the remainder is
    tracked as an "uncovered" quantity.
  • Wash-sale basis adjustments and corporate actions (splits/spinoffs) are
    not applied.
  • Same-day ordering is approximated (adds before removes).

stdlib-only. CLI:  python3 holdings.py [--dir DIR | --csv FILE ...] [--json]
                   [--symbol SYM] [--include-cash] [--selftest]
"""

import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
from collections import defaultdict

# Cash-sweep / money-market symbols: excluded from the stock table by default
# (their share counts are meaningless — cash movements don't all appear here).
MONEY_MARKET = {"SPAXX", "FDRXX", "SPRXX", "FZFXX", "FCASH", "CORE"}

OPTION_SYMBOL_RE = re.compile(r"^-?[A-Z]{1,6}\d{6}[CP][\d.]+$")

# Friendly short names for Fidelity account labels (fallback: label as-is).
ACCOUNT_SHORT = {
    "JOINT WROS - TOD": "Joint",
    "ROTH IRA": "Roth IRA",
    "SEP-IRA": "SEP-IRA",
    "TRADITIONAL IRA": "Trad IRA",
    "INDIVIDUAL": "Individual",
    "INDIVIDUAL - TOD": "Individual",
}

QTY_EPS = 1e-6


def _num(v):
    """Parse a Fidelity numeric cell ('', '1,234.56', '-0.295') -> float."""
    s = str(v or "").strip().replace(",", "").replace("$", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(s):
    s = str(s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def classify_action(action):
    """Map a Fidelity Action string to a transaction kind (or None)."""
    a = str(action or "").upper()
    if "BOUGHT" in a or ("BUY" in a and "BUYS" not in a):
        return "BUY"
    if "SOLD" in a or "SELL" in a:
        return "SELL"
    if "REINVESTMENT" in a:
        return "REINVEST"
    if "TRANSFERRED" in a:
        return "TRANSFER"
    if "EXPIRED" in a:
        return "EXPIRE"
    if a.startswith("ASSIGNED"):
        return "ASSIGN"
    return None


def transfer_hint(action):
    """Counterparty account from 'TRANSFERRED TO VS X17-212229-1 ...'
    -> 'X172122291' (dashes stripped). None when absent."""
    m = re.search(r"\bVS\s+([A-Z0-9-]+)", str(action or "").upper())
    return m.group(1).replace("-", "") if m else None


def parse_file(path):
    """One Fidelity trade-history CSV -> list of normalized txn dicts.
    Footer/disclaimer rows (unparseable date) are skipped."""
    txns = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header_idx = None
    for i, row in enumerate(rows):
        if row and str(row[0]).strip().lower() == "run date":
            header_idx = i
            break
    if header_idx is None:
        return txns
    cols = [str(c).strip().lower() for c in rows[header_idx]]

    def col(row, name):
        try:
            return row[cols.index(name)]
        except (ValueError, IndexError):
            return ""

    for row in rows[header_idx + 1:]:
        if not row or len(row) < 4:
            continue
        date = _parse_date(col(row, "run date"))
        if date is None:
            continue                       # footer / blank / disclaimer line
        kind = classify_action(col(row, "action"))
        if kind is None:
            continue                       # dividends, interest, EFT, ...
        symbol = str(col(row, "symbol")).strip().upper()
        if not symbol:
            continue                       # cash-only transfer/contribution
        qty = _num(col(row, "quantity"))
        if abs(qty) < QTY_EPS:
            continue
        acct_num = str(col(row, "account number")).strip()
        txns.append({
            "date": date,
            "kind": kind,
            "symbol": symbol,
            "desc": str(col(row, "description")).strip(),
            "acct": acct_num or "?",
            "acct_name": str(col(row, "account")).strip(),
            "qty": qty,                    # signed, as exported
            "price": abs(_num(col(row, "price"))),
            "amount": _num(col(row, "amount")),
            "hint": transfer_hint(col(row, "action")) if kind == "TRANSFER" else None,
        })
    return txns


def _txn_key(t):
    return (t["date"].isoformat(), t["kind"], t["symbol"], t["acct"],
            round(t["qty"], 6), round(t["price"], 6), round(t["amount"], 4))


def union_files(paths):
    """Union overlapping exports. Each unique row's multiplicity = the MAX
    count seen in any single file (collapses re-export duplicates, keeps
    genuinely repeated identical fills). Returns (txns, n_unique_rows)."""
    max_mult = {}
    first = {}
    for p in paths:
        counts = defaultdict(int)
        for t in parse_file(p):
            k = _txn_key(t)
            counts[k] += 1
            if k not in first:
                first[k] = t
        for k, c in counts.items():
            if c > max_mult.get(k, 0):
                max_mult[k] = c
    txns = []
    for k, t in first.items():
        txns.extend([t] * max_mult[k])
    return txns, len(first)


# ═══════════════════════════════════════════════════════════════════════════
#  Lot engine — one Book per (account, symbol)
# ═══════════════════════════════════════════════════════════════════════════

class Book:
    """Open lots for one (account, symbol). `uncovered` counts removals that
    had no lot to consume (short sales, or activity predating the window)."""

    def __init__(self):
        self.lots = []          # [{qty, basis, date, est}] FIFO order
        self.uncovered = 0.0

    def add(self, qty, basis_total, date, est=False):
        """qty > 0. Closing an uncovered quantity first, remainder = new lot."""
        offset = min(self.uncovered, qty)
        if offset > QTY_EPS:
            self.uncovered -= offset
            basis_total *= (qty - offset) / qty if qty > QTY_EPS else 0.0
            qty -= offset
        if qty > QTY_EPS:
            self.lots.append({"qty": qty, "basis": max(basis_total, 0.0),
                              "date": date, "est": est})

    def remove(self, qty):
        """qty > 0. Consume lots FIFO; returns (removed_lots, shortfall)."""
        removed = []
        while qty > QTY_EPS and self.lots:
            lot = self.lots[0]
            take = min(qty, lot["qty"])
            frac = take / lot["qty"]
            removed.append({"qty": take, "basis": lot["basis"] * frac,
                            "date": lot["date"], "est": lot["est"]})
            lot["qty"] -= take
            lot["basis"] -= lot["basis"] * frac if lot["qty"] > QTY_EPS else lot["basis"]
            if lot["qty"] <= QTY_EPS:
                self.lots.pop(0)
            qty -= take
        shortfall = qty if qty > QTY_EPS else 0.0
        self.uncovered += shortfall
        return removed, shortfall

    @property
    def shares(self):
        return sum(l["qty"] for l in self.lots)

    @property
    def basis(self):
        return sum(l["basis"] for l in self.lots)

    @property
    def has_estimated(self):
        return any(l["est"] for l in self.lots)


_PHASE = {"BUY": 0, "REINVEST": 0, "ASSIGN": 1, "EXPIRE": 1,
          "TRANSFER_OUT": 2, "TRANSFER_IN": 3, "SELL": 4}


def _phase(t):
    if t["kind"] == "TRANSFER":
        return _PHASE["TRANSFER_OUT"] if t["qty"] < 0 else _PHASE["TRANSFER_IN"]
    return _PHASE.get(t["kind"], 5)


def run_engine(txns):
    """Process txns chronologically. Returns (books, accounts, flags):
    books[(acct, symbol)] -> Book; accounts[acct] -> display name;
    flags[symbol] -> set of data-quality flag strings."""
    books = defaultdict(Book)
    accounts = {}
    flags = defaultdict(set)
    transit = defaultdict(list)   # symbol -> [{qty, lots, dest_hint}]

    for t in sorted(txns, key=lambda t: (t["date"], _phase(t))):
        sym, acct = t["symbol"], t["acct"]
        accounts.setdefault(acct, t["acct_name"])
        book = books[(acct, sym)]
        kind, qty = t["kind"], t["qty"]

        if kind in ("BUY", "REINVEST"):
            basis = abs(t["amount"]) or abs(qty) * t["price"]
            book.add(abs(qty), basis, t["date"])

        elif kind == "SELL":
            _, short = book.remove(abs(qty))
            if short > QTY_EPS:
                flags[sym].add("sold more than the data window shows bought "
                               "— pre-window shares or short sale; count/basis incomplete")

        elif kind == "TRANSFER" and qty < 0:          # outgoing
            moved, short = book.remove(abs(qty))
            entry = {"qty": abs(qty), "lots": moved, "dest": t["hint"]}
            if short > QTY_EPS:
                entry["lots"].append({"qty": short, "basis": 0.0,
                                      "date": t["date"], "est": True})
                flags[sym].add("transferred-out shares exceed known lots — "
                               "basis for the moved remainder is unknown")
            transit[sym].append(entry)

        elif kind == "TRANSFER" and qty > 0:          # incoming
            match = None
            for e in transit[sym]:
                if abs(e["qty"] - abs(qty)) < 1e-4 and (
                        e["dest"] is None or e["dest"].startswith(acct)
                        or acct.startswith(e["dest"][:len(acct)])):
                    match = e
                    break
            if match is None and transit[sym]:
                for e in transit[sym]:
                    if abs(e["qty"] - abs(qty)) < 1e-4:
                        match = e
                        break
            if match is not None:
                transit[sym].remove(match)
                for lot in match["lots"]:
                    book.add(lot["qty"], lot["basis"], lot["date"], lot["est"])
            else:
                # No visible outgoing side (source account not in exports).
                # Best available basis = transfer-date market value if present.
                book.add(abs(qty), abs(t["amount"]), t["date"], est=True)
                flags[sym].add("transfer-in had no matching transfer-out — "
                               "basis estimated from transfer-date value")

        elif kind in ("EXPIRE", "ASSIGN"):
            # Options only: signed offset rows (+1 closes a short contract,
            # -1 closes a long). These CLOSE positions, never open them — a
            # positive qty only absorbs an outstanding short; any remainder
            # means the opening trade predates the data window.
            if qty > 0:
                offset = min(book.uncovered, qty)
                book.uncovered -= offset
                if qty - offset > QTY_EPS:
                    flags[sym].add("expiry/assignment closed a position "
                                   "opened before the data window")
            else:
                _, short = book.remove(abs(qty))
                if short > QTY_EPS:
                    flags[sym].add("expiry/assignment closed a position "
                                   "opened before the data window")

    # Anything still in transit went out but never arrived inside the window.
    for sym, entries in transit.items():
        if entries:
            flags[sym].add("transfer-out with no matching transfer-in — "
                           "shares left the visible accounts")
    return books, accounts, flags


# ═══════════════════════════════════════════════════════════════════════════
#  Aggregation
# ═══════════════════════════════════════════════════════════════════════════

def _acct_label(acct_num, acct_name):
    short = ACCOUNT_SHORT.get(str(acct_name).upper().strip(), acct_name or "Account")
    tail = acct_num[-4:] if len(acct_num) >= 4 else acct_num
    return f"{short} ··{tail}"


def _r(v, dp):
    return round(v + 0.0, dp)


def compute_holdings(txns, n_unique, n_files, include_cash=False):
    """txns -> holdings payload dict (see docstring at top of file)."""
    books, accounts, flags = run_engine(txns)

    per_symbol = defaultdict(list)     # symbol -> [(acct, Book)]
    for (acct, sym), book in books.items():
        if book.shares > QTY_EPS or book.uncovered > QTY_EPS:
            per_symbol[sym].append((acct, book))

    stocks, options = [], []
    warnings = []
    for sym in sorted(per_symbol):
        is_opt = bool(OPTION_SYMBOL_RE.match(sym))
        if not is_opt and not include_cash and sym in MONEY_MARKET:
            continue
        acct_rows, tot_sh, tot_basis, tot_unc, est = [], 0.0, 0.0, 0.0, False
        for acct, book in sorted(per_symbol[sym]):
            sh, ba = book.shares, book.basis
            tot_sh += sh
            tot_basis += ba
            tot_unc += book.uncovered
            est = est or book.has_estimated
            if sh > QTY_EPS:
                acct_rows.append({
                    "a": _acct_label(acct, accounts.get(acct, "")),
                    "shares": _r(sh, 4),
                    "basis": _r(ba, 2),
                    "bps": _r(ba / sh, 2),
                })
        net = tot_sh - tot_unc
        sym_flags = sorted(flags.get(sym, ()))
        if est:
            sym_flags.append("basis partly estimated (unmatched transfer)")
        if abs(net) < QTY_EPS and not sym_flags:
            continue
        row = {
            "t": sym,
            "shares": _r(net, 4),
            "basis": _r(tot_basis, 2),
            "bps": _r(tot_basis / tot_sh, 2) if tot_sh > QTY_EPS else None,
            "accounts": acct_rows,
        }
        if sym_flags:
            row["flags"] = sym_flags
        if is_opt:
            desc = next((t["desc"] for t in txns if t["symbol"] == sym and t["desc"]), "")
            row["label"] = desc[:44]
            row["contracts"] = row.pop("shares")
            options.append(row)
        else:
            stocks.append(row)

    stocks.sort(key=lambda s: -(s["basis"] or 0))
    options.sort(key=lambda s: -(s["basis"] or 0))
    dates = [t["date"] for t in txns]
    flagged = sum(1 for s in stocks + options if s.get("flags"))
    if flagged:
        warnings.append(f"{flagged} symbol(s) have incomplete history — "
                        f"counts/basis may miss pre-window activity")
    return {
        "stocks": stocks,
        "options": options,
        "windowStart": min(dates).isoformat() if dates else None,
        "windowEnd": max(dates).isoformat() if dates else None,
        "files": n_files,
        "rowsUnique": n_unique,
        "warnings": warnings,
    }


def build_from_paths(paths, include_cash=False):
    txns, n_unique = union_files(paths)
    return compute_holdings(txns, n_unique, len(paths), include_cash)


def discover_csvs(directory):
    """All Accounts_History*.csv in a directory, stable order."""
    return sorted(glob.glob(os.path.join(directory, "Accounts_History*.csv")))


def default_csv_dir():
    """Directory of the journal app's last-loaded CSV (its config), else None."""
    cfg = os.path.expanduser("~/.trade_journal_config.json")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            last = json.load(f).get("last_csv_path")
        if last and os.path.isfile(last):
            return os.path.dirname(last)
    except (OSError, ValueError):
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  Self-test — synthetic rows, zero file/network access
# ═══════════════════════════════════════════════════════════════════════════

def _tx(date, kind, sym, acct, qty, price=0.0, amount=0.0, hint=None,
        name="Joint WROS - TOD"):
    return {"date": dt.date.fromisoformat(date), "kind": kind, "symbol": sym,
            "desc": sym, "acct": acct, "acct_name": name, "qty": qty,
            "price": price, "amount": amount, "hint": hint}


def selftest():
    # 1. Multi-account aggregate with FIFO sell: basis of REMAINING shares.
    txns = [
        _tx("2026-01-07", "BUY", "TSLA", "X17212229", 5, 435.25, -2176.25),
        _tx("2026-01-07", "BUY", "TSLA", "X83768586", 1, 437.86, -437.86),
        _tx("2026-01-20", "BUY", "TSLA", "235498151", 3, 427.21, -1281.63, name="ROTH IRA"),
        _tx("2026-02-01", "SELL", "TSLA", "X17212229", -2, 500.00, 1000.00),
    ]
    h = compute_holdings(txns, len(txns), 1)
    s = h["stocks"][0]
    assert s["t"] == "TSLA" and abs(s["shares"] - 7) < 1e-9, s
    # remaining basis: 3/5 of first lot + full others
    want = 2176.25 * 3 / 5 + 437.86 + 1281.63
    assert abs(s["basis"] - round(want, 2)) < 0.01, (s["basis"], want)
    assert abs(s["bps"] - round(want / 7, 2)) < 0.01
    assert len(s["accounts"]) == 3 and "flags" not in s
    roth = [a for a in s["accounts"] if a["a"].startswith("Roth IRA")]
    assert roth and abs(roth[0]["basis"] - 1281.63) < 0.01

    # 2. Transfer moves basis + acquisition date across accounts; total flat.
    txns = [
        _tx("2026-01-07", "BUY", "TSLA", "X83768586", 2, 400.00, -800.00),
        _tx("2026-04-06", "TRANSFER", "TSLA", "X83768586", -1, hint="X172122291"),
        _tx("2026-04-06", "TRANSFER", "TSLA", "X17212229", 1, amount=0.0),
    ]
    h = compute_holdings(txns, len(txns), 1)
    s = h["stocks"][0]
    assert abs(s["shares"] - 2) < 1e-9 and abs(s["basis"] - 800.00) < 0.01, s
    assert "flags" not in s, s
    dst = [a for a in s["accounts"] if "2229" in a["a"]][0]
    assert abs(dst["basis"] - 400.00) < 0.01          # original basis carried

    # 3. Reinvestment adds fractional shares at |Amount| basis.
    txns = [_tx("2026-03-31", "REINVEST", "PLTU", "X17212229", 0.114, 41.34, -4.71)]
    h = compute_holdings(txns, 1, 1)
    s = h["stocks"][0]
    assert abs(s["shares"] - 0.114) < 1e-9 and abs(s["basis"] - 4.71) < 0.01

    # 4. Oversell (pre-window buy invisible) -> flagged, net count honest.
    txns = [
        _tx("2026-02-01", "BUY", "NVDA", "235498151", 1, 100, -100, name="ROTH IRA"),
        _tx("2026-02-10", "SELL", "NVDA", "235498151", -3, 110, 330, name="ROTH IRA"),
    ]
    h = compute_holdings(txns, 2, 1)
    s = h["stocks"][0]
    assert s["shares"] == -2 and s.get("flags"), s
    assert h["warnings"], h["warnings"]

    # 5. Money market excluded by default, included with the flag.
    txns = [_tx("2026-03-31", "REINVEST", "SPAXX", "X83768586", 0.25, 1, -0.25)]
    assert compute_holdings(txns, 1, 1)["stocks"] == []
    assert compute_holdings(txns, 1, 1, include_cash=True)["stocks"]

    # 6. Options tracked separately in contracts; short put closed by expiry.
    txns = [
        _tx("2026-01-02", "SELL", "-BMNR260306P18", "X17212229", -1, 1.10, 109.35),
        _tx("2026-03-09", "EXPIRE", "-BMNR260306P18", "X17212229", 1),
        _tx("2026-04-06", "BUY", "-NOW280121C200", "X17212229", 1, 8.43, -843.67),
    ]
    h = compute_holdings(txns, 3, 1)
    assert h["stocks"] == []
    opts = {o["t"]: o for o in h["options"]}
    assert "-BMNR260306P18" not in opts                # closed by expiry
    now = opts["-NOW280121C200"]
    assert now["contracts"] == 1 and abs(now["basis"] - 843.67) < 0.01

    # 6b. Assignment whose opening short predates the window: no phantom
    #     contract is fabricated; the symbol is flagged instead.
    txns = [_tx("2026-02-02", "ASSIGN", "-BMNR260130P26", "X17212229", 1)]
    h = compute_holdings(txns, 1, 1)
    opts = {o["t"]: o for o in h["options"]}
    row = opts.get("-BMNR260130P26")
    assert row is None or (abs(row["contracts"]) < 1e-9 and row.get("flags")), row

    # 7. Unmatched transfer-in -> estimated basis from transfer-date value.
    txns = [_tx("2026-04-09", "TRANSFER", "TSLA", "X17212229", 0.146, amount=50.46)]
    h = compute_holdings(txns, 1, 1)
    s = h["stocks"][0]
    assert abs(s["shares"] - 0.146) < 1e-9 and abs(s["basis"] - 50.46) < 0.01
    assert any("estimated" in f for f in s["flags"]), s

    # 8. Max-multiplicity dedup: re-export collapses, real double-fill survives.
    row = "01/07/2026,\"Joint WROS - TOD\",\"X17212229\",\"YOU BOUGHT TESLA INC COM (TSLA) (Margin)\",TSLA,\"TESLA INC COM\",Margin,0,,USD,435.25,1,0,,,,-435.25,01/08/2026"
    hdr = ("Run Date,Account,Account Number,Action,Symbol,Description,Type,"
           "Exchange Quantity,Exchange Currency,Currency,Price,Quantity,"
           "Exchange Rate,Commission,Fees,Accrued Interest,Amount,Settlement Date")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "Accounts_History.csv")
        p2 = os.path.join(td, "Accounts_History (1).csv")
        with open(p1, "w", encoding="utf-8") as f:
            f.write("\n\n" + hdr + "\n" + row + "\n")                 # once
        with open(p2, "w", encoding="utf-8") as f:
            f.write("﻿\n\n" + hdr + "\n" + row + "\n" + row + "\n")  # twice (2 fills)
        txns, n_unique = union_files([p1, p2])
        assert n_unique == 1 and len(txns) == 2, (n_unique, len(txns))
        h = compute_holdings(txns, n_unique, 2)
        assert abs(h["stocks"][0]["shares"] - 2) < 1e-9

    # 9. Consistency: FIFO net equals the signed quantity sum per symbol.
    txns = [
        _tx("2026-01-07", "BUY", "MU", "X17212229", 5, 100, -500),
        _tx("2026-01-08", "SELL", "MU", "X17212229", -1.5, 110, 165),
        _tx("2026-01-09", "TRANSFER", "MU", "X17212229", -2, hint="X837685861"),
        _tx("2026-01-09", "TRANSFER", "MU", "X83768586", 2),
        _tx("2026-01-10", "SELL", "MU", "X83768586", -0.5, 120, 60),
    ]
    h = compute_holdings(txns, len(txns), 1)
    s = h["stocks"][0]
    signed = 5 - 1.5 - 2 + 2 - 0.5
    assert abs(s["shares"] - signed) < 1e-9, (s["shares"], signed)
    assert "flags" not in s

    print("holdings selftest OK — aggregate/FIFO basis, transfers, reinvest, "
          "oversell flag, money-market filter, options, dedup, consistency")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _fmt_shares(v):
    return f"{v:,.4f}".rstrip("0").rstrip(".") if v == v else "—"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Cross-account share counts + adjusted cost basis from "
                    "Fidelity Accounts_History CSVs")
    ap.add_argument("--csv", action="append", default=None,
                    help="a CSV to include (repeatable)")
    ap.add_argument("--dir", default=None,
                    help="directory to scan for Accounts_History*.csv "
                         "(default: the journal app's last CSV's folder)")
    ap.add_argument("--symbol", default=None, help="only this symbol")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--include-cash", action="store_true",
                    help="include money-market/core positions (SPAXX, ...)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    paths = list(args.csv or [])
    if args.dir:
        paths += discover_csvs(args.dir)
    if not paths:
        d = default_csv_dir()
        if d:
            paths = discover_csvs(d)
    paths = [p for p in dict.fromkeys(paths) if os.path.isfile(p)]
    if not paths:
        print("ERROR: no CSVs found — pass --csv FILE or --dir DIR", file=sys.stderr)
        return 1

    h = build_from_paths(paths, include_cash=args.include_cash)
    if args.symbol:
        want = args.symbol.upper()
        h["stocks"] = [s for s in h["stocks"] if s["t"] == want]
        h["options"] = [o for o in h["options"] if want in o["t"]]
    if args.json:
        print(json.dumps(h, indent=1))
        return 0

    print(f"Holdings across accounts — {h['files']} file(s), "
          f"{h['rowsUnique']} unique rows, window {h['windowStart']} → {h['windowEnd']}")
    print("=" * 78)
    if not h["stocks"]:
        print("(no open stock positions in the data window)")
    for s in h["stocks"]:
        bps = f"${s['bps']:,.2f}" if s["bps"] is not None else "—"
        flag = "  ⚠" if s.get("flags") else ""
        print(f"{s['t']:<8} {_fmt_shares(s['shares']):>12} sh   "
              f"adj basis ${s['basis']:>12,.2f}   {bps}/sh{flag}")
        for a in s["accounts"]:
            print(f"    {a['a']:<18} {_fmt_shares(a['shares']):>12} sh   "
                  f"${a['basis']:>12,.2f}   ${a['bps']:,.2f}/sh")
        for fl in s.get("flags", []):
            print(f"    ⚠ {fl}")
    if h["options"]:
        print("-" * 78)
        print("Options (contracts, tracked separately):")
        for o in h["options"]:
            print(f"{o['t']:<18} {o['contracts']:>6.0f} ct   "
                  f"basis ${o['basis']:>10,.2f}   {o.get('label', '')}")
            for fl in o.get("flags", []):
                print(f"    ⚠ {fl}")
    for w in h["warnings"]:
        print(f"⚠ {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
