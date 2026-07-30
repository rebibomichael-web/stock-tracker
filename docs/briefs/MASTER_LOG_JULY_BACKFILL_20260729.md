# JULY BACKFILL — MASTER_LOG + CHANGE_LIST (authored in the cloud 2026-07-29, Michael-ratified "do backfill")

**For the local Claude Code session on the Dell. Docs-only task — no code files touched.**

## Apply instructions
1. Targets: `~/Desktop/swing_project/MASTER_LOG.md` + `CHANGE_LIST_CONSOLIDATED.md` (canonical), then `cp` both to `~/trading-src/docs/`, verify byte-identical — same flow as your Item-46 close-out.
2. **Insertion point:** the MASTER_LOG entries below go **between the last June entry (Item 45) and your two new July-28 entries** (pill-card session, Item 46), keeping the file chronological. Do NOT duplicate 07-28 — those two entries are yours and already in.
3. **Verify before committing:** every fact below is sourced from the roadmap board, the mirrored briefs, and cloud-verified mirror state — but YOU hold the session memories and `.bak` files. Check each entry against them. Fill every `[DELL: …]` marker; if you cannot confirm something, keep the entry but mark that line `— UNVERIFIED` rather than dropping it silently.
4. After applying: `md5sum swing_trader.py swing_core.py golden_scoring_expected.json` must be unchanged (**`d835bfcc`** / `55ffd7f2` / `04b8e0cf`). (Updated 07-29 evening: fix (b) shipped, so the swing_trader baseline moved a10c31d0 → d835bfcc — an earlier copy of this package said a10c31d0; this version supersedes it.) Report the two docs' new line counts + the mirror copy confirmation.
5. Board reconciliation flag for Michael/cloud: while writing this I noticed the board's Next-Up "Warm start" card may be stale — today's driver transcripts show "startup scan: skipped — last scan … is fresh (<4h)", which looks like the shipped warm-start/freshness path. `[DELL: confirm whether warm start shipped, when, and its MD5 chain — entry provided below either way]`

---

## → CHANGE_LIST_CONSOLIDATED.md — one bridging note + one status line

**(a)** Insert after the last numbered June item, before your Item-46 CLOSED block:

```
### NOTE — July 2026 record-keeping bridge
From July 2026 the working system of record moved from numbered CHANGE_LIST items
to dated CODE_HANDOFF briefs + MASTER_LOG entries + the live roadmap board
(cloud artifact, mirrored to stock-tracker ROADMAP.html). July work is logged as
dated MASTER_LOG entries (backfilled July 29 after the gap was found during the
Item-46 close-out). New numbered items are still valid for discrete defects;
Item 46's July closure is the worked example.
```

**(b)** Item 51 (perf/stability), append to its Status: reopened early July on both triggers; root cause named in PERF_STABILITY_ROOTCAUSE_SPEC (07-07); P1+P2 canaries live 07-13, P3+P5 live 07-19, freeze root cause isolated 07-20 (main-thread health probes); P4 + F1–F3 remain. `[DELL: confirm Item 51's current wording before appending]`

---

## → MASTER_LOG.md — the July entries (chronological)

