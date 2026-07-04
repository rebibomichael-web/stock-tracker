"""
Michael Swing Trader v1.2.0 — Data Layer Module
================================================
Handles all persistent storage: state, history, outcome analysis.
3-file architecture with monthly scan archival.

Files managed (in ~/.michael_swing_trader/):
  state.json            — config + active trades + scan metadata
  history.json          — closed trades + scan summaries (30d) + fire alerts
  outcome_analysis.json — derived stats, recomputed nightly if dirty
  scan_archive.YYYY-MM.gz — compressed old scans
  migrations.log        — text audit trail

Usage:
  dm = DataManager()
  dm.log_trade("PANW", recommendation, entries=[{"price": 152.3, "shares": 100}])
  dm.close_trade("PANW_20260405_001", exits=[{"price": 155.8, "shares": 100, "reason": "1R"}])
  dm.save_scan_summary(results, market_ctx)
  dm.log_fire_alert(alert_data)
  dm.recompute_if_dirty()
"""

import json
import gzip
import os
import time
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import threading

# --- Constants ---

CURRENT_SCHEMA = "1.2.0"
DATA_DIR = os.path.expanduser("~/.michael_swing_trader")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
OUTCOME_FILE = os.path.join(DATA_DIR, "outcome_analysis.json")
MIGRATION_LOG = os.path.join(DATA_DIR, "migrations.log")

DEFAULT_CONFIG = {
    "min_buy_score": 70,
    "min_sell_score": 72,
    "min_conditions": 7,
    "min_atr_swing_pct": 4.0,
    "fire_threshold": 90,
    "base_score_cap": 75,
    "max_bonus": 25,
    "suppress_below_winrate": 0.40,
    "flag_above_winrate": 0.70,
    "min_combo_samples": 5,
    "sl_tp_monitor_interval_sec": 30,
    "sl_tp_monitor_enabled": False,
    "sl_tp_monitor_end_et": "18:30",
}

TOP_N_SIGNALS_PER_SCAN = 20
SCAN_RETENTION_DAYS = 30


# --- Helpers ---

def _now_utc():
    """Return current UTC time as naive datetime (for internal comparisons)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_iso():
    """Return current UTC time as ISO string with Z suffix."""
    return _now_utc().isoformat(timespec="seconds") + "Z"


def _normalize_combo_key(conditions):
    """Canonical sorted list from any input (list or '+'-joined string)."""
    if isinstance(conditions, str):
        conditions = [c.strip() for c in conditions.split("+")]
    return sorted(set(conditions))


def _combo_key_str(combo_list):
    """Deterministic string for dict keys from sorted list."""
    return "+".join(combo_list)


def _weighted_avg_price(entries):
    """Compute weighted average entry price from fills."""
    total_cost = sum(e["price"] * e["shares"] for e in entries)
    total_shares = sum(e["shares"] for e in entries)
    if total_shares == 0:
        return 0.0
    return round(total_cost / total_shares, 4)


# --- Migration ---

def _migrate(data, filepath):
    """Migrate data to current schema version. Creates backup first."""
    v = data.get("schema_version", "1.0.0")
    if v == CURRENT_SCHEMA:
        return data

    # Backup
    backup = f"{filepath}.backup_{v}_{int(time.time())}"
    try:
        with open(backup, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # Best effort backup

    # 1.0.0 → 1.1.0: convert flat entry fields to entries[]
    if v < "1.1.0":
        for t in data.get("trades", data.get("active_trades", [])):
            if "entry_price" in t and "entries" not in t:
                t["entries"] = [{
                    "price": t.pop("entry_price"),
                    "shares": t.pop("entry_shares", 0),
                    "timestamp": t.pop("entry_timestamp", ""),
                }]
                t["avg_entry_price"] = t["entries"][0]["price"]
                t["total_shares"] = t["entries"][0]["shares"]
                t["total_amount_usd"] = round(
                    t["avg_entry_price"] * t["total_shares"], 2
                )
            # Normalize combo_key from string to sorted list
            rec = t.get("recommendation", {})
            ck = rec.get("combo_key")
            if isinstance(ck, str):
                rec["combo_key"] = _normalize_combo_key(ck)

    # 1.1.0 → 1.2.0: add new config fields, dirty flag
    if v < "1.2.0":
        data.setdefault("outcome_dirty", False)
        cfg = data.setdefault("config", {})
        for k, default_v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, default_v)

    data["schema_version"] = CURRENT_SCHEMA

    # Log migration
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MIGRATION_LOG, "a") as f:
            f.write(f"{_now_iso()} | {os.path.basename(filepath)} | {v} → {CURRENT_SCHEMA}\n")
    except OSError:
        pass

    return data


# --- File I/O ---

def _load_json(filepath, default_factory):
    """Load JSON file, migrate if needed, return data."""
    if not os.path.exists(filepath):
        data = default_factory()
        data["schema_version"] = CURRENT_SCHEMA
        return data
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        return _migrate(data, filepath)
    except (json.JSONDecodeError, OSError):
        # Corrupted — try backup
        backup = filepath + ".backup"
        if os.path.exists(backup):
            with open(backup, "r") as f:
                data = json.load(f)
            return _migrate(data, filepath)
        return default_factory()


def _save_json(filepath, data):
    """Atomic write: write to temp, then rename."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp = filepath + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    # Keep one rolling backup
    if os.path.exists(filepath):
        shutil.copy2(filepath, filepath + ".backup")
    os.replace(tmp, filepath)


