#!/usr/bin/env python3
"""
swing_web_reader.py - thin web display for Michael Swing Trader signals.

ADR-1 architecture: the scan engine writes results to a JSON file; THIS is a thin
reader of that file. No coupling to the tkinter GUI, no scan logic here. The JSON
file on disk is the contract between engine and display.

Why a web page: viewable on ANY device on your network, including your phone.
Why stdlib-only: zero dependencies -> runs identically on Linux (Dell) and Windows.

Run:
    python3 swing_web_reader.py
Then open the printed URL. The "network" URL works from your phone/other machines.

Optional overrides:
    python3 swing_web_reader.py /path/to/results.json     # custom JSON path
    SWING_READER_PORT=9000 python3 swing_web_reader.py     # custom port
    SWING_JSON=/path/to/results.json python3 swing_web_reader.py

Anti-silent-failure: the expanded view of each row dumps EVERY field in the
record (raw data preserved alongside the derived columns), and a missing/unreadable
JSON file shows an explicit message instead of a blank screen.
"""
import http.server
import socketserver
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

# ----- config (all overridable; expanduser is Windows-safe) -----
PORT = int(os.environ.get("SWING_READER_PORT", "8765"))
DEFAULT_JSON = os.path.expanduser("~/Desktop/swing_headless_results.json")
JSON_PATH = (
    sys.argv[1] if len(sys.argv) > 1
    else os.environ.get("SWING_JSON", DEFAULT_JSON)
)

# ----- Scan-Now state -----
SCAN_SCRIPT = os.path.expanduser("~/Desktop/swing_project/swing_headless_scan.py")
SCAN_CWD = os.path.expanduser("~/Desktop/swing_project")
SCAN_LOG = "/tmp/swing_scan_run.log"
SCAN = {"proc": None, "started": 0.0}
SCAN_LOCK = threading.Lock()

def scan_state():
    p = SCAN["proc"]
    if p is None:
        return "idle"
    rc = p.poll()
    if rc is None:
        return "running"
    return "done" if rc == 0 else "failed"

