#!/usr/bin/env python3
"""
journal_dash_server.py — no-cache static server + on-demand rebuild for the
Journal Dashboard.

This REPLACES the ad-hoc "python3 -m http.server with no-cache" static server
that serves journal_dashboard.html + journal_data.js on Michael's LAN. It keeps
that behavior (every response is sent no-store so the phone never shows a stale
cached page) and ADDS two endpoints that let the phone's "🔄 Refresh" button
re-run the swing scan and rebuild the dashboard so it matches the always-live
desktop app.

Same architecture as swing_web_reader.py's Scan-Now: POST to kick off a
subprocess, poll a status endpoint that parses `[n/m]` progress out of the log,
then the page reloads itself.

    GET  /                (+ any file)  static, served no-cache from DIR
    GET  /rebuild-status  JSON: idle | running | done | failed  (+ progress)
    POST /rebuild         starts scan -> build in a worker thread (idempotent)

The rebuild runs, from DIR, sequentially:
    1. swing_headless_scan.py     (the real swing scan; ~1-2 min live)
    2. build_journal_data.py      (regenerates journal_data.js)
Combined stdout+stderr of both is captured to a log file (truncated per run).

stdlib only — runs on the system python3. The rebuild itself uses the venv
python (JOURNAL_DASH_PY) because build_journal_data.py needs yfinance.

Run:
    python3 journal_dash_server.py
Then open the printed "your phone" URL on any device on the wifi.

Config (all env, sensible defaults):
    JOURNAL_DASH_DIR   directory served + rebuild cwd  (~/Desktop/swing_project)
    JOURNAL_DASH_PORT  listen port                     (8090)
    JOURNAL_DASH_PY    python that runs the scripts     (~/stock-tracker-env/bin/python)
    JOURNAL_DASH_LOG   combined rebuild log path        (/tmp/journal_rebuild.log)
"""
import http.server
import json
import os
import re
import socket
import socketserver
import subprocess
import threading
import time
import datetime as dt
from urllib.parse import urlparse

# ───────────────────────── config (env-overridable) ─────────────────────────
DIR      = os.path.abspath(os.path.expanduser(
    os.environ.get("JOURNAL_DASH_DIR", "~/Desktop/swing_project")))
PORT     = int(os.environ.get("JOURNAL_DASH_PORT", "8090"))
PY       = os.path.expanduser(
    os.environ.get("JOURNAL_DASH_PY", "~/stock-tracker-env/bin/python"))
LOG_PATH = os.path.expanduser(
    os.environ.get("JOURNAL_DASH_LOG", "/tmp/journal_rebuild.log"))
# Watchdog: a phase that runs longer than this is killed and the run marked
# failed, so a wedged yfinance can't leave the state stuck "running" forever
# (which, with single-flight, would disable Refresh on every device).
PHASE_TIMEOUT = int(os.environ.get("JOURNAL_DASH_TIMEOUT", "1800"))  # 30 min/phase

# The two scripts run from DIR, in this order. Fixed names — NEVER taken from
# the request (this server is LAN-exposed; no query-driven paths, no arbitrary
# command exec).
SCAN_SCRIPT  = "swing_headless_scan.py"
BUILD_SCRIPT = "build_journal_data.py"

# ───────────────────────── rebuild state ─────────────────────────
# One rebuild at a time. The worker thread updates this dict; the request
# handlers read it. CPython dict key writes are atomic, and every transition
# that must be exclusive (the "already running?" check + thread start) happens
# under REBUILD_LOCK.
REBUILD = {
    "thread":   None,    # the worker Thread (None => never run)
    "started":  0.0,     # time.time() when the current/last run began
    "phase":    None,    # "scan" | "build" | None (None when not running)
    "scan_rc":  None,    # return code of swing_headless_scan.py
    "build_rc": None,    # return code of build_journal_data.py
}
REBUILD_LOCK = threading.Lock()


def rebuild_state():
    """idle | running | done | failed — derived from thread liveness + rc's."""
    th = REBUILD["thread"]
    if th is None:
        return "idle"
    if th.is_alive():
        return "running"
    if REBUILD["scan_rc"] == 0 and REBUILD["build_rc"] == 0:
        return "done"
    return "failed"


def rebuild_progress():
    """Last `[n/m]` emitted in the log (the scan phase prints them)."""
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            m = re.findall(r"\[(\d+)/(\d+)\]", f.read())
        if m:
            return int(m[-1][0]), int(m[-1][1])
    except Exception:
        pass
    return None, None


def log_tail(n=12):
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return ""


def _wait_with_watchdog(p, logf, line):
    """Wait for p, but kill it (SIGTERM→SIGKILL) if it runs past PHASE_TIMEOUT.
    Returns the exit code, or a nonzero sentinel on timeout so the run is
    marked failed instead of hanging 'running' forever."""
    try:
        return p.wait(timeout=PHASE_TIMEOUT)
    except subprocess.TimeoutExpired:
        line("\n!!! phase exceeded %ss — terminating" % PHASE_TIMEOUT)
        logf.flush()
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                p.wait(timeout=10)
            except Exception:
                pass
        return p.returncode if p.returncode is not None else 124  # 124 = timed out