# --- Default Schemas ---

def _default_state():
    return {
        "schema_version": CURRENT_SCHEMA,
        "config": dict(DEFAULT_CONFIG),
        "outcome_dirty": False,
        "last_scan_metadata": {},
        "active_trades": [],
    }


def _default_history():
    return {
        "schema_version": CURRENT_SCHEMA,
        "closed_trades": [],
        "scan_summaries": [],
        "fire_alerts": [],
    }


def _default_outcome():
    return {
        "schema_version": CURRENT_SCHEMA,
        "last_recomputed": None,
        "portfolio_summary": {
            "all_time": _empty_portfolio_stats(),
            "last_30_days": _empty_portfolio_stats(),
            "last_90_days": _empty_portfolio_stats(),
        },
        "by_sector": {},
        "by_stock": {},
        "by_combo": {},
        "precomputed_90day_rollup": {},
        "learning_insights": {
            "top_performers": [],
            "avoid_patterns": [],
            "recommendations": [],
        },
    }


def _empty_portfolio_stats():
    return {
        "wins": 0, "losses": 0, "breakeven": 0,
        "win_rate": 0.0, "total_pnl_usd": 0.0,
        "profit_factor": 0.0, "avg_hold_days": 0.0,
    }


# --- DataManager ---

class DataManager:
    """Thread-safe manager for all v1.2.0 persistent data."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._lock = threading.Lock()
        self.state = _load_json(STATE_FILE, _default_state)
        self.history = _load_json(HISTORY_FILE, _default_history)
        self.outcome = _load_json(OUTCOME_FILE, _default_outcome)

    @property
    def config(self):
        return self.state.get("config", DEFAULT_CONFIG)

    # --- Save helpers ---

    def save_state(self):
        with self._lock:
            _save_json(STATE_FILE, self.state)

    def save_history(self):
        with self._lock:
            _save_json(HISTORY_FILE, self.history)

    def save_outcome(self):
        with self._lock:
            _save_json(OUTCOME_FILE, self.outcome)

    # --- Trade Logging ---

    def log_trade(self, stock, recommendation, entries, sector="Unknown",
                  suggested_exits=None):
        """
        Log a new trade to active_trades.

        Args:
            stock: ticker symbol
            recommendation: dict with signal_date, score, score_breakdown,
                           primary_condition, conditions_met, combo_key
            entries: list of {"price": float, "shares": int, "timestamp": str}
            sector: sector string
            suggested_exits: dict with stop_loss_atr, take_profit_1r, etc.
        """
        # Normalize combo key
        rec = dict(recommendation)
        rec["combo_key"] = _normalize_combo_key(rec.get("combo_key", []))
        rec.setdefault("suppressed", False)

        # Build trade record
        trade_id = f"{stock}_{_now_utc().strftime('%Y%m%d')}_{self._next_trade_seq(stock)}"
        avg_price = _weighted_avg_price(entries)
        total_shares = sum(e["shares"] for e in entries)

        trade = {
            "trade_id": trade_id,
            "stock": stock,
            "sector": sector,
            "recommendation": rec,
            "entries": entries,
            "avg_entry_price": avg_price,
            "total_shares": total_shares,
            "total_amount_usd": round(avg_price * total_shares, 2),
            "suggested_exits": suggested_exits or {},
            "edits": [],
        }

        self.state["active_trades"].append(trade)
        self.save_state()
        return trade_id

    def add_fill(self, trade_id, price, shares, timestamp=None):
        """Add another fill to an existing active trade (scaling in)."""
        trade = self._find_active_trade(trade_id)
        if not trade:
            return False
        trade["entries"].append({
            "price": price,
            "shares": shares,
            "timestamp": timestamp or _now_iso(),
        })
        trade["avg_entry_price"] = _weighted_avg_price(trade["entries"])
        trade["total_shares"] = sum(e["shares"] for e in trade["entries"])
        trade["total_amount_usd"] = round(
            trade["avg_entry_price"] * trade["total_shares"], 2
        )
        self.save_state()
        return True

    def update_active_trade_excursion(self, trade_id, current_price, current_iso=None):
        """
        Update MFE/MAE on an active trade from a live price quote.

        Called from _monitor_loop (swing_trader.py) per active trade per tick.
        MFE/MAE source: poll-price (NOT bar high/low). Known limitation
        documented in CHANGE_LIST_CONSOLIDATED.md Item E0-live: underestimates
        MFE and overestimates MAE relative to true intraday extremes.

        Args:
            trade_id: active trade ID
            current_price: latest poll quote (float)
            current_iso: ISO timestamp string; defaults to _now_iso()
        Returns:
            True if updated, False if trade not found
        """
        trade = self._find_active_trade(trade_id)
        if not trade:
            return False
        if current_price is None or current_price <= 0:
            return False

        ts = current_iso or _now_iso()
        try:
            ny_date = datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
        except (ValueError, AttributeError):
            ny_date = ts[:10] if len(ts) >= 10 else ""

        avg_entry = trade.get("avg_entry_price", 0)
        if avg_entry <= 0:
            return False
        pct = (current_price - avg_entry) / avg_entry * 100

        ex = trade.setdefault("excursion", {
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "mfe_bar_iso": None,
            "mae_bar_iso": None,
            "mfe_last_date": None,
            "mae_last_date": None,
            "same_bar_extreme_count": 0,
            "same_bar_extreme_bars": [],
            "mfe_source": "poll_price",
        })

        mfe_updated = False
        mae_updated = False

        if pct > ex["mfe_pct"]:
            ex["mfe_pct"] = round(pct, 4)
            ex["mfe_bar_iso"] = ts
            ex["mfe_last_date"] = ny_date
            mfe_updated = True

        if pct < ex["mae_pct"]:
            ex["mae_pct"] = round(pct, 4)
            ex["mae_bar_iso"] = ts
            ex["mae_last_date"] = ny_date
            mae_updated = True

        if mfe_updated and mae_updated:
            ex["same_bar_extreme_count"] += 1
            ex["same_bar_extreme_bars"].append({
                "bar_iso": ny_date,
                "bar_mfe_pct": ex["mfe_pct"],
                "bar_mae_pct": ex["mae_pct"],
            })
        elif (mfe_updated and ex["mae_last_date"] == ny_date) or \
             (mae_updated and ex["mfe_last_date"] == ny_date):
            already_counted = (
                ex["same_bar_extreme_bars"] and
                ex["same_bar_extreme_bars"][-1].get("bar_iso") == ny_date
            )
            if not already_counted:
                ex["same_bar_extreme_count"] += 1
                ex["same_bar_extreme_bars"].append({
                    "bar_iso": ny_date,
                    "bar_mfe_pct": ex["mfe_pct"],
                    "bar_mae_pct": ex["mae_pct"],
                })

        if mfe_updated or mae_updated:
            self.save_state()

        return True

    def edit_trade(self, trade_id, field, new_value):
        """Edit a field on an active trade. Logs old→new for audit."""
        trade = self._find_active_trade(trade_id)
        if not trade:
            return False

        # Navigate dotted field paths like "entries.0.price"
        parts = field.split(".")
        obj = trade
        for p in parts[:-1]:
            if p.isdigit():
                obj = obj[int(p)]
            else:
                obj = obj[p]
        last_key = parts[-1]
        if last_key.isdigit():
            old_value = obj[int(last_key)]
            obj[int(last_key)] = new_value
        else:
            old_value = obj.get(last_key)
            obj[last_key] = new_value

        trade["edits"].append({
            "field": field,
            "old": old_value,
            "new": new_value,
            "timestamp": _now_iso(),
        })

        # Recalculate derived fields if entry changed
        if "entries" in field or "price" in field or "shares" in field:
            trade["avg_entry_price"] = _weighted_avg_price(trade["entries"])
            trade["total_shares"] = sum(e["shares"] for e in trade["entries"])
            trade["total_amount_usd"] = round(
                trade["avg_entry_price"] * trade["total_shares"], 2
            )

        self.save_state()
        return True

    def close_trade(self, trade_id, exits, telemetry=None):
        """
        Close (fully or partially) an active trade.

        Args:
            exits: list of {"price": float, "shares": int,
                            "timestamp": str, "reason": str}
            telemetry: optional dict of E0-live exit-side fields; passed
                       through to _full_close for sink emit (ignored on
                       partial close). None produces bit-identical behavior
                       to the pre-E0-live code path.
        Returns:
            "full" if fully closed, "partial" if shares remain, None if not found
        """
        trade = self._find_active_trade(trade_id)
        if not trade:
            return None

        exit_shares = sum(e["shares"] for e in exits)
        remaining = trade["total_shares"] - exit_shares

        if remaining <= 0:
            # Full close — move to history
            return self._full_close(trade, exits, telemetry)
        else:
            # Partial close — update active trade, log partial exit
            return self._partial_close(trade, exits, remaining)

    def _full_close(self, trade, exits, telemetry=None):
        """Move trade from active to closed, compute outcome."""
        avg_exit = _weighted_avg_price(exits)
        pnl = (avg_exit - trade["avg_entry_price"]) * trade["total_shares"]

        # Determine hold time
        first_entry_ts = trade["entries"][0].get("timestamp", "")
        last_exit_ts = exits[-1].get("timestamp", _now_iso())
        hold_days = self._calc_hold_days(first_entry_ts, last_exit_ts)

        # Determine outcome
        pnl_pct = (pnl / trade["total_amount_usd"]) * 100 if trade["total_amount_usd"] else 0
        if abs(pnl_pct) < 0.25:
            status = "breakeven"
        elif pnl > 0:
            status = "win"
        else:
            status = "loss"

        closed = dict(trade)  # shallow copy preserves suggested_exits from active trade record
        closed["exits"] = exits
        closed["outcome"] = {
            "status": status,
            "pnl_usd": round(pnl, 2),
            "pnl_percent": round(pnl_pct, 2),
            "hold_time_days": hold_days,
            "avg_exit_price": avg_exit,
        }

        # Remove from active, add to history
        self.state["active_trades"] = [
            t for t in self.state["active_trades"]
            if t["trade_id"] != trade["trade_id"]
        ]
        self.state["outcome_dirty"] = True
        self.history["closed_trades"].append(closed)

        self.save_state()
        self.save_history()

        # Phase E Item E0-live — emit to live sink if telemetry provided
        if telemetry is not None:
            try:
                rec = trade.get("recommendation", {})
                entry_ta = rec.get("ta_snapshot", {}) or {}
                entry_atr = entry_ta.get("ATR")
                entry_conds = rec.get("conditions_met", []) or []
                ex_block = trade.get("excursion") or {}
                first_entry_ts = trade["entries"][0].get("timestamp", "")

                sink_record = {
                    "symbol": trade["stock"],
                    "data_source": "live",
                    "trade_id": trade["trade_id"],
                    "entry_bar": first_entry_ts,
                    "entry_px": trade["avg_entry_price"],
                    "entry_atr": entry_atr,
                    "entry_conds": list(entry_conds),
                    "entry_ta_snapshot": dict(entry_ta),
                    "exit_bar": exits[-1].get("timestamp", _now_iso()),
                    "exit_price": avg_exit,
                    "exit_reason": telemetry.get("exit_reason"),
                    "exit_hold_bars": hold_days,
                    "exit_sell_score": telemetry.get("exit_sell_score"),
                    "exit_regime": telemetry.get("exit_regime"),
                    "exit_ta_snapshot": telemetry.get("exit_ta_snapshot") or {},
                    "pnl_pct": round(pnl_pct, 4),
                    "mfe_pct": ex_block.get("mfe_pct", 0.0),
                    "mae_pct": ex_block.get("mae_pct", 0.0),
                    "mfe_bar": ex_block.get("mfe_bar_iso"),
                    "mae_bar": ex_block.get("mae_bar_iso"),
                    "mfe_source": ex_block.get("mfe_source", "poll_price"),
                    "same_bar_extreme_count": ex_block.get("same_bar_extreme_count", 0),
                    "same_bar_extreme_bars": ex_block.get("same_bar_extreme_bars", []),
                    "condition_decay": telemetry.get("condition_decay") or {},
                }
                from swing_trader import _E0LiveSink
                _E0LiveSink().emit(sink_record)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"E0-live sink emit: {e}")

        return "full"

    def _partial_close(self, trade, exits, remaining_shares):
        """Log partial exit, reduce active position."""
        exit_shares = sum(e["shares"] for e in exits)

        # Store partial exit in a running list
        trade.setdefault("partial_exits", [])
        trade["partial_exits"].extend(exits)

        # Update position size
        trade["total_shares"] = remaining_shares
        trade["total_amount_usd"] = round(
            trade["avg_entry_price"] * remaining_shares, 2
        )
        self.save_state()
        return "partial"

    def get_active_trades(self):
        return self.state.get("active_trades", [])

    def get_closed_trades(self):
        return self.history.get("closed_trades", [])

    # --- Scan Summaries ---

    def save_scan_summary(self, results, market_ctx):
        """
        Save a scan summary with top N signals only.

        Args:
            results: list of scan result dicts (must have 'score', 'stock',
                     'combo_key', 'signal', 'suppressed', 'passes_gates')
            market_ctx: dict with spy_price, vix, regime
        """
        now = _now_utc()
        sorted_sigs = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

        summary = {
            "scan_id": f"SCAN_{now.strftime('%Y%m%d_%H%M%S')}",
            "timestamp": now.isoformat(timespec="seconds") + "Z",
            "watchlist_count": len(results),
            "signals_generated": len(results),
            "signals_passing": sum(1 for r in results if r.get("passes_gates")),
            "signals_suppressed": sum(1 for r in results if r.get("suppressed")),
            "high_scores_80plus": sum(1 for r in results if r.get("score", 0) >= 80),
            "top_20_signals": [
                {
                    "stock": r.get("stock"),
                    "score": r.get("score", 0),
                    "combo_key": _normalize_combo_key(r.get("combo_key", [])),
                    "signal": r.get("signal", "WATCH"),
                    "suppressed": r.get("suppressed", False),
                }
                for r in sorted_sigs[:TOP_N_SIGNALS_PER_SCAN]
            ],
            "market_context": market_ctx,
        }

        self.history["scan_summaries"].append(summary)

        # Update last scan metadata in state
        passing = summary["signals_passing"]
        self.state["last_scan_metadata"] = {
            "timestamp": summary["timestamp"],
            "display": self._friendly_time(now),
            "minutes_ago": 0,
            "signals_count": summary["signals_generated"],
            "signals_passing": passing,
            "signals_suppressed": summary["signals_suppressed"],
            "watchlist_count": summary["watchlist_count"],
        }

        self.save_state()
        self.save_history()

        # Prune old scans beyond retention
        self._prune_old_scans()

    def _prune_old_scans(self):
        """Move scans older than SCAN_RETENTION_DAYS to monthly .gz archive."""
        cutoff = _now_utc() - timedelta(days=SCAN_RETENTION_DAYS)
        keep = []
        archive_bins = {}  # "YYYY-MM" → list of scans

        for scan in self.history.get("scan_summaries", []):
            ts = scan.get("timestamp", "")
            try:
                scan_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                keep.append(scan)
                continue

            if scan_dt < cutoff:
                month_key = scan_dt.strftime("%Y-%m")
                archive_bins.setdefault(month_key, []).append(scan)
            else:
                keep.append(scan)

        if not archive_bins:
            return  # Nothing to archive

        # Write each month's scans to compressed archive
        for month_key, scans in archive_bins.items():
            archive_path = os.path.join(DATA_DIR, f"scan_archive.{month_key}.gz")
            existing = []
            if os.path.exists(archive_path):
                try:
                    with gzip.open(archive_path, "rt") as f:
                        existing = json.load(f)
                except (json.JSONDecodeError, OSError):
                    existing = []

            existing.extend(scans)
            with gzip.open(archive_path, "wt") as f:
                json.dump(existing, f)

        self.history["scan_summaries"] = keep
        self.save_history()

    def load_archived_scans(self, year_month):
        """Load compressed scans for a specific month (e.g. '2026-03')."""
        archive_path = os.path.join(DATA_DIR, f"scan_archive.{year_month}.gz")
        if not os.path.exists(archive_path):
            return []
        try:
            with gzip.open(archive_path, "rt") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    # --- Fire Alerts ---

    def log_fire_alert(self, stock, alert_type, alert_details,
                       triggered_signal=None, trade_id=None,
                       current_state=None):
        """Log a fire alert and return alert_id."""
        now = _now_utc()
        alert_id = f"ALERT_{now.strftime('%Y%m%d_%H%M%S')}_{stock}_{alert_type[:2].upper()}"

        alert = {
            "alert_id": alert_id,
            "alert_type": alert_type,
            "timestamp": now.isoformat(timespec="seconds") + "Z",
            "stock": stock,
            "triggered_signal": triggered_signal or {},
            "trade_id": trade_id,
            "alert_details": alert_details,
            "current_state": current_state or {},
            "user_action": "pending",
        }

        self.history["fire_alerts"].append(alert)
        self.save_history()
        return alert_id

    def update_alert_action(self, alert_id, action):
        """Update user action on a fire alert (dismissed, logged_trade, etc.)."""
        for alert in self.history.get("fire_alerts", []):
            if alert["alert_id"] == alert_id:
                alert["user_action"] = action
                self.save_history()
                return True
        return False

    def get_fire_alerts(self, stock=None, pending_only=False):
        """Get fire alerts, optionally filtered."""
        alerts = self.history.get("fire_alerts", [])
        if stock:
            alerts = [a for a in alerts if a["stock"] == stock]
        if pending_only:
            alerts = [a for a in alerts if a["user_action"] == "pending"]
        return alerts

    # --- Outcome Analysis ---

    def recompute_if_dirty(self):
        """Recompute outcome_analysis.json only if trades have changed."""
        if not self.state.get("outcome_dirty", False):
            return False

        self._recompute_outcome()
        self.state["outcome_dirty"] = False
        self.save_state()
        return True

    def force_recompute(self):
        """Force recompute regardless of dirty flag."""
        self._recompute_outcome()
        self.state["outcome_dirty"] = False
        self.save_state()

    def _recompute_outcome(self):
        """Rebuild outcome_analysis.json from closed trades."""
        trades = self.history.get("closed_trades", [])
        config = self.config

        outcome = _default_outcome()
        outcome["last_recomputed"] = _now_iso()

        if not trades:
            self.outcome = outcome
            self.save_outcome()
            return

        now = _now_utc()
        cutoff_30 = now - timedelta(days=30)
        cutoff_90 = now - timedelta(days=90)

        # Portfolio summary
        all_stats = self._compute_stats(trades)
        outcome["portfolio_summary"]["all_time"] = all_stats

        recent_30 = [t for t in trades if self._trade_after(t, cutoff_30)]
        recent_90 = [t for t in trades if self._trade_after(t, cutoff_90)]
        outcome["portfolio_summary"]["last_30_days"] = self._compute_stats(recent_30)
        outcome["portfolio_summary"]["last_90_days"] = self._compute_stats(recent_90)

        # By sector
        sectors = {}
        for t in trades:
            s = t.get("sector", "Unknown")
            sectors.setdefault(s, []).append(t)
        for s, s_trades in sectors.items():
            st = self._compute_stats(s_trades)
            outcome["by_sector"][s] = {
                "trades": len(s_trades),
                "win_rate": st["win_rate"],
                "pnl_usd": st["total_pnl_usd"],
            }

        # By stock
        stocks = {}
        for t in trades:
            sym = t.get("stock", "???")
            stocks.setdefault(sym, []).append(t)
        for sym, s_trades in stocks.items():
            st = self._compute_stats(s_trades)
            recs = len([
                at for at in self.state.get("active_trades", [])
                if at["stock"] == sym
            ]) + len(s_trades)
            tag = None
            if st["wins"] >= 3 and st["win_rate"] >= 0.75:
                tag = "GOLD"
            elif st["losses"] >= 3 and st["win_rate"] <= 0.35:
                tag = "WEAK"
            outcome["by_stock"][sym] = {
                "recs": recs,
                "closed": len(s_trades),
                "wins": st["wins"],
                "win_rate": st["win_rate"],
                "pnl_usd": st["total_pnl_usd"],
                "tag": tag,
            }

        # By combo
        combos = {}
        for t in trades:
            ck = t.get("recommendation", {}).get("combo_key", [])
            if isinstance(ck, str):
                ck = _normalize_combo_key(ck)
            key = _combo_key_str(ck)
            if not key:
                continue
            combos.setdefault(key, []).append(t)
        min_samples = config.get("min_combo_samples", 5)
        suppress_wr = config.get("suppress_below_winrate", 0.40)
        flag_wr = config.get("flag_above_winrate", 0.70)
        for key, c_trades in combos.items():
            st = self._compute_stats(c_trades)
            n = len(c_trades)
            suppressed = n >= min_samples and st["win_rate"] < suppress_wr
            flagged = n >= min_samples and st["win_rate"] > flag_wr
            note = None
            if n < min_samples and st["win_rate"] < suppress_wr:
                note = f"Below threshold but only {n} samples, not suppressed yet"
            outcome["by_combo"][key] = {
                "signals": n,
                "wins": st["wins"],
                "win_rate": st["win_rate"],
                "pnl_usd": st["total_pnl_usd"],
                "suppressed": suppressed,
                "flagged": flagged,
                "note": note,
            }

        # Pre-computed 90-day rollup (avoids needing archive decompression)
        scans_90 = [
            s for s in self.history.get("scan_summaries", [])
            if self._timestamp_after(s.get("timestamp", ""), cutoff_90)
        ]
        outcome["precomputed_90day_rollup"] = {
            "total_scans": len(scans_90),
            "total_signals": sum(s.get("signals_generated", 0) for s in scans_90),
            "avg_signals_per_scan": (
                round(sum(s.get("signals_generated", 0) for s in scans_90) / max(len(scans_90), 1), 1)
            ),
            "top_combos": self._top_combos(outcome["by_combo"], flagged=True),
            "worst_combos": self._top_combos(outcome["by_combo"], flagged=False, worst=True),
        }

        # Learning insights
        insights = outcome["learning_insights"]
        by_stock = outcome["by_stock"]
        by_combo = outcome["by_combo"]

        # Top performers (stocks with GOLD tag)
        insights["top_performers"] = [
            f"{sym} ({d['win_rate']:.0%} win, {d['tag']})"
            for sym, d in sorted(by_stock.items(), key=lambda x: x[1]["win_rate"], reverse=True)
            if d.get("tag") == "GOLD"
        ][:5]

        # Avoid patterns (suppressed combos)
        insights["avoid_patterns"] = [
            f"{key}: {d['win_rate']:.0%} win ({d['signals']} trades)"
            for key, d in by_combo.items()
            if d.get("suppressed")
        ][:5]

        # Recommendations (flagged combos)
        insights["recommendations"] = [
            f"{key} is your strongest edge ({d['win_rate']:.0%}, {d['signals']} trades)"
            for key, d in sorted(by_combo.items(), key=lambda x: x[1]["win_rate"], reverse=True)
            if d.get("flagged")
        ][:5]

        self.outcome = outcome
        self.save_outcome()

    def is_combo_suppressed(self, combo_key):
        """Check if a combo should be suppressed based on outcome data."""
        key = _combo_key_str(_normalize_combo_key(combo_key))
        combo_data = self.outcome.get("by_combo", {}).get(key, {})
        return combo_data.get("suppressed", False)

    def is_combo_flagged(self, combo_key):
        """Check if a combo should be highlighted as high-performing."""
        key = _combo_key_str(_normalize_combo_key(combo_key))
        combo_data = self.outcome.get("by_combo", {}).get(key, {})
        return combo_data.get("flagged", False)

    # --- Score Capping ---

    @staticmethod
    def cap_score(base_raw, bt_adj=0, arb_bonus=0, regime_mult=1.0, config=None):
        """
        Apply v1.2.0 score capping: base 75 + bonus 25 = max 100.

        Returns:
            (final_score, score_breakdown)
        """
        cfg = config or DEFAULT_CONFIG
        base_cap = cfg.get("base_score_cap", 75)
        max_bonus = cfg.get("max_bonus", 25)

        base = min(max(base_raw, 0), base_cap)
        bt = min(max(bt_adj, -10), 10)  # bt_adj: -10 to +10
        arb = min(max(arb_bonus, 0), 10)  # arb: 0 to +10
        bonus = min(bt + arb, max_bonus)
        raw_total = base + max(bonus, 0)  # Negative bt can pull below base
        pre_regime = min(raw_total, 100)
        final = int(pre_regime * regime_mult)
        final = min(max(final, 0), 100)

        breakdown = {
            "base": base,
            "bt_adj": bt,
            "arb_bonus": arb,
            "regime_mult": regime_mult,
            "pre_regime": pre_regime,
            "final": final,
        }
        return final, breakdown

    # --- SL/TP Monitor Helpers ---

    def get_monitor_config(self):
        """Return SL/TP monitor settings."""
        cfg = self.config
        return {
            "enabled": cfg.get("sl_tp_monitor_enabled", False),
            "interval_sec": cfg.get("sl_tp_monitor_interval_sec", 30),
            "end_et": cfg.get("sl_tp_monitor_end_et", "18:30"),
        }

    def set_monitor_enabled(self, enabled):
        """Toggle SL/TP monitor on/off."""
        self.state["config"]["sl_tp_monitor_enabled"] = enabled
        self.save_state()

    # --- Internal Helpers ---

    def _find_active_trade(self, trade_id):
        for t in self.state.get("active_trades", []):
            if t["trade_id"] == trade_id:
                return t
        return None

    def _next_trade_seq(self, stock):
        """Generate sequential trade number for today."""
        today = _now_utc().strftime("%Y%m%d")
        prefix = f"{stock}_{today}_"
        existing = [
            t["trade_id"] for t in self.state.get("active_trades", [])
            if t["trade_id"].startswith(prefix)
        ] + [
            t["trade_id"] for t in self.history.get("closed_trades", [])
            if t["trade_id"].startswith(prefix)
        ]
        return f"{len(existing) + 1:03d}"

    @staticmethod
    def _compute_stats(trades):
        """Compute win/loss/pnl stats from a list of closed trades."""
        wins = sum(1 for t in trades if t.get("outcome", {}).get("status") == "win")
        losses = sum(1 for t in trades if t.get("outcome", {}).get("status") == "loss")
        be = sum(1 for t in trades if t.get("outcome", {}).get("status") == "breakeven")
        total_pnl = sum(t.get("outcome", {}).get("pnl_usd", 0) for t in trades)
        n = len(trades)

        # Profit factor
        gross_wins = sum(
            t["outcome"]["pnl_usd"] for t in trades
            if t.get("outcome", {}).get("pnl_usd", 0) > 0
        )
        gross_losses = abs(sum(
            t["outcome"]["pnl_usd"] for t in trades
            if t.get("outcome", {}).get("pnl_usd", 0) < 0
        ))
        pf = round(gross_wins / gross_losses, 2) if gross_losses > 0 else float("inf")

        # Avg hold days
        hold_days = [
            t.get("outcome", {}).get("hold_time_days", 0) for t in trades
            if t.get("outcome", {}).get("hold_time_days")
        ]
        avg_hold = round(sum(hold_days) / len(hold_days), 1) if hold_days else 0

        decided = wins + losses
        return {
            "wins": wins,
            "losses": losses,
            "breakeven": be,
            "win_rate": round(wins / decided, 4) if decided > 0 else 0.0,
            "total_pnl_usd": round(total_pnl, 2),
            "profit_factor": pf if pf != float("inf") else 999.99,
            "avg_hold_days": avg_hold,
        }

    @staticmethod
    def _trade_after(trade, cutoff_dt):
        """Check if a trade's first entry is after cutoff."""
        entries = trade.get("entries", [])
        if not entries:
            return False
        ts = entries[0].get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            return dt >= cutoff_dt
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _timestamp_after(ts_str, cutoff_dt):
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            return dt >= cutoff_dt
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _calc_hold_days(entry_ts, exit_ts):
        try:
            e = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
            x = datetime.fromisoformat(exit_ts.replace("Z", "+00:00"))
            return round((x - e).total_seconds() / 86400, 2)
        except (ValueError, AttributeError):
            return 0

    @staticmethod
    def _friendly_time(dt):
        """Generate display string like 'Today at 4:30 PM'."""
        now = _now_utc()
        if dt.date() == now.date():
            return f"Today at {dt.strftime('%-I:%M %p')}"
        elif dt.date() == (now - timedelta(days=1)).date():
            return f"Yesterday at {dt.strftime('%-I:%M %p')}"
        else:
            return dt.strftime("%b %d at %-I:%M %p")

    @staticmethod
    def _top_combos(by_combo, flagged=True, worst=False):
        """Extract top/worst combo keys from by_combo dict."""
        items = [
            (k, v) for k, v in by_combo.items()
            if v.get("flagged") == flagged or worst
        ]
        if worst:
            items = [(k, v) for k, v in by_combo.items() if v.get("suppressed")]
        items.sort(key=lambda x: x[1].get("win_rate", 0), reverse=not worst)
        return [k for k, _ in items[:5]]