## July 6–7, 2026 — Phase-1 edge-reality corrections closed + Barchart removal + perf/synth spec cluster
### CONTEXT
The June Phase-1 finding (swing's realized edge not beating benchmark after corrections) had open correction work; simultaneously Barchart's feed was being retired and app slowness had two fresh triggers.
### WHAT LANDED
- Each-way Part B re-run (PF 1.13, cross-checked, Arm A intact); all SUSPECTED/caveat doc edits committed and pushed (`e6aec9f → 924e0ff → bd08c67` on main); ~/Downloads board reconciled; verified from the cloud. Two de-risks deferred to M2 (open-book mark-to-market; beta-adjusted benchmark) — frozen criteria.
- Barchart removal shipped (CODE_HANDOFF_barchart_swing_removal + barchart_removal_keep_synth, 07-07): synth+live JSONs frozen (3.6MB of the unbounded-data problem eliminated); correlation findings archived.
- PERF_STABILITY_ROOTCAUSE_SPEC delivered: main-thread work scales with unbounded data; cause-fixes + 5 HealthMonitor canaries specced.
- Synth timing exploration run (Apr–Jul archived panel, in-sample): "turn up" signals ANTI-work (knife turns 46.0% vs 49.1% WR at 7d); one positive cell — steady-Buy persistence (≥67 holding: 54.9% WR, +4.36% mean at 21d). SYNTH_TIMING_PREREG_SPEC filed (C1/C2/C3, confirmation ~Aug 18). Consensus logger LIVE 07-07: 155 symbols nightly, idempotent, backed up to trading-data.
- LEAP lane parked 07-07 pending Michael's overhaul: logger-persistence fix (brief delivered), maturity gate (press Check Outcomes post-overhaul), Stage 0.
### CROSS-REFS
CODE_HANDOFF_phase1_corrections_20260707 · PHASE1_EDGE_REALITY_SPEC · PERF_STABILITY_ROOTCAUSE_SPEC_20260707 · SYNTH_TIMING_PREREG_SPEC_20260707 · synth_timing_exploration_findings_20260707

## July 13, 2026 — P1+P2 perf canaries installed + pipeline unwedge
### FIX APPLIED
P1 (Yahoo latency probe: each scan + hourly idle) and P2 (scan-duration trend + telemetry with skipped-count) installed; 7 health probes total. MD5 `40c2b853 → d1a2eeea` (incl. follow-ups). Pipeline unwedge per CODE_HANDOFF_pipeline_unwedge_20260713. `[DELL: one line on what the unwedge fixed — cloud record is thin here]`
### SMOKE CONFIRMED
Verified end-to-end 07-14: perf.json flowing to trading-data (20 entries; baseline forming: 152 symbols ≈134s ≈ 1.14 sym/s; probes 0.6–1.0s green). Story-B baseline accrues automatically from here.

## July 14, 2026 — CRON_TZ found inert (synth logger mistimed) + warm-start brief
### KEY FINDING
Debian cron ignores `CRON_TZ`: the synth logger's "16:30 ET" ran 16:30 IL = 09:30 ET (market OPEN). Jul 7–14 synth records are open-bell snapshots — the Aug-18 confirmation must split pre-fix/post-fix eras, never pool. Cron moved to 23:30 IL = true 16:30 ET.
### ALSO
Warm-start brief delivered (CODE_HANDOFF_warm_start_20260714): persist scan table at scan end, instant restore on launch with age flag (amber >4h), refresh only if stale. Expected before-MD5 9c266fbe. `[DELL: confirm ship date + MD5 chain — see apply-instruction 5]` Autoscan-on-launch brief same window (CODE_HANDOFF_autoscan_on_launch_20260714).

## July 15, 2026 — Swing scan TZ bug fixed same day (dual scans + regime break) — the big one
### CONTEXT
Same CRON_TZ disease, worse patient: the "16:40 ET" swing scan had ALWAYS run 16:40 IL ≈ 09:44 ET — 14 min after the open, scoring a partial intraday bar (journalctl-confirmed).
### FIX APPLIED
Option C same day: 16:40-local stays as evening snapshot; NEW 23:40-local (16:40 ET) authoritative close scan overwrites results + feeds journal history; outcomes check 23:45→23:55; "Scan HH:MM ET" label now truly converts via zoneinfo. Safe by construction — headless scan never writes the signal log (GUI-only).
### KEY FINDING
REGIME BREAK annotated in ROADMAP_swing_trader.md: pre-07-15 headless series are mid-market data; no M2/standing analysis may pool across 2026-07-15 without modeling it.

## July 15, 2026 — Design marathon: PERFORMANCE + SYMBOL ratifications; import-exits root cause; overnight journal-sync build
### WHAT LANDED (decisions, all Michael-ratified)
Tab audit (8→4 endstate); ANALYZE+DIAGNOSE merge ratified after the three-way test proved one shared engine; plain-lingo set as the bar for every panel; verdict pills carry specifics; hollow barbell rail; Details popups everywhere (6 mocked working); SYMBOL and PERFORMANCE mockups approved IN FULL ("all approved"); score-bands panel added (standing daily version of the M2 cross-tab, same 72–84/85–89/90+ cuts); engines panel folded into the Learning-loop pill; query builder DEMOTED to a find bar after Michael challenged the premise (fixed panels + scheduled pre-registered analyses beat a dredging box); mood+bands+recent-calls consolidated to one 3-cut panel. Sweet-spot re-test pre-registered for M2 grounded in ledger IV-3 (the 07-15 lesson: check the ledger before calling a documented finding an eyeball call).
### KEY FINDING — import-exits root cause
The 07-06 "eight exitless imports" root cause FOUND per fix-the-source rule: `journal_sync.py` never computes suggested_exits — its import path calls log_trade without them. Brief delivered (CODE_HANDOFF_import_exits_rootfix): entry-date-ATR exits at the source (backfill_exits math reused) + backfill for CSCO/MRK/NVDA/RIOT + regime-MODE persistence rider.
### ALSO — parallel overnight session
Journal-sync Trades-tab integration built overnight by a parallel Dell session (⟲ Sync Journal button, journal_sync.py imported read-only, report popup, in-app "✔ Apply N change(s)"): the app became the single writer for journal syncs. Confirmed in the 09:20 mirror sync + Michael's morning screenshot.

## July 17, 2026 — Import-exits root fix shipped + sync-apply doubling fix
### FIX APPLIED
Root-cause half of the import-exits work DONE: imports now compute exits at the source; backfill ran. `[DELL: MD5 chain + the four symbols' backfilled levels if in memory]` Sync-apply doubling addressed per CODE_HANDOFF_sync_apply_doubling_20260717. `[DELL: confirm ship + one-line summary]`

## July 19, 2026 — P3+P5 canaries live; first real catches; barbell gauge
### FIX APPLIED
P3 (1s UI heartbeat + daemon watchdog, 3s floor, probe 8) + P5 (/proc CLOSE_WAIT counter + session-baseline growth, probe 9, PROVISIONAL thresholds) live same day as the brief (CODE_HANDOFF_perf_canaries_P3P5_20260719). Mirror MD5 `f132f33a` verified from the cloud at the 16:20 sync. Barbell gauge shipped same day (CODE_HANDOFF_barbell_gauge_20260719). `[DELL: barbell MD5 chain]`
### KEY FINDING
Probe 8 AMBER on its FIRST live run — a real, previously-invisible 9.4s freeze during 152-symbol scan-result finalization: the first quantified measurement of the residual render stall. Second catch same day (barbell run): reproducible 4.0s launch/first-paint stall, proven PRE-EXISTING by counterfactual against the no-gauge backup. Probe 9 green (0 CLOSE_WAIT). Side observation: three rapid launches appeared to trip Yahoo rate-limiting — consistent with the P2 premise.

## July 20, 2026 — PERFORMANCE tab live (stage 1 of 3); Fix-A crons + a silent-cron bug; freeze root cause; exit-config precondition cleared
### FIX APPLIED
- PERFORMANCE tab LIVE (8→7 tabs): 2 pills + 3-cut outcomes panel + combo allow/block table, per the ratified design. `[DELL: MD5 chain]`
- Fix A (learning-loop automation) DONE: nightly recompute_if_dirty + Friday-night headless full backtest crons added. AND it caught a silent infra bug: the existing 23:55 grading cron had NEVER RUN in ~2 weeks — `pgrep -f` self-matched its own `sh -c` wrapper, guard always thought the GUI was up; proof: its log never existed. Fixed with the `[b]racket` idiom on all three lines. Crons: 23:55 grading · 23:58 recompute · Fri 23:30 full backtest — LOCAL time. By-hand runs verified: outcome_analysis.json 07-15→07-20; backtest_results.json 05-11→07-20 (152 universe, PF 1.18 — stale since MAY).
- Precondition cleared: `journal_sync.build_import_exits` switched CFG → `get_exit_config` (grep-proven; sync script never writes that file). Exit-multiplier ratification unblocked.
- SYMBOL stage-2 brief delivered (CODE_HANDOFF_SYMBOL_build_20260720; amended 07-21).
### KEY FINDING
The 6–16s stall family ROOT-CAUSED via the build's A/B testing: `_tick → _refresh_health_dot → HealthMonitor.run_all()` doing NETWORK probes synchronously on the MAIN thread every 5 minutes — pre-existing, reproducible on baseline. (Fix later folded into the SYMBOL build rider.) Why the cron bug stayed silent, per Michael's question: the guard's skip was a legitimate exit-0; nothing checked the OUTPUT side; GUI-path grading partially masked it; P-canaries watch the app, not the automation outside it. Candidate class-fix: cron-heartbeat probe (#10). Follow-up gap: the two new runner scripts are NOT in sync-trading-src.sh's whitelist.

## July 20–22, 2026 — SYMBOL tab build + R-series hardening + dead-code sweep
### FIX APPLIED
SYMBOL tab built per the stage-2 brief (one box replacing four entry surfaces; verdict pair with promoted trigger readout; chart+ladder+shared barbell; five checks; deduped plain-lingo track record; per-symbol backtest with BT± thread; analytics slice; row-click auto-fill; health probes moved OFF the main thread as the rider — P3 proved the fix across two quiet 5-min boundaries). R-series follow-ups briefed 07-21 and applied: R2 treeview/persistent-widget render contract, R3 scan honesty, R4 stall stacks, hygiene-guard UTC. `[DELL: MD5 chains + which R-briefs shipped vs merged into the build]` End state: SIGNALS · SYMBOL · TRADES · PERFORMANCE (7→4 tabs core). Independent cross-lane audit of the build diff by the remote trading-suite session: PASSED; its recommended dead-code sweep satisfied 07-22 (orphan cut `d6f7c25` + `_ui_call` docstring). swing_core legitimately unfrozen 07-22 for the grading attempt-recording commit (`371d5fe`) → new baseline `55ffd7f2`.
### CROSS-REFS
CODE_HANDOFF_SYMBOL_build_20260720(+_AMENDED_0721) · R2/R3/R4 + hygiene briefs 20260721 · stage3 combined-view brief · M2_ANALYSIS_PROTOCOL_draft_20260721

## July 21, 2026 — Market narratives + trigger watch built (trading-suite; cross-repo record)
### WHAT LANDED (separate repo, logged here for the complete picture)
Parallel remote session built in trading-suite (commits `82e1fb0…9e2fec3`; handoff docs/HANDOFF_20260721_narratives_triggers_session.md): (1) push-button per-ticker Claude narratives w/ web search, delivered as GitHub issues (email+push); (2) 30-min market-hours trigger watch merging manual levels + every trade's stop/TP1/TP2 + narrative triggers → issue → phone push; proven live (issue #46). Blocked on Michael: TRADING_DATA_TOKEN secret + first Run workflow.

## July 22, 2026 — Pill narratives brief
### CONTEXT
Deterministic in-app pill sentences layer, complementary to the trading-suite narratives. Brief CODE_HANDOFF_pill_narratives_20260722. `[DELL: ship status + MD5 if landed]`

## July 28, 2026 — Friday backtest backstop never fired (cron/nightly time collision) — FOUND
### KEY FINDING
From the PERFORMANCE pill reading "backtest 8d old (stale)": the Friday headless backstop cron (`30 23 * * 5`, pgrep-guarded) has NEVER executed — it fires 23:30 IL, the same minute as the GUI's 16:30-ET nightly (zones' DST shifts together), so pgrep always short-circuits; the absent log proves it; syslog shows one blocked firing (07-24, its first Friday). Red herring: `state["last_backtest"]` looked fresh because `_bt_run_default` writes state but NOT backtest_results.json — only `_bt_run_sweep` writes the file. Michael chose fix (b): GUI Friday nightly calls `_bt_run_sweep` (one line); the cron stays as-is and becomes a correct closed-app backstop. Brief CODE_HANDOFF_nightly_writes_backtest_file_20260728, superseded by DELL_RUNSHEET_backtest_freshness_20260729. Fixed next day — see the July 29 entry below.

