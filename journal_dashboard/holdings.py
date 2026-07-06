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

A Fidelity POSITIONS export (Portfolio_Positions*.csv) in the same folder is
picked up automatically and becomes the authoritative current count + basis
(Fidelity's own adjusted basis, which includes lots older than the history
window); the transaction history then reconciles against it and quantifies
what it can't explain. Without a snapshot, history-only counts MISS anything
bought before the earliest export row and untouched since — a warning says so.

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

OPTION_SYMBOL_RE = re.compile(r"^-?([A-Z]{1,6})\d{6}[CP][\d.]+$")

# CUSIP-shaped symbol (9 chars, digit first and last — never a ticker or an
# option symbol). Fidelity sometimes switches a security's Symbol cell from
# the ticker to its CUSIP between exports; see union_files().
CUSIP_RE = re.compile(r"^[0-9][0-9A-Z]{7}[0-9]$")

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
    """Map a Fidelity Action string to a transaction kind (or None).
    Anchored prefixes, most-specific first — bare substrings would misread
    security NAMES ('ISHARES RUSSELL 2000' contains SELL, 'BEST BUY' contains
    BUY). Every trade row Fidelity exports starts with 'YOU BOUGHT'/'YOU SOLD'
    (including 'YOU BOUGHT ASSIGNED PUTS...', which is a BUY, vs the bare
    'ASSIGNED as of ...' option-removal row)."""
    a = str(action or "").upper().strip()
    if a.startswith("REINVESTMENT"):
        return "REINVEST"
    if a.startswith("TRANSFERRED"):
        return "TRANSFER"
    if a.startswith("EXPIRED"):
        return "EXPIRE"
    if a.startswith("ASSIGNED"):
        return "ASSIGN"
    if a.startswith("YOU BOUGHT"):
        return "BUY"
    if a.startswith("YOU SOLD"):
        return "SELL"
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
    # hint distinguishes same-day equal-qty transfers to DIFFERENT
    # destinations (None for every non-transfer row).
    return (t["date"].isoformat(), t["kind"], t["symbol"], t["acct"],
            round(t["qty"], 6), round(t["price"], 6), round(t["amount"], 4),
            t["hint"])


def _desc_key(t):
    return re.sub(r"\s+", " ", t["desc"]).strip().upper()


def _normalize_cusips(parsed_files):
    """Fidelity sometimes flips a security's Symbol between ticker and CUSIP
    across exports (real case: HON vs 438516106, description identical up to
    a trailing ' 1'). Rewrite CUSIP-shaped symbols to the ticker whose
    description matches; return the set that could not be mapped."""
    desc2sym = {}
    for rows in parsed_files:
        for t in rows:
            if t["symbol"] and not CUSIP_RE.match(t["symbol"]):
                desc2sym.setdefault(_desc_key(t), t["symbol"])
    unmapped = set()
    for rows in parsed_files:
        for t in rows:
            if not CUSIP_RE.match(t["symbol"]):
                continue
            dk = _desc_key(t)
            mapped = desc2sym.get(dk)
            if mapped is None:
                for k, v in desc2sym.items():
                    if k and (dk.startswith(k) or k.startswith(dk)):
                        mapped = v
                        break
            if mapped:
                t["symbol"] = mapped
            else:
                unmapped.add(t["symbol"])
    return unmapped