def scan_progress():
    try:
        with open(SCAN_LOG) as f:
            m = re.findall(r"\[(\d+)/(\d+)\]", f.read())
        if m:
            return int(m[-1][0]), int(m[-1][1])
    except Exception:
        pass
    return None, None

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Swing Signals</title>
<style>
  :root{
    --bg:#0e1116; --panel:#161b22; --panel2:#1c232d; --line:#2a3340;
    --text:#e6edf3; --muted:#8b98a5; --accent:#58a6ff;
    --gold:#e3b341; --green:#3fb950; --red:#f85149; --watchbg:#1c232d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-text-size-adjust:100%;}
  .wrap{max-width:880px;margin:0 auto;padding:14px 12px 60px;}
  header{position:sticky;top:0;z-index:5;background:var(--bg);
    padding:6px 0 10px;border-bottom:1px solid var(--line);}
  h1{font-size:17px;margin:0 0 6px;letter-spacing:.3px;}
  .meta{font-size:12.5px;color:var(--muted);line-height:1.5;
    display:flex;flex-wrap:wrap;gap:4px 14px;}
  .meta b{color:var(--text);font-weight:600;}
  .controls{display:flex;gap:8px;margin:10px 0 4px;flex-wrap:wrap;}
  .btn{font-size:12.5px;color:var(--text);background:var(--panel2);
    border:1px solid var(--line);border-radius:7px;padding:6px 11px;cursor:pointer;}
  .btn.active{border-color:var(--accent);color:var(--accent);}
  .sec{font-size:12px;color:var(--muted);text-transform:uppercase;
    letter-spacing:.8px;margin:16px 2px 7px;}
  .row{background:var(--panel);border:1px solid var(--line);border-radius:9px;
    margin-bottom:7px;overflow:hidden;}
  .row.watch{background:var(--watchbg);}
  .rowhead{display:grid;
    grid-template-columns:64px 50px 1fr 80px 46px 44px 46px 46px 56px;
    align-items:center;gap:8px;padding:11px 12px;cursor:pointer;}
  .sym{font-weight:700;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    font-size:15px;}
  .score{font-family:ui-monospace,Consolas,monospace;font-size:15px;font-weight:700;
    text-align:right;}
  .badge{justify-self:start;font-size:11px;font-weight:700;padding:3px 8px;
    border-radius:999px;white-space:nowrap;letter-spacing:.3px;}
  .b-actnow{background:rgba(227,179,65,.18);color:var(--gold);border:1px solid rgba(227,179,65,.5);}
  .b-arb{background:rgba(227,179,65,.12);color:var(--gold);border:1px solid rgba(227,179,65,.4);}
  .b-buy{background:rgba(63,185,80,.14);color:var(--green);border:1px solid rgba(63,185,80,.45);}
  .b-sell{background:rgba(248,81,73,.14);color:var(--red);border:1px solid rgba(248,81,73,.45);}
  .b-watch{background:transparent;color:var(--muted);border:1px solid var(--line);}
  .num{font-family:ui-monospace,Consolas,monospace;font-size:13px;color:var(--text);
    text-align:right;}
  .num .lbl{display:block;font-size:9.5px;color:var(--muted);letter-spacing:.4px;
    font-family:inherit;font-weight:400;text-transform:uppercase;}
  .caret{color:var(--muted);font-size:12px;text-align:center;}
  .detail{display:none;border-top:1px solid var(--line);padding:10px 12px 12px;
    background:var(--panel2);}
  .row.open .detail{display:block;}
  .row.open .caret{transform:rotate(90deg);}
  .kv{display:grid;grid-template-columns:1fr 1fr;gap:5px 16px;
    font-family:ui-monospace,Consolas,monospace;font-size:12px;}
  .kv div{display:flex;justify-content:space-between;gap:8px;
    border-bottom:1px dotted var(--line);padding:3px 0;}
  .kv .k{color:var(--muted);}
  .kv .v{color:var(--text);text-align:right;word-break:break-word;}
  .empty{color:var(--muted);font-size:13px;padding:24px 4px;text-align:center;}
  .err{color:var(--red);font-size:13px;padding:18px 4px;text-align:center;line-height:1.5;}
  footer{position:fixed;left:0;right:0;bottom:0;background:var(--panel);
    border-top:1px solid var(--line);font-size:11px;color:var(--muted);
    padding:7px 12px;display:flex;justify-content:space-between;gap:10px;
    flex-wrap:wrap;}
  footer code{color:var(--muted);}
  @media (max-width:520px){
    .rowhead{grid-template-columns:52px 44px 1fr 64px 42px 52px;}
    .col-desk{display:none;}
    .meta{font-size:12px;}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Swing Signals</h1>
    <div class="meta" id="meta">loading…</div>
    <div class="controls">
      <button class="btn active" id="btn-act" onclick="setView('actionable')">Actionable</button>
      <button class="btn" id="btn-all" onclick="setView('all')">All scored</button>
      <button class="btn" id="btn-refresh" onclick="load()">↻ Refresh</button>
      <button class="btn" id="btn-scan" onclick="scanNow()">⟳ Scan Now</button>
    </div>
  </header>
  <div id="list"><div class="empty">loading…</div></div>
</div>
<footer>
  <span id="foot-left">—</span>
  <span id="foot-right"><code id="foot-path"></code></span>
</footer>

<script>
var VIEW = "actionable";
var LAST = null;

function tier(sig){
  sig = (sig||"").toString().toUpperCase();
  if (sig.indexOf("ACT NOW") >= 0) return "actnow";
  if (sig.indexOf("ARB")     >= 0) return "arb";
  if (sig.indexOf("BUY")     >= 0) return "buy";
  if (sig.indexOf("SELL")    >= 0) return "sell";
  return "watch";
}
function isActionable(t){ return t==="actnow"||t==="arb"||t==="buy"||t==="sell"; }
function rank(t){ return {actnow:0,arb:1,buy:2,sell:3,watch:4}[t]; }

function g(r, keys, dflt){           // defensive multi-key getter
  for (var i=0;i<keys.length;i++){
    var parts = keys[i].split(".");
    var v = r, ok = true;
    for (var j=0;j<parts.length;j++){
      if (v && typeof v==="object" && parts[j] in v) v = v[parts[j]];
      else { ok=false; break; }
    }
    if (ok && v!==null && v!==undefined && v!=="") return v;
  }
  return dflt;
}
function fnum(v, dp, suffix){
  if (v===null||v===undefined||v==="") return "—";
  var n = Number(v);
  if (isNaN(n)) return String(v);
  return n.toFixed(dp) + (suffix||"");
}
function esc(s){ return String(s).replace(/[&<>"]/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]; }); }

function setView(v){
  VIEW = v;
  document.getElementById("btn-act").classList.toggle("active", v==="actionable");
  document.getElementById("btn-all").classList.toggle("active", v==="all");
  render();
}

function flatten(obj, prefix, out){    // dump every field for the expanded raw view
  out = out || {}; prefix = prefix || "";
  if (obj && typeof obj==="object" && !Array.isArray(obj)){
    for (var k in obj){ flatten(obj[k], prefix? prefix+"."+k : k, out); }
  } else {
    out[prefix] = Array.isArray(obj) ? JSON.stringify(obj) : obj;
  }
  return out;
}

function rowHTML(r, idx){
  var sig = g(r,["signal","tier"],"");
  var t = tier(sig);
  var sym = g(r,["symbol","ticker"],"?");
  var score = g(r,["final_score","score"],"—");
  var price = g(r,["price"],null);
  var rsi = g(r,["rsi"],null);
  var atr = g(r,["atr_swing","atr_swing_pct","atr"],null);
  var tech = g(r,["raw_buy","buy_score"],null);
  var btv  = g(r,["bt_adj"],null);
  var btStr = (btv===null) ? "—" : ((Number(btv)>=0?"+":"")+btv);
  var zz   = g(r,["arb.z"],null);
  var arbStr = (zz===null||Number(zz)===0) ? "—" : Number(zz).toFixed(1);
  var flat = flatten(r);
  var kv = "";
  Object.keys(flat).sort().forEach(function(k){
    kv += '<div><span class="k">'+esc(k)+'</span><span class="v">'+esc(flat[k])+'</span></div>';
  });
  return ''
    + '<div class="row '+(t==="watch"?"watch":"")+'" id="row'+idx+'">'
    +   '<div class="rowhead" onclick="document.getElementById(\'row'+idx+'\').classList.toggle(\'open\')">'
    +     '<span class="sym">'+esc(sym)+'</span>'
    +     '<span class="score">'+esc(score)+'</span>'
    +     '<span class="badge b-'+t+'">'+esc(sig||t.toUpperCase())+'</span>'
    +     '<span class="num">'+ (price!==null?("$"+fnum(price,2)):"—") +'<span class="lbl">price</span></span>'
    +     '<span class="num">'+ fnum(rsi,0) +'<span class="lbl">rsi</span></span>'
    +     '<span class="num col-desk">'+ (tech!==null?tech:"—") +'<span class="lbl">tech</span></span>'
    +     '<span class="num col-desk">'+ btStr +'<span class="lbl">bt±</span></span>'
    +     '<span class="num col-desk">'+ arbStr +'<span class="lbl">arb</span></span>'
    +     '<span class="num col-atr">'+ fnum(atr,1,"%") +'<span class="lbl">swing%</span></span>'
    +   '</div>'
    +   '<div class="detail"><div class="kv">'+kv+'</div></div>'
    + '</div>';
}

function render(){
  var box = document.getElementById("list");
  if (!LAST){ box.innerHTML = '<div class="empty">no data</div>'; return; }
  var results = LAST.results || LAST.signals || [];
  // keep only scored records (have a numeric score) for display
  var scored = results.filter(function(r){
    var s = g(r,["final_score","score"],null); return s!==null && !isNaN(Number(s));
  });
  var rows = scored.map(function(r){ return {r:r, t:tier(g(r,["signal","tier"],""))}; });
  if (VIEW==="actionable") rows = rows.filter(function(x){ return isActionable(x.t); });
  rows.sort(function(a,b){
    if (rank(a.t)!==rank(b.t)) return rank(a.t)-rank(b.t);
    return Number(g(b.r,["final_score","score"],0)) - Number(g(a.r,["final_score","score"],0));
  });
  if (!rows.length){
    box.innerHTML = '<div class="empty">'
      + (VIEW==="actionable" ? "No actionable signals in this scan (try \u201cAll scored\u201d)." : "No scored results.")
      + '</div>';
    return;
  }
  var html = "";
  var lastTier = null;
  rows.forEach(function(x, i){
    if (VIEW==="actionable" && x.t!==lastTier){
      var label = {actnow:"Act now",arb:"ARB",buy:"Buy",sell:"Sell"}[x.t] || x.t;
      html += '<div class="sec">'+label+'</div>'; lastTier = x.t;
    }
    html += rowHTML(x.r, i);
  });
  box.innerHTML = html;
}

function renderMeta(){
  var m = document.getElementById("meta");
  if (!LAST){ m.textContent = ""; return; }
  var ts = (LAST.timestamp||"").toString().replace("T"," ").slice(0,16);
  var parts = [];
  if (ts) parts.push('<b>'+esc(ts)+'</b>');
  if (LAST.regime_state!==undefined) parts.push('regime <b>'+esc(LAST.regime_state)+'</b>');
  if (LAST.vix!==undefined) parts.push('VIX <b>'+fnum(LAST.vix,1)+'</b>');
  if (LAST.regime_mult!==undefined) parts.push('mult <b>'+fnum(LAST.regime_mult,2)+'</b>');
  if (LAST.breadth_mult!==undefined) parts.push('breadth <b>'+fnum(LAST.breadth_mult,2)+'</b>');
  if (LAST.buy_count!==undefined) parts.push('buys <b>'+esc(LAST.buy_count)+'</b>');
  if (LAST.universe_total!==undefined) parts.push('universe <b>'+esc(LAST.universe_total)+'</b>');
  m.innerHTML = parts.join("");
}

function load(){
  fetch("/data", {cache:"no-store"}).then(function(res){ return res.json(); })
  .then(function(p){
    if (!p.ok){
      LAST = null;
      document.getElementById("list").innerHTML = '<div class="err">'+esc(p.error||"could not load data")+'</div>';
      document.getElementById("meta").textContent = "";
      document.getElementById("foot-left").textContent = "load error";
      document.getElementById("foot-path").textContent = p.path? esc(p.path) : "";
      return;
    }
    LAST = p.data;
    renderMeta(); render();
    var now = new Date();
    var n = (LAST.results||LAST.signals||[]).length;
    document.getElementById("foot-left").textContent =
      n + " records · refreshed " + now.toTimeString().slice(0,8);
    document.getElementById("foot-path").textContent = esc(p.path||"");
  })
  .catch(function(e){
    document.getElementById("foot-left").textContent = "fetch failed: "+e;
  });
}

load();
var SCAN_POLL = null;
function scanNow(){
  var b = document.getElementById("btn-scan");
  b.disabled = true; b.textContent = "starting…";
  fetch("/scan", {method:"POST"}).then(function(r){return r.json();}).then(function(p){
    if (!p.ok){ b.disabled=false; b.textContent="⟳ Scan Now"; alert(p.error||"scan failed to start"); return; }
    pollScan();
  }).catch(function(e){ b.disabled=false; b.textContent="⟳ Scan Now"; });
}
function pollScan(){
  var b = document.getElementById("btn-scan");
  b.disabled = true; b.textContent = "scanning…";
  if (SCAN_POLL) clearInterval(SCAN_POLL);
  SCAN_POLL = setInterval(function(){
    fetch("/scan-status", {cache:"no-store"}).then(function(r){return r.json();}).then(function(p){
      if (p.state === "running"){
        var prog = (p.done_n && p.done_total) ? (" "+p.done_n+"/"+p.done_total) : "";
        b.textContent = "scanning"+prog+"… "+Math.round(p.elapsed)+"s";
      } else if (p.state === "done"){
        clearInterval(SCAN_POLL); SCAN_POLL=null;
        b.disabled=false; b.textContent="⟳ Scan Now";
        load();
        document.getElementById("foot-left").textContent = "scan complete · fresh data loaded";
      } else if (p.state === "failed"){
        clearInterval(SCAN_POLL); SCAN_POLL=null;
        b.disabled=false; b.textContent="⟳ Scan Now";
        alert("Scan failed:\n" + (p.log_tail || "(see /tmp/swing_scan_run.log)"));
      } else {
        clearInterval(SCAN_POLL); SCAN_POLL=null;
        b.disabled=false; b.textContent="⟳ Scan Now";
      }
    }).catch(function(e){});
  }, 2000);
}
// resume polling if a scan is already running when the page loads
fetch("/scan-status",{cache:"no-store"}).then(function(r){return r.json();}).then(function(p){
  if (p && p.state === "running") pollScan();
}).catch(function(e){});
setInterval(load, 20000);   // auto-refresh every 20s so new scans appear
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/data":
            self._send_data()
        elif path == "/scan-status":
            st = scan_state()
            n, tot = scan_progress()
            elapsed = round(time.time() - SCAN["started"], 1) if SCAN["started"] else 0
            payload = {"ok": True, "state": st, "elapsed": elapsed, "done_n": n, "done_total": tot}
            if st == "failed":
                try:
                    with open(SCAN_LOG) as f:
                        payload["log_tail"] = "".join(f.readlines()[-8:])
                except Exception:
                    pass
            self._send(200, json.dumps(payload))
        else:
            self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/scan":
            with SCAN_LOCK:
                if scan_state() == "running":
                    self._send(200, json.dumps({"ok": True, "state": "running", "note": "already running"}))
                    return
                if not os.path.exists(SCAN_SCRIPT):
                    self._send(200, json.dumps({"ok": False, "error": "scan script not found: " + SCAN_SCRIPT}))
                    return
                logf = open(SCAN_LOG, "w")
                SCAN["proc"] = subprocess.Popen(
                    ["python3", "-u", SCAN_SCRIPT],
                    cwd=SCAN_CWD, stdout=logf, stderr=subprocess.STDOUT)
                SCAN["started"] = time.time()
            self._send(200, json.dumps({"ok": True, "state": "running"}))
        else:
            self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def _send_data(self):
        try:
            with open(JSON_PATH, "r") as f:
                raw = json.load(f)
            payload = {"ok": True, "data": raw, "path": JSON_PATH}
        except FileNotFoundError:
            payload = {"ok": False,
                       "error": "No scan file found yet. Run swing_headless_scan.py, then refresh.",
                       "path": JSON_PATH}
        except json.JSONDecodeError as e:
            payload = {"ok": False, "error": "Scan file is not valid JSON: %s" % e, "path": JSON_PATH}
        except Exception as e:
            payload = {"ok": False, "error": "Could not read scan file: %s" % e, "path": JSON_PATH}
        self._send(200, json.dumps(payload))

    def log_message(self, *args):
        pass  # quiet console


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
        print("Swing web reader -- serving: %s" % JSON_PATH)
        if not os.path.exists(JSON_PATH):
            print("  (note: that file doesn't exist yet -- run swing_headless_scan.py; the page will show a message until it does)")
        print("Open in a browser:")
        print("  this machine : http://127.0.0.1:%d" % PORT)
        for ip in lan_ips():
            print("  your phone   : http://%s:%d   <- open this on any device on your wifi" % (ip, PORT))
        print("Ctrl-C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