# --- Quick Self-Test ---

if __name__ == "__main__":
    print("=== DataManager v1.2.0 Self-Test ===\n")

    # Use temp dir for testing — patch module globals directly
    import tempfile
    _orig_dir = DATA_DIR
    test_dir = tempfile.mkdtemp(prefix="mst_test_")
    DATA_DIR = test_dir
    STATE_FILE = os.path.join(test_dir, "state.json")
    HISTORY_FILE = os.path.join(test_dir, "history.json")
    OUTCOME_FILE = os.path.join(test_dir, "outcome_analysis.json")
    MIGRATION_LOG = os.path.join(test_dir, "migrations.log")

    dm = DataManager()

    # Test 1: Score capping
    print("1. Score capping (base 75 + bonus 25 = max 100)")
    tests = [
        (80, 8, 10, 1.0, "base=80 raw → capped 75, bt=8, arb=10"),
        (70, 5, 0, 1.0, "base=70, bt=5, arb=0"),
        (75, 10, 10, 0.80, "max base+bonus, caution regime"),
        (50, -5, 0, 1.0, "low base with negative bt"),
        (90, 10, 10, 1.10, "everything maxed, bull regime"),
    ]
    for base, bt, arb, mult, desc in tests:
        score, bd = DataManager.cap_score(base, bt, arb, mult)
        print(f"   {desc}: final={score}, breakdown={bd}")
    assert DataManager.cap_score(90, 10, 10, 1.10)[0] <= 100, "Score exceeded 100!"
    print("   ✅ All scores ≤ 100\n")

    # Test 2: Trade logging
    print("2. Trade logging (multi-fill)")
    rec = {
        "signal_date": "2026-04-05",
        "score": 85,
        "score_breakdown": {"base": 72, "bt_adj": 8, "arb_bonus": 0, "final": 80},
        "primary_condition": "RSI(3)≤20",
        "conditions_met": ["RSI_recovery", "MACD_bullish", "Volume_surge"],
        "combo_key": "Volume_surge+RSI_recovery+MACD_bullish",  # unsorted string
    }
    tid = dm.log_trade(
        "PANW", rec,
        entries=[{"price": 152.3, "shares": 50, "timestamp": _now_iso()}],
        sector="Software",
    )
    print(f"   Trade logged: {tid}")

    # Add fill
    dm.add_fill(tid, 153.1, 50)
    trade = dm._find_active_trade(tid)
    print(f"   After 2nd fill: avg={trade['avg_entry_price']}, shares={trade['total_shares']}")
    assert trade["avg_entry_price"] == 152.7, "Weighted avg wrong"
    assert trade["total_shares"] == 100
    # Check combo_key normalized
    assert trade["recommendation"]["combo_key"] == ["MACD_bullish", "RSI_recovery", "Volume_surge"]
    print("   ✅ Multi-fill + combo normalization correct\n")

    # Test 3: Edit trade
    print("3. Trade editing")
    dm.edit_trade(tid, "entries.0.price", 152.50)
    trade = dm._find_active_trade(tid)
    assert trade["entries"][0]["price"] == 152.50
    assert len(trade["edits"]) == 1
    assert trade["edits"][0]["old"] == 152.3
    print(f"   Edit logged: {trade['edits'][0]}")
    print("   ✅ Edit tracking works\n")

    # Test 4: Partial close
    print("4. Partial close")
    result = dm.close_trade(tid, [{"price": 155.0, "shares": 40, "timestamp": _now_iso(), "reason": "Partial profit"}])
    assert result == "partial"
    trade = dm._find_active_trade(tid)
    assert trade["total_shares"] == 60
    print(f"   After partial: {trade['total_shares']} shares remain")
    print("   ✅ Partial close correct\n")

    # Test 5: Full close
    print("5. Full close + outcome")
    result = dm.close_trade(tid, [{"price": 156.0, "shares": 60, "timestamp": _now_iso(), "reason": "1R"}])
    assert result == "full"
    assert len(dm.get_active_trades()) == 0
    assert len(dm.get_closed_trades()) == 1
    closed = dm.get_closed_trades()[0]
    print(f"   Outcome: {closed['outcome']}")
    assert closed["outcome"]["status"] == "win"
    assert dm.state["outcome_dirty"] is True
    print("   ✅ Full close + dirty flag set\n")

    # Test 6: Combo suppression/flagging with min samples
    print("6. Combo suppression logic (min 5 samples)")
    dm.recompute_if_dirty()
    combo_key = ["MACD_bullish", "RSI_recovery", "Volume_surge"]
    assert not dm.is_combo_suppressed(combo_key), "Should not suppress with 1 sample"
    print("   1 trade, win_rate=100% → not suppressed (< 5 samples)")
    print("   ✅ Min sample size respected\n")

    # Test 7: Scan summary (top 20 only)
    print("7. Scan summary storage (top 20)")
    fake_results = [
        {"stock": f"SYM{i}", "score": 90 - i, "combo_key": ["RSI_recovery"],
         "signal": "BUY" if i < 5 else "WATCH", "passes_gates": i < 18, "suppressed": False}
        for i in range(50)
    ]
    dm.save_scan_summary(fake_results, {"spy_price": 520, "vix": 18.3, "regime": "NORMAL"})
    scan = dm.history["scan_summaries"][-1]
    assert len(scan["top_20_signals"]) == 20
    assert scan["top_20_signals"][0]["score"] == 90  # highest first
    print(f"   Stored {len(scan['top_20_signals'])} signals (of {scan['watchlist_count']} scanned)")
    print("   ✅ Top-20 trim working\n")

    # Test 8: Fire alert
    print("8. Fire alert logging")
    aid = dm.log_fire_alert(
        "PANW", "sl_tp_alert",
        {"subtype": "stop_loss_hit", "target_price": 149.50, "current_price": 149.20},
        trade_id=tid,
    )
    alerts = dm.get_fire_alerts(stock="PANW", pending_only=True)
    assert len(alerts) == 1
    dm.update_alert_action(aid, "dismissed")
    alerts = dm.get_fire_alerts(stock="PANW", pending_only=True)
    assert len(alerts) == 0
    print(f"   Alert {aid} → dismissed")
    print("   ✅ Fire alert lifecycle correct\n")

    # Test 9: SL/TP monitor config
    print("9. Monitor config (extended to 18:30)")
    mon = dm.get_monitor_config()
    assert mon["end_et"] == "18:30"
    print(f"   Monitor window ends at {mon['end_et']} ET")
    print("   ✅ Extended monitor window confirmed\n")

    # Cleanup
    shutil.rmtree(test_dir)
    print("=" * 40)
    print("ALL 9 TESTS PASSED ✅")
    print("=" * 40)