def union_files(paths):
    """Union overlapping exports. Each unique row's multiplicity = the MAX
    count seen in any single file (collapses re-export duplicates, keeps
    genuinely repeated identical fills). CUSIP-shaped symbols are normalized
    to their ticker first so the same fill exported under both spellings
    dedups. Returns (txns, n_unique_rows, notes) — notes are data-quality
    warnings (skipped files, unmappable CUSIPs)."""
    notes = []
    parsed_files = []
    for p in paths:
        try:
            parsed_files.append(parse_file(p))
        except (UnicodeDecodeError, csv.Error, OSError) as e:
            notes.append(f"skipped unreadable CSV {os.path.basename(p)}: {e}")
            print(f"WARNING: skipping unreadable CSV {p}: {e}", file=sys.stderr)
    unmapped = _normalize_cusips(parsed_files)
    if unmapped:
        notes.append("CUSIP-labeled symbol(s) with no ticker match kept as-is: "
                     + ", ".join(sorted(unmapped)))
    max_mult = {}
    first = {}
    for rows in parsed_files:
        counts = defaultdict(int)
        for t in rows:
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
    return txns, len(first), notes


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
        """qty > 0. Consume lots FIFO **by acquisition date** (a transferred-in
        lot keeps its original date and may be OLDER than lots already here —
        true FIFO disposes earliest-acquired first). min() is stable, so
        same-date lots fall back to insertion order.
        Returns (removed_lots, shortfall)."""
        removed = []
        while qty > QTY_EPS and self.lots:
            i = min(range(len(self.lots)), key=lambda j: self.lots[j]["date"])
            lot = self.lots[i]
            take = min(qty, lot["qty"])
            frac = take / lot["qty"]
            removed.append({"qty": take, "basis": lot["basis"] * frac,
                            "date": lot["date"], "est": lot["est"]})
            lot["qty"] -= take
            lot["basis"] -= lot["basis"] * frac if lot["qty"] > QTY_EPS else lot["basis"]
            if lot["qty"] <= QTY_EPS:
                self.lots.pop(i)
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


def _merge_snapshot(stocks, options, snapshot, include_cash):
    """Positions snapshot = authoritative current qty + Fidelity's own
    (adjusted) cost basis; the history engine's rows become reconciliation.
    Returns (stocks, options) rebuilt from the snapshot, plus engine-only
    rows (positions the snapshot doesn't show) flagged as such."""
    eng = {r["t"]: r for r in stocks + options}
    by_sym = defaultdict(list)
    for r in snapshot["rows"]:
        by_sym[r["symbol"]].append(r)

    out_stocks, out_options = [], []
    for sym in sorted(by_sym):
        srows = by_sym[sym]
        is_opt = srows[0]["is_option"] or bool(OPTION_SYMBOL_RE.match(sym))
        if not is_opt and not include_cash and sym in MONEY_MARKET:
            continue
        snap_qty = sum(r["qty"] for r in srows)
        known = [r for r in srows if r["basis"] is not None]
        snap_basis = sum(r["basis"] for r in known)
        e = eng.pop(sym, None)
        hist_net = (e or {}).get("shares", (e or {}).get("contracts", 0.0)) or 0.0
        sym_flags = []
        if abs(hist_net - snap_qty) > 1e-4:
            sym_flags.append(
                f"transaction history covers {_r(hist_net, 4)} of "
                f"{_r(snap_qty, 4)} — the rest predates the exports; count and "
                f"basis are Fidelity's own snapshot ({snapshot['asof'] or 'undated'})")
        if len(known) < len(srows):
            if e and abs(hist_net - snap_qty) <= 1e-4 and e.get("basis"):
                snap_basis = e["basis"]
                known = srows
                sym_flags.append("snapshot lacked cost basis — basis computed "
                                 "from transaction history")
            else:
                sym_flags.append("cost basis missing for part of this position "
                                 "in the snapshot")
        accts = []
        for r in sorted(srows, key=lambda r: r["acct"]):
            accts.append({
                "a": _acct_label(r["acct"], r["acct_name"]),
                "shares": _r(r["qty"], 4),
                "basis": _r(r["basis"], 2) if r["basis"] is not None else None,
                "bps": _r(r["basis"] / r["qty"], 2)
                       if (r["basis"] is not None and abs(r["qty"]) > QTY_EPS) else None,
            })
        row = {
            "t": sym,
            "shares": _r(snap_qty, 4),
            "basis": _r(snap_basis, 2) if known else None,
            "bps": _r(snap_basis / snap_qty, 2)
                   if (known and abs(snap_qty) > QTY_EPS) else None,
            "accounts": accts,
        }
        if sym_flags:
            row["flags"] = sym_flags
        if is_opt:
            row["label"] = (srows[0]["desc"] or "")[:44]
            row["contracts"] = row.pop("shares")
            out_options.append(row)
        else:
            out_stocks.append(row)

    # engine-only leftovers: positions history shows but the snapshot doesn't
    for sym, e in sorted(eng.items()):
        qty = e.get("shares", e.get("contracts"))
        if qty is None or abs(qty) < QTY_EPS:
            continue
        e.setdefault("flags", []).append(
            f"not in the positions snapshot ({snapshot['asof'] or 'undated'}) "
            f"— opened after it, or closed since the history window")
        (out_options if "contracts" in e else out_stocks).append(e)

    out_stocks.sort(key=lambda s: -(s["basis"] or 0))
    out_options.sort(key=lambda s: -(s["basis"] or 0))
    return out_stocks, out_options