def _rebuild_worker(scan_path, build_path):
    """Run scan -> build sequentially, combined output to LOG_PATH (truncated
    by the POST handler before this thread starts). A crash here NEVER takes
    down the server — it only marks the run failed."""
    try:
        # Append (the POST handler already truncated + wrote a start banner, so
        # a stale prior [n/m] can't flash on the first poll of the new run).
        with open(LOG_PATH, "a", encoding="utf-8") as logf:
            def line(s):
                logf.write(s + "\n")
                logf.flush()

            line("DIR=%s  PY=%s" % (DIR, PY))

            # phase 1: swing scan (emits [n/total] progress lines)
            REBUILD["phase"] = "scan"
            line("\n--- phase: scan (%s) ---" % SCAN_SCRIPT)
            logf.flush()
            p = subprocess.Popen([PY, "-u", scan_path], cwd=DIR,
                                 stdout=logf, stderr=subprocess.STDOUT)
            REBUILD["scan_rc"] = _wait_with_watchdog(p, logf, line)
            line("\n--- scan exit rc=%s ---" % REBUILD["scan_rc"])

            # phase 2: rebuild journal_data.js (only if the scan succeeded)
            if REBUILD["scan_rc"] == 0:
                REBUILD["phase"] = "build"
                line("\n--- phase: build (%s) ---" % BUILD_SCRIPT)
                logf.flush()
                p2 = subprocess.Popen([PY, "-u", build_path], cwd=DIR,
                                      stdout=logf, stderr=subprocess.STDOUT)
                REBUILD["build_rc"] = _wait_with_watchdog(p2, logf, line)
                line("\n--- build exit rc=%s ---" % REBUILD["build_rc"])
            else:
                line("\n--- build skipped (scan failed) ---")
    except Exception as e:
        # record the crash both in the log and in the state, so status reports
        # "failed" instead of a hung "running".
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as logf:
                logf.write("\n!!! rebuild worker crashed: %r\n" % (e,))
        except Exception:
            pass
        if REBUILD["scan_rc"] is None:
            REBUILD["scan_rc"] = 1
        elif REBUILD["scan_rc"] == 0 and REBUILD["build_rc"] is None:
            REBUILD["build_rc"] = 1
    finally:
        REBUILD["phase"] = None


def _start_rebuild(scan_path, build_path):
    """Reset state and launch the worker. Caller holds REBUILD_LOCK.
    Truncates the log HERE, before this function returns and the POST replies
    'running', so the first /rebuild-status poll can't read a stale [n/m] left
    over from the previous run's log."""
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as logf:
            logf.write("=== journal rebuild started %s ===\n"
                       % dt.datetime.now().isoformat(timespec="seconds"))
    except Exception:
        pass  # a log we can't truncate is non-fatal; the worker appends anyway
    REBUILD["scan_rc"] = None
    REBUILD["build_rc"] = None
    REBUILD["phase"] = "scan"
    REBUILD["started"] = time.time()
    th = threading.Thread(target=_rebuild_worker,
                          args=(scan_path, build_path), daemon=True)
    REBUILD["thread"] = th
    th.start()


# ───────────────────────── HTTP handler ─────────────────────────
class Handler(http.server.SimpleHTTPRequestHandler):
    """Static file server rooted at DIR, plus the two rebuild routes. Every
    response carries the no-cache headers (via end_headers)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    # no-store on EVERY response (static files, JSON, and error pages alike)
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- GET: /rebuild-status, else static ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/rebuild-status":
            self._rebuild_status()
            return
        try:
            super().do_GET()          # static serve from DIR
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        try:
            super().do_HEAD()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- POST: /rebuild only; everything else 404 ----
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/rebuild":
            self._rebuild_post()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def _rebuild_status(self):
        try:
            state = rebuild_state()
            elapsed = round(time.time() - REBUILD["started"], 1) if REBUILD["started"] else 0
            n, tot = rebuild_progress()
            payload = {
                "ok": True,
                "state": state,
                "elapsed": elapsed,
                "phase": REBUILD["phase"] if state == "running" else None,
                "done_n": n,
                "done_total": tot,
            }
            if state == "failed":
                payload["log_tail"] = log_tail(12)
            self._send_json(200, payload)
        except Exception as e:
            self._send_json(500, {"ok": False, "error": "status error: %r" % (e,)})

    def _rebuild_post(self):
        try:
            scan_path = os.path.join(DIR, SCAN_SCRIPT)
            build_path = os.path.join(DIR, BUILD_SCRIPT)
            with REBUILD_LOCK:
                if rebuild_state() == "running":
                    self._send_json(200, {"ok": True, "state": "running",
                                          "note": "already running"})
                    return
                missing = [name for name, p in
                           ((SCAN_SCRIPT, scan_path), (BUILD_SCRIPT, build_path))
                           if not os.path.isfile(p)]
                if missing:
                    self._send_json(200, {"ok": False,
                                          "error": "missing in %s: %s"
                                                   % (DIR, ", ".join(missing))})
                    return
                _start_rebuild(scan_path, build_path)
            self._send_json(200, {"ok": True, "state": "running"})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": "rebuild error: %r" % (e,)})

    def log_message(self, *args):
        pass  # quiet console (LAN traffic isn't interesting)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def main():
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        print("Journal dashboard server")
        print("  serving  : %s  (no-cache)" % DIR)
        print("  rebuild  : %s -> %s" % (SCAN_SCRIPT, BUILD_SCRIPT))
        print("  python   : %s" % PY)
        print("  log      : %s" % LOG_PATH)
        if not os.path.isfile(os.path.join(DIR, "journal_dashboard.html")):
            print("  (note: journal_dashboard.html not found in DIR — copy it in)")
        for name in (SCAN_SCRIPT, BUILD_SCRIPT):
            if not os.path.isfile(os.path.join(DIR, name)):
                print("  (note: %s not in DIR — Refresh will report it missing)" % name)
        print("Open in a browser:")
        print("  this machine : http://127.0.0.1:%d/journal_dashboard.html" % PORT)
        for ip in lan_ips():
            print("  your phone   : http://%s:%d/journal_dashboard.html   "
                  "<- open this on any device on your wifi" % (ip, PORT))
        print("Ctrl-C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
