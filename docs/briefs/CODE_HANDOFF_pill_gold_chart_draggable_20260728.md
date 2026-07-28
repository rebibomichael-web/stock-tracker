# CODE_HANDOFF — WATCH gold flip + draggable chart overlays (Item 46, owner-ratified)
**Date:** 2026-07-28 · **Target:** `~/Desktop/swing_project/swing_trader.py` · **One session, one relaunch, one MD5 chain.**

**Before-MD5 (verify FIRST; if it doesn't match, STOP and report):** `48e07b4b` (`48e07b4bfcaeb6eeb4053387b2c05a01`) — the committed pill-card build, confirmed on the 12:20 mirror sync.
**Backup:** `cp swing_trader.py swing_trader.py.bak_20260728_goldrag` before any edit.

---

## Task 1 — WATCH tint → gold (RATIFIED by Michael, 2026-07-28)

Line 815, `_PILL_TINT`:

```python
"watch":"white"   →   "watch":"#fff8e1"
```

That is the whole task — the a3 screenshot variant Michael approved. Context you already know from the pill-card session; two notes for the log:

- **Deliberate asymmetry:** the SIGNALS row tint for WATCH stays white. The list wants the sparse BUY/ACT rows to pop out of 86 white WATCH rows; the card wants to read as a badge. Context-aware, not inconsistent — do not "reconcile" them.
- **Observed, accepted:** WATCH now shares ACT's tint (`#fff8e1`); ARB stays the slightly different `#fef9e7`. Michael approved the screenshot as-is. If ACT/WATCH differentiation is ever wanted, ACT could deepen (e.g. `#fff3cd`) — **NOT in this brief.**

Configure-only, no widget churn, no strings touched — the R2 contract is untouched by a dict literal.

---

## Task 2 — Chart overlays draggable (Item 46, gate resolved after 14 months)

### Why (evidence, swept 07-28)
The "Best indicators (backtest)" box obscuring the top-right of the price chart is **CHANGE_LIST Item 46, captured 2026-05-29** (BLK example — the overlay blocks the most recent ~2 weeks of price/EMAs, exactly the entry-decision zone). It was **never implemented**: it stalled at its own mandatory design-review gate (placement options a–d never chosen) and sat queued behind Item 32. Michael hit it again 07-28 on TSLA in SYMBOL and has now **resolved the gate: option (e), drag-and-drop** — the user relocates the box; no placement debate needed.

### Where
`render_chart(df, sym, sigs, fig, ca=None)` — def at mirror line **1226**; the box at **~1250–1255** (anchor by the string `Best indicators (backtest)` — Task 1 shifts no lines). **One call site** (~3534, `self._ensure_fig(); render_chart(...)`) → SYMBOL, 🧭 COMBINED, and the chart popup are all covered by this single edit, same registry economics as the pill work.

### Edit A — the box: `ap.text` → `ap.annotate` + draggable
mpl's plain `Text` has **no** draggable support; `Annotation` and `Legend` do — hence the artist swap. Current code:

```python
ap.text(.99,.98,"\n".join(lines),transform=ap.transAxes,fontsize=7.5,fontfamily="monospace",
       va="top",ha="right",bbox=dict(boxstyle="round,pad=.4",fc="white",ec="#ccc",alpha=.85))
```

Becomes:

```python
ann=ap.annotate("\n".join(lines),xy=(.99,.98),xycoords="axes fraction",fontsize=7.5,fontfamily="monospace",
       va="top",ha="right",bbox=dict(boxstyle="round,pad=.4",fc="white",ec="#ccc",alpha=.85))
ann.draggable(use_blit=True)
```

Identical string, identical styling, identical default position. `anncoords` defaults to `xycoords`, so drags stay in axes-fraction — resize-safe. **Blit fallback:** if dragging leaves stale-pixel artifacts on TkAgg (5-panel figure), drop to `ann.draggable()` (full redraw per move) and accept the slower drag.

### Edit B — rider: EMA legend draggable too
The upper-left EMA legend overlays price the same way. Line ~1249:

```python
leg=ap.legend(fontsize=8,loc="upper left",framealpha=.7)
leg.set_draggable(True)
```

(Leave the Stoch panel's legend alone — it overlays reference data only.)

### Edit C — OPTIONAL rider, only if A works cleanly: session memory of dragged position
`fig.clear()` at the top of `render_chart` means every re-render resets the box to `(.99,.98)`. Cheap fix: module global `_BESTIND_POS=(.99,.98)`; render with `xy=_BESTIND_POS`; connect once per render `fig.canvas.mpl_connect("button_release_event", <store>)` where the handler reads `ann.xyann` (DraggableAnnotation writes the dragged position back there, in axes-fraction) and stores it if both coords are within −0.2..1.2. In-session only — **no disk persistence, no state.json touch.** If it fights the re-render or the drag machinery in any way, skip it and say so; A+B alone close the item.

### Out of scope — explicit
- Box **content** stays byte-identical. Yes, today's TSLA box is five rows of "0% (2 trades)" — small-cell reference info that arguably deserves an n<20 caveat, but that is a separate conversation for the board, not this brief.
- No default reposition, no transparency change, no collapsible ⓘ — drag replaces all of Item 46's a–d options.
- Rounded pill corners stay out (standing).

---

## Gates (one pass, both tasks)
1. `python -m py_compile swing_trader.py`
2. `--selftest-dedup` 3/3 PASS
3. `thread_context_audit --scan` — zero violations (no new threads here; should be trivially clean)
4. Golden untouched (`04b8e0cf`); `swing_core.py` untouched (`55ffd7f2` — this IS the current baseline, the old frozen-hash `ea95e092` was stale)
5. Full `text=` literal inventory identical before/after (Task 2 moves a string between artist types; the string itself is unchanged)
6. **Item 46 acceptance, adapted:** open **TSLA and BLK** (the original 05-29 example) in SYMBOL — drag the box off the right edge, confirm the last ~2 weeks of candles + EMAs are fully readable; box content still legible where dropped; drag the EMA legend; verify the same box drags in 🧭 COMBINED.
7. WATCH card renders gold in SYMBOL + COMBINED (NVDA or any WATCH name); SIGNALS WATCH row still white.
8. `ui_stall` count for 07-28 unchanged (0) after a drag session — drags are main-thread redraws; with blit they should be nowhere near the 3s floor.
9. Relaunch GUI, report MD5 chain `48e07b4b → <new>` + `.bak` name. Mirror check at next :20 sync.

## Logging
- CHANGE_LIST_CONSOLIDATED **Item 46 → CLOSED** (owner-ratified option (e) drag-and-drop, 2026-07-28; note the 05-29→07-28 stall at the design gate as the resolution story).
- MASTER_LOG entry for both tasks; note the WATCH-gold ratification in the pill-card entry.