def compute_holdings(txns, n_unique, n_files, include_cash=False, snapshot=None):
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
        if tot_unc > QTY_EPS:
            sym_flags.append(
                f"basis and basis/share cover the {_r(tot_sh, 4)} share(s) with "
                f"known lots; share count is net of {_r(tot_unc, 4)} uncovered "
                f"pre-window sale(s)")
        if abs(net) < QTY_EPS and not sym_flags:
            continue
        row = {
            "t": sym,
            "shares": _r(net, 4),
            "basis": _r(tot_basis, 2),
            "bps": _r(tot_basis / tot_sh, 2) if tot_sh > QTY_EPS else None,
            "accounts": acct_rows,
        }
        if tot_unc > QTY_EPS:
            row["lotShares"] = _r(tot_sh, 4)   # what basis/bps refer to
            row["uncovered"] = _r(tot_unc, 4)
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

    # Flags on symbols whose books ended flat (e.g. an assignment closing a
    # pre-window short) would otherwise vanish — surface them as warnings.
    # For a flat OPTION book, a lone sell-side-shortfall flag is a routine
    # short round trip (sold to open, closed in-window), not missing history.
    emitted = {r["t"] for r in stocks + options}
    for sym in sorted(set(flags) - emitted):
        if not include_cash and sym in MONEY_MARKET:
            continue
        sym_flags = flags[sym]
        if OPTION_SYMBOL_RE.match(sym):
            sym_flags = {f for f in sym_flags if not f.startswith("sold more")}
        for fl in sorted(sym_flags):
            warnings.append(f"{sym} (closed): {fl}")

    if snapshot:
        stocks, options = _merge_snapshot(stocks, options, snapshot, include_cash)
    elif stocks or options:
        # history alone CANNOT see positions opened before the earliest export
        # row and untouched since — say so instead of under-counting silently.
        ws = min(dates).isoformat() if dates else "?"
        warnings.append(
            f"counts come from transaction history starting {ws} — positions "
            f"opened earlier and untouched since are NOT included. For "
            f"authoritative totals put a Fidelity positions export "
            f"(Portfolio_Positions*.csv) in the same folder.")

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
        "source": "snapshot+history" if snapshot else "history",
        "snapshotAsOf": snapshot["asof"] if snapshot else None,
        "snapshotFile": snapshot["file"] if snapshot else None,
        "warnings": warnings,
    }


def build_from_paths(paths, include_cash=False, positions_paths=None):
    txns, n_unique, notes = union_files(paths)
    snapshot = load_newest_snapshot(positions_paths or [], notes)
    h = compute_holdings(txns, n_unique, len(paths), include_cash,
                         snapshot=snapshot)
    h["warnings"] = notes + h["warnings"]
    return h


def discover_csvs(directory):
    """All Accounts_History*.csv in a directory, stable order."""
    return sorted(glob.glob(os.path.join(directory, "Accounts_History*.csv")))