## July 29, 2026 — Fix (b) shipped: nightly writes backtest_results.json; staleness cleared
### FIX APPLIED
One-line change at line 4697 per the runsheet: `stats, _sweep_out = _bt_run_sweep(raw_data)` replaces `_bt_run_default` in `_nightly_bg`'s Friday branch (no commented-out predecessor — the file has no dead-code convention; provenance lives here). MD5 `a10c31d0 → d835bfcc`, backup `swing_trader.py.bak_20260729_fixb`. Crontab untouched (read-only check: backstop `30 23 * * 5` + :20 mirror intact) — the cron is now a correct closed-app backstop; exactly one writer per Friday.
### SMOKE CONFIRMED
Scoring-neutrality interlock PASSED: OHLCV frozen (quote drift can't masquerade as score movement); five proof symbols straddling the min_buy=70 boundary (76/75 above, 65/48/30 below — a leaked CFG override would have flipped a signal); before/after final_scores identical; golden byte-identical (3 fixtures, exact). End-to-end in the real app: no-click driver ran the actual nightly body on its daemon thread inside App+mainloop — file written, zero ui_stall (lifetime count still 4, none on 07-29), launch_first_beat 1.53s in-band; app relaunched clean (1.52s; freshness pill FRESH). Staleness cleared (file age 0d, was 9d). Mirror `d835bfcc` verified from the cloud at the 21:20 sync.
### CONTEXT NOTE
This entry was authored in the cloud because the executing session hit an API 529 immediately before its own logging step. `[DELL: verify against .bak_20260729_fixb + the interlock output if session memory persisted; confirm the Step-0 manual-run OK line from earlier in that session]` First live test of the new coverage: Friday July 31 — app open → GUI writes the file; verify `generated_at` on Saturday.

*(July 28 pill-card session and Item 46 gold+drag: already logged in your two entries — not duplicated here.)*
