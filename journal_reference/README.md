# Trade Journal — reference copy

Read-only reference snapshot of Michael's desktop trade journal app, published
so another Claude session can understand its dashboard layout and data
formats without needing filesystem access to his machine.

**Canonical live location** (this is a copy, not the source of truth):
`~/Downloads/trade_journal.py` + `~/Downloads/swing_flag.py`, mirrored hourly
into `~/trading-src/journal/` by `~/sync-trading-src.sh`.

## Files

| File | What it is |
|---|---|
| `trade_journal.py` | The app itself. Tkinter desktop GUI (`TradeJournalApp` class), ~2070 lines. Parses Fidelity CSV exports, FIFO-matches buys/sells into closed trades, tags each trade with a strategy, and renders a dashboard. |
| `swing_flag.py` | Helper module/CLI. Classifies open positions into WATCH/ROT/HOLD flags from the −8% worst-since-entry cliff and 22-day rotation rules. Imported by the swing/journal dashboard tooling; also invoked as a standalone script by `trade_journal.py` on window close. |
| `closed_trades_sample.csv` | 3 real closed round-trip trades (6 rows: matching buy + sell), in the exact raw Fidelity trade-history CSV format the app parses. Account Number column redacted. |

## Dashboard / summary view

`TradeJournalApp` renders two dashboard sections in a right-hand panel
(`trade_journal.py`):

- **Summary cards** — `_build_summary_cards` (~L1029), populated by
  `_refresh_summary` (~L1543). Four cards: **Total P/L** (dollar + %, on cost
  basis of non-excluded closed trades), **Win Rate** (win count / loss count),
  **Swing Trader P/L**, **LEAP Strategy P/L** (per-method dollar P/L + trade
  count + win rate). The two strategy cards also show a capital-efficiency
  sub-block: average deployed capital, turnover multiple, and a monthly-
  equivalent return. The Swing card additionally carries a ✓/✗ badge against
  a 3%/month target, measured against a user-editable "swing program capital"
  allocation (persisted to config).
- **Charts** — `_build_chart_slots` (~L1104), a 5-subplot matplotlib figure
  (`FigureCanvasTkAgg` embedded in Tk): P/L-by-strategy pie, a bar chart, a
  daily P/L chart, a rolling win-rate chart, and cumulative P/L. Toggle
  between "Closed Only" and "All (incl Open)".
- A third area (not strictly "dashboard") is the sortable trade table with a
  per-row Strategy dropdown (Swing Trader / LEAP Strategy / Excluded), and a
  toolbar with **Load CSV**, **Export Open**, and drag-and-drop CSV support.

No screenshot is included — attempting to render the live GUI (Tk +
matplotlib, `FigureCanvasTkAgg`) against this machine's real X display for a
screenshot caused the process to exit within ~1–2 seconds with no Python
traceback (consistent with a Tk/matplotlib rendering crash, not a data
issue), and repeated attempts against the user's live desktop weren't worth
pursuing further for what the task treated as an optional step. The section
above plus the source itself should be enough to reconstruct the layout.

## Data files read/written

| Path | Format | Direction |
|---|---|---|
| User-selected Fidelity CSV (path remembered, not fixed) | Either a **trade-history** export (`Run Date, Account, Account Number, Action, Symbol, Description, Type, Price, Quantity, Commission, Fees, Amount, Settlement Date` — header auto-detected by scanning for `run date`+`action`+`symbol`) or a **positions snapshot** export (`Symbol, Quantity, Last Price, Cost Basis, Average Cost Basis, Total Gain/Loss...`). See `closed_trades_sample.csv` for the trade-history shape. | Read |
| `~/.trade_journal_config.json` | JSON: `{"last_csv_path": ..., "swing_allocation": ...}` | Read/write |
| `~/.trade_journal_tags.db` | SQLite, single table `tags(trade_key PRIMARY KEY, method)` — user's strategy tag per closed/open leg, keyed by a derived `trade_key` (e.g. `SYMBOL-YYYYMMDD-YYYYMMDD-QTY`). | Read/write |
| `~/Desktop/swing_project/open_positions_export.csv` | CSV, columns exactly `Ticker, Buy Date, Quantity, Buy Price, Buy Cost, Strategy, Hold Days`. Written automatically on every CSV load (silent) and via the toolbar's **Export Open** button (with a confirmation dialog). This is the swing dashboard's position-flagging input. | Write |
| `~/Downloads/swing_flag.py` (hardcoded path, invoked as a subprocess) | N/A — re-runs flag classification against the export above and regenerates `~/Desktop/swing_project/swing_flags.html` | Side effect, on app close |

## In-memory closed-trade record shape

Produced by `fifo_match()` (FIFO buy/sell matching per symbol) — this is the
record type the summary cards and charts are computed from:

```
{
  "ticker": "COP", "ticker_label": "COP",
  "buy_date": datetime(...), "sell_date": datetime(...),
  "qty": 1.0, "qty_str": "1 sh",
  "buy_price": 122.77, "sell_price": 114.23,
  "buy_cost": 122.77, "sell_proceeds": 114.23,
  "commission": 0.0,
  "pl_dollar": -8.54, "pl_pct": -6.96,
  "hold_days": 4,
  "is_option": false, "description": "CONOCOPHILLIPS COM",
  "trade_key": "COP-20260413-20260417-1.0000",
  "is_open": false, "from_assignment": false,
  "method": "Swing Trader"   # added post-hoc from the tags DB / default_method_for()
}
```

Open positions and orphan sells (a SELL with no matching BUY in the loaded
CSV) share most of the same fields; `open_positions` has `sell_date: None`,
`is_open: True`; orphans have `is_orphan: True`.