# ═══════════════════════════════════════════════════════════════════════════
#  Positions snapshot (Portfolio_Positions*.csv) — the authoritative "what do
#  I own right now" source. Transaction history only reaches back to the
#  earliest export row; shares bought before that and untouched since leave
#  NO trace in history, so a snapshot is the only way to get true totals.
# ═══════════════════════════════════════════════════════════════════════════

def discover_positions(directory):
    """All Portfolio_Positions*.csv in a directory (Fidelity's positions-
    download default name), stable order."""
    return sorted(glob.glob(os.path.join(directory, "Portfolio_Positions*.csv")))


def _snapshot_asof(path, raw_rows=None):
    """As-of date: the file's own 'Date downloaded Jul-06-2026 …' footer wins,
    then the filename (…_Jul-06-2026.csv or …_Jul062026.csv), then mtime."""
    for row in (raw_rows or []):
        for c in row:
            m = re.search(r"Date downloaded\s+([A-Za-z]{3})-?(\d{2})-?(\d{4})",
                          str(c))
            if m:
                try:
                    return dt.datetime.strptime("-".join(m.groups()),
                                                "%b-%d-%Y").date()
                except ValueError:
                    pass
    m = re.search(r"([A-Za-z]{3})-?(\d{2})-?(\d{4})", os.path.basename(path))
    if m:
        try:
            return dt.datetime.strptime("-".join(m.groups()), "%b-%d-%Y").date()
        except ValueError:
            pass
    try:
        return dt.date.fromtimestamp(os.stat(path).st_mtime)
    except OSError:
        return None


def parse_positions_file(path):
    """One Fidelity positions CSV -> (asof_date|None, rows).
    rows: {acct, acct_name, symbol, desc, qty, basis|None, is_option}.
    Raises ValueError on the known misaligned dialect (quoted header +
    trailing commas shift every value one column left) — mirrors the
    fail-loud gate in trade_journal._parse_positions; never fabricates."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        raw = list(csv.reader(f))
    header_idx = None
    for i, r in enumerate(raw):
        cells = [str(c).strip().lower() for c in r]
        if any(c == "symbol" for c in cells) and any("quantity" in c for c in cells) \
                and not any(c == "run date" for c in cells):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("no positions header (Symbol/Quantity) found")
    cols = [re.sub(r"\W+", "_", str(c).strip().lower()).strip("_")
            for c in raw[header_idx]]

    def find(*kws):
        for j, c in enumerate(cols):
            if all(k in c for k in kws):
                return j
        return None

    i_sym = find("symbol")
    i_qty = find("quantity")
    i_cost = find("cost", "basis", "total")
    if i_cost is None:
        i_cost = find("cost", "total")
    i_desc = find("description")
    i_num = find("account", "number")
    i_name = find("account", "name")
    if i_name is None:
        i_name = next((j for j, c in enumerate(cols)
                       if "account" in c and j != i_num), None)
    i_type = find("type")

    def cell(r, j):
        return str(r[j]).strip() if (j is not None and j < len(r)) else ""

    # misalignment gate: quantity cells must read like counts, not $/%.
    seen = ok = 0
    for r in raw[header_idx + 1:]:
        q = cell(r, i_qty)
        if q in ("", "--", "n/a", "N/A"):
            continue
        seen += 1
        if "$" not in q and "%" not in q:
            try:
                float(q.replace(",", ""))
                ok += 1
            except ValueError:
                pass
    if seen and ok / seen < 0.8:
        raise ValueError(
            f"positions CSV {os.path.basename(path)} looks column-shifted "
            f"(quantity column holds currency/percent values) — refusing to "
            f"parse rather than fabricate; re-export it from Fidelity")

    rows = []
    for r in raw[header_idx + 1:]:
        sym = cell(r, i_sym).upper().rstrip("*")
        if (not sym or sym.startswith("--") or "PENDING" in sym
                or "TOTAL" in sym or " " in sym.strip()):
            continue
        qty = _num(cell(r, i_qty))
        if abs(qty) < QTY_EPS:
            continue
        basis_raw = cell(r, i_cost)
        basis = abs(_num(basis_raw)) if basis_raw not in ("", "--", "n/a", "N/A") else None
        desc = cell(r, i_desc)
        a_type = cell(r, i_type).upper()
        is_opt = bool(OPTION_SYMBOL_RE.match(sym)) or "OPTION" in a_type \
            or any(k in desc.upper() for k in ("CALL", "PUT"))
        rows.append({
            "acct": cell(r, i_num) or "?",
            "acct_name": cell(r, i_name),
            "symbol": sym,
            "desc": desc,
            "qty": qty,
            "basis": basis,
            "is_option": is_opt,
        })
    return _snapshot_asof(path, raw), rows


def load_newest_snapshot(paths, notes):
    """Parse every positions CSV, keep the newest by as-of date.
    Unparseable files degrade to a note, never an exception."""
    best = None
    for p in paths:
        try:
            asof, rows = parse_positions_file(p)
        except (ValueError, OSError, UnicodeDecodeError, csv.Error) as e:
            notes.append(f"positions snapshot {os.path.basename(p)} unusable: {e}")
            continue
        if not rows:
            continue
        key = asof or dt.date.min
        if best is None or key > best[0]:
            best = (key, {"asof": asof.isoformat() if asof else None,
                          "file": os.path.basename(p), "rows": rows})
    return best[1] if best else None


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
#  Self-test — synthetic rows, zero network access (case 8 round-trips two
#  tiny CSVs through a private temp dir; everything else is in-memory)
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

    # 2b. FIFO is by ACQUISITION DATE, not insertion: a transferred-in lot
    #     older than the destination's own lot is disposed first.
    txns = [
        _tx("2026-01-01", "BUY", "TSLA", "AAA1111", 1, 100.00, -100.00),
        _tx("2026-02-01", "BUY", "TSLA", "BBB2222", 1, 300.00, -300.00),
        _tx("2026-03-01", "TRANSFER", "TSLA", "AAA1111", -1, hint="BBB22221"),
        _tx("2026-03-01", "TRANSFER", "TSLA", "BBB2222", 1),
        _tx("2026-04-01", "SELL", "TSLA", "BBB2222", -1, 350.00, 350.00),
    ]
    h = compute_holdings(txns, len(txns), 1)
    s = h["stocks"][0]
    # the Jan-acquired $100 lot sells; the Feb $300 lot remains
    assert abs(s["shares"] - 1) < 1e-9 and abs(s["basis"] - 300.00) < 0.01, s

    # 2c. _txn_key keeps the destination hint: same-day equal-qty transfers
    #     to different destinations must stay distinct rows.
    ta = _tx("2026-01-05", "TRANSFER", "TSLA", "AAA1111", -5, hint="BBB22221")
    tb = _tx("2026-01-05", "TRANSFER", "TSLA", "AAA1111", -5, hint="CCC33331")
    assert _txn_key(ta) != _txn_key(tb)

    # 3. Reinvestment adds fractional shares at |Amount| basis.
    txns = [_tx("2026-03-31", "REINVEST", "PLTU", "X17212229", 0.114, 41.34, -4.71)]
    h = compute_holdings(txns, 1, 1)
    s = h["stocks"][0]
    assert abs(s["shares"] - 0.114) < 1e-9 and abs(s["basis"] - 4.71) < 0.01

    # 4. Oversell (pre-window buy invisible) -> flagged, net count honest,
    #    and the row is self-describing about what basis/bps cover.
    txns = [
        _tx("2026-02-01", "BUY", "NVDA", "235498151", 1, 100, -100, name="ROTH IRA"),
        _tx("2026-02-10", "SELL", "NVDA", "235498151", -3, 110, 330, name="ROTH IRA"),
    ]
    h = compute_holdings(txns, 2, 1)
    s = h["stocks"][0]
    assert s["shares"] == -2 and s.get("flags"), s
    assert s.get("uncovered") == 2 and s.get("lotShares") == 0, s
    assert h["warnings"], h["warnings"]

    # 4b. Action classification is anchored — security NAMES containing
    #     SELL/BUY substrings must not flip the kind.
    assert classify_action("REINVESTMENT ISHARES RUSSELL 2000 ETF (IWM) (Cash)") == "REINVEST"
    assert classify_action("TRANSFERRED FROM VS X17-212229-1 BEST BUY CO INC (BBY) (Cash)") == "TRANSFER"
    assert classify_action("YOU SOLD BEST BUY CO INC (BBY) (Cash)") == "SELL"
    assert classify_action("YOU BOUGHT ASSIGNED PUTS AS OF 01/30/26 BITMINE (BMNR) (Cash)") == "BUY"
    assert classify_action("ASSIGNED as of Jan-30-2026 PUT (BMNR) ...") == "ASSIGN"
    assert classify_action("DIVIDEND RECEIVED NVIDIA CORPORATION COM (NVDA) (Cash)") is None

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
    #     contract is fabricated, and the flag surfaces as a warning even
    #     though the book ends flat.
    txns = [_tx("2026-02-02", "ASSIGN", "-BMNR260130P26", "X17212229", 1)]
    h = compute_holdings(txns, 1, 1)
    opts = {o["t"]: o for o in h["options"]}
    row = opts.get("-BMNR260130P26")
    assert row is None or (abs(row["contracts"]) < 1e-9 and row.get("flags")), row
    assert any("-BMNR260130P26" in w for w in h["warnings"]), h["warnings"]

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
        txns, n_unique, notes = union_files([p1, p2])
        assert n_unique == 1 and len(txns) == 2 and not notes, (n_unique, len(txns))
        h = compute_holdings(txns, n_unique, 2)
        assert abs(h["stocks"][0]["shares"] - 2) < 1e-9

        # 8b. Ticker/CUSIP flip across exports dedups to ONE fill (real case:
        #     HON vs 438516106, description gains a trailing ' 1').
        hon_t = "06/16/2026,\"ROTH IRA\",\"235498151\",\"YOU BOUGHT HONEYWELL INTERNATIONAL INC COM USD1 (HON) (Cash)\",HON,\"HONEYWELL INTERNATIONAL INC COM USD1\",Cash,0,,USD,230.52,0.433,0,,,,-99.82,06/17/2026"
        hon_c = "06/16/2026,\"ROTH IRA\",\"235498151\",\"YOU BOUGHT HONEYWELL INTERNATIONAL INC COM USD1 (HON) (Cash)\",438516106,\"HONEYWELL INTERNATIONAL INC COM USD1 1\",Cash,0,,USD,230.52,0.433,0,,,,-99.82,06/17/2026"
        p3 = os.path.join(td, "Accounts_History (2).csv")
        p4 = os.path.join(td, "Accounts_History (3).csv")
        with open(p3, "w", encoding="utf-8") as f:
            f.write("\n\n" + hdr + "\n" + hon_t + "\n")
        with open(p4, "w", encoding="utf-8") as f:
            f.write("\n\n" + hdr + "\n" + hon_c + "\n")
        txns, n_unique, notes = union_files([p3, p4])
        assert n_unique == 1 and len(txns) == 1, (n_unique, len(txns))
        assert txns[0]["symbol"] == "HON"
        h = compute_holdings(txns, n_unique, 2)
        assert abs(h["stocks"][0]["shares"] - 0.433) < 1e-9

        # 8c. One unreadable file is skipped with a note; the rest still parse.
        p5 = os.path.join(td, "Accounts_History (4).csv")
        with open(p5, "wb") as f:
            f.write(b"\xc9\xc9 not utf-8 \xc9\n")
        txns, n_unique, notes = union_files([p1, p5])
        assert n_unique == 1 and len(txns) == 1
        assert notes and "skipped unreadable" in notes[0], notes

    # 10. Positions snapshot: parse (with $ values, ** suffix, Pending row),
    #     snapshot wins over history, mismatch quantified, engine-only kept.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        # dashless filename (real download) + trailing commas on data rows
        # (the dialect the desktop app refuses) + 'Date downloaded' footer
        # (wins over filename) + a CUSIP-only row with no basis at all.
        pp = os.path.join(td, "Portfolio_Positions_Jul052026.csv")
        with open(pp, "w", encoding="utf-8") as f:
            f.write(
                "Account Number,Account Name,Symbol,Description,Quantity,"
                "Last Price,Current Value,Cost Basis Total,Average Cost Basis,Type\n"
                'X17212229,Joint WROS - TOD,TSLA,TESLA INC COM,80,$400.00,'
                '"$32,000.00","$28,000.00",$350.00,Margin,\n'
                "235498151,ROTH IRA,TSLA,TESLA INC COM,23,$400.00,"
                '"$9,200.00","$8,100.00",$352.17,Cash,\n'
                "X83768586,Joint WROS - TOD,SPAXX**,FIDELITY GOVT MONEY MARKET,"
                "512.05,$1.00,$512.05,--,--,Cash,\n"
                "X17212229,Joint WROS - TOD,704916204,PEAKSOFT MULTINET CORP,"
                "138,--,--,--,--,Cash,\n"
                "Pending Activity,,,,,,$99.00,,,\n"
                ',,,"The data and information in this spreadsheet",,,,,,\n'
                '"Date downloaded Jul-06-2026 3:49 a.m ET"\n')
        asof, rows = parse_positions_file(pp)
        assert asof == dt.date(2026, 7, 6) and len(rows) == 4, (asof, rows)
        assert rows[0]["qty"] == 80 and rows[0]["basis"] == 28000.00
        assert rows[2]["symbol"] == "SPAXX" and rows[2]["basis"] is None
        assert rows[3]["symbol"] == "704916204" and rows[3]["basis"] is None

        snap = {"asof": "2026-07-06", "file": os.path.basename(pp), "rows": rows}
        txns = [_tx("2026-03-01", "BUY", "TSLA", "X17212229", 20, 400, -8000.00)]
        h = compute_holdings(txns, 1, 1, snapshot=snap)
        s = h["stocks"][0]
        assert s["t"] == "TSLA" and abs(s["shares"] - 103) < 1e-9, s
        assert abs(s["basis"] - 36100.00) < 0.01 and len(s["accounts"]) == 2
        assert any("covers 20" in f and "103" in f for f in s["flags"]), s["flags"]
        assert h["source"] == "snapshot+history" and h["snapshotAsOf"] == "2026-07-06"
        assert not any("NOT included" in w for w in h["warnings"])

        # engine-only symbol (not in snapshot) is kept and flagged
        txns.append(_tx("2026-07-01", "BUY", "MU", "X17212229", 2, 100, -200.00))
        h = compute_holdings(txns, 2, 1, snapshot=snap)
        mu = [x for x in h["stocks"] if x["t"] == "MU"][0]
        assert abs(mu["shares"] - 2) < 1e-9
        assert any("not in the positions snapshot" in f for f in mu["flags"])

        # misaligned (column-shifted) snapshot is refused, not fabricated
        bad = os.path.join(td, "Portfolio_Positions_bad.csv")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("Account Number,Account Name,Symbol,Description,Quantity,"
                    "Last Price,Cost Basis Total\n"
                    "X1,Joint,TSLA,TESLA INC COM,$400.00,$32000.00,$28000.00\n")
        try:
            parse_positions_file(bad)
            raise AssertionError("misaligned snapshot was NOT refused")
        except ValueError:
            pass
        notes = []
        assert load_newest_snapshot([bad], notes) is None and notes

    # 10b. History-only mode states its blind spot explicitly.
    h = compute_holdings([_tx("2026-03-01", "BUY", "TSLA", "X1", 1, 400, -400)], 1, 1)
    assert any("NOT included" in w for w in h["warnings"]), h["warnings"]

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
          "oversell flag, money-market filter, options, dedup, positions "
          "snapshot (parse/merge/misalignment-refusal), consistency")
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
    ap.add_argument("--positions", action="append", default=None,
                    help="a Fidelity Portfolio_Positions CSV — authoritative "
                         "current counts/basis (repeatable; newest wins)")
    ap.add_argument("--dir", default=None,
                    help="directory to scan for Accounts_History*.csv and "
                         "Portfolio_Positions*.csv "
                         "(default: the journal app's last CSV's folder)")
    ap.add_argument("--symbol", default=None, help="only this symbol")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--simple", action="store_true",
                    help="one line per stock: ticker, shares, avg cost basis "
                         "— no per-account breakdown, no options, no warnings")
    ap.add_argument("--include-cash", action="store_true",
                    help="include money-market/core positions (SPAXX, ...)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    paths = list(args.csv or [])
    ppaths = list(args.positions or [])
    scan_dirs = [args.dir] if args.dir else []
    if not paths and not scan_dirs:
        d = default_csv_dir()
        if d:
            scan_dirs = [d]
    for d in scan_dirs:
        paths += discover_csvs(d)
        ppaths += discover_positions(d)
    paths = [p for p in dict.fromkeys(paths) if os.path.isfile(p)]
    ppaths = [p for p in dict.fromkeys(ppaths) if os.path.isfile(p)]
    if not paths and not ppaths:
        print("ERROR: no CSVs found — pass --csv FILE or --dir DIR", file=sys.stderr)
        return 1

    h = build_from_paths(paths, include_cash=args.include_cash,
                         positions_paths=ppaths)
    if args.symbol:
        want = args.symbol.upper()
        h["stocks"] = [s for s in h["stocks"] if s["t"] == want]
        # options match on the exact UNDERLYING (substring would make
        # --symbol ON pull in -NOW... and -SNOW... contracts)
        h["options"] = [o for o in h["options"]
                        if (m := OPTION_SYMBOL_RE.match(o["t"])) and m.group(1) == want]
    if args.json:
        print(json.dumps(h, indent=1))
        return 0

    if args.simple:
        # ticker, shares, avg cost basis — one line each, nothing else
        for s in h["stocks"]:
            bps = f"${s['bps']:,.2f}" if s["bps"] is not None else "—"
            print(f"{s['t']:<8} {_fmt_shares(s['shares']):>10} sh   {bps}/sh")
        return 0

    snap = (f", positions snapshot {h['snapshotAsOf'] or 'undated'} "
            f"({h['snapshotFile']})" if h.get("snapshotFile") else "")
    print(f"Holdings across accounts — {h['files']} history file(s), "
          f"{h['rowsUnique']} unique rows, window {h['windowStart']} → "
          f"{h['windowEnd']}{snap}")
    print("=" * 78)
    if not h["stocks"]:
        print("(no open stock positions in the data window)")
    def _money(v, width=12):
        return f"${v:>{width},.2f}" if v is not None else f"{'—':>{width + 1}}"

    for s in h["stocks"]:
        bps = f"${s['bps']:,.2f}" if s["bps"] is not None else "—"
        flag = "  ⚠" if s.get("flags") else ""
        print(f"{s['t']:<10} {_fmt_shares(s['shares']):>12} sh   "
              f"adj basis {_money(s['basis'])}   {bps}/sh{flag}")
        for a in s["accounts"]:
            abps = f"${a['bps']:,.2f}/sh" if a["bps"] is not None else "—"
            print(f"    {a['a']:<20} {_fmt_shares(a['shares']):>12} sh   "
                  f"{_money(a['basis'])}   {abps}")
        for fl in s.get("flags", []):
            print(f"    ⚠ {fl}")
    if h["options"]:
        print("-" * 78)
        print("Options (contracts, tracked separately):")
        for o in h["options"]:
            print(f"{o['t']:<18} {o['contracts']:>6.0f} ct   "
                  f"basis {_money(o['basis'], 10)}   {o.get('label', '')}")
            for fl in o.get("flags", []):
                print(f"    ⚠ {fl}")
    for w in h["warnings"]:
        print(f"⚠ {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
