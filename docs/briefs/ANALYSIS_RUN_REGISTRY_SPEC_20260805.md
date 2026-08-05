# ANALYSIS RUN REGISTRY — "every analysis run is loud and clear to the rest of the programs"
**Spec, cloud, 2026-08-05. Michael's directive: "we need to create structure first — any anal[ysis] run is loud and clear to rest of programs." Companion to inbox/BANNER_RUN_BUTTON_SPEC.md (launch side) — this is the completion/announcement side. For the Dell to implement after Michael approves.**

## The problem it solves (this week's demo)
M2 ran 08-03 and produced a rigorous report — and the only system-visible trace was a *stale* banner still saying "Run the standing analysis," because the run's existence lived in a doc footer and an unmade manual `--mark-done`. Michael read the banner as a bug. The run was silent to every program. Same disease class as the silent schedulers, on the analysis lane.

## Design: one registry file, many consumers

### The registry (the only new artifact)
`~/.michael_analysis_runs.json` — **append-only** list of run records (the fidelity_ledger philosophy: prior entries never change). Written by the analysis itself as its LAST step. Schema per record:

```json
{ "run_id": "M2-20260803", "kind": "milestone|s-lane|adhoc-prereg",
  "date": "2026-08-03", "script": "analysis/standing_analysis_milestone2_20260803.py",
  "script_md5": "…", "report": "docs/findings/swing_analysis_milestones.md#milestone-2",
  "status": "awaiting_approval",       // → "approved" | "superseded"
  "headline": "0 graduated · 2 M1 headliners failed OOS · 3 new Suspected",
  "approved_on": null }
```

Rules: writing a record is part of the run (a run that didn't stamp didn't finish — same absent-log lesson); `status` transitions are the ONLY permitted mutation; the file joins the trading-data backup whitelist.

### Consumers (each is a small, separate change)
1. **milestone_banner.py becomes state-aware (the fix for "why banner?").** Three states instead of one:
   - DUE, no unapproved run → today's banner (+ the ▶Run button per the inbox spec).
   - Registry has `awaiting_approval` milestone run → banner flips to: **"⚑ M2 ran 08-03 — report awaiting YOUR review → [Approve & mark done] [Open report]"**. Approve = `--mark-done` + status→approved, stamping `last_analysis_date` with the RUN date (not click date — fixes the drift noted 08-05).
   - Nothing due, nothing pending → no banner (unchanged).
2. **PERFORMANCE learning-loop pill** gains one line from the registry: "analysis: M2 08-03 awaiting approval" / "approved ✓" — the output-side gauge for the analysis lane.
3. **Probe #10 (when built)** treats registry cadence as one more heartbeat: milestone stamp older than ~25 trading days → amber. Run-evidence, not artifacts.
4. **S-lane runs stamp too** (`kind: "s-lane"`, one record per run, headline = modules run + verdict deltas), so the S5 watchlist verdicts arriving in ~2–3 weeks announce themselves instead of waiting to be noticed.

### Explicitly NOT in scope
No change to the 20-trading-day trigger, the frozen methodology, or approval-before-mark discipline (M1 precedent stays — the registry makes the pending state VISIBLE, it does not skip Michael). No auto-run of anything.

---

# Part 2 — M3 SPEC AMENDMENT (proposed): pre-registered curiosity
**Addresses Michael's M2 critique verbatim: "minimal curiosity, i.e. if this and that what then, and had to prod repeatedly … my goal was a very thorough set of criteria that would pull data and then a formulaic analysis looking for patterns; what I got was prod research response instead."**

Add to STANDING_ANALYSIS_SPEC (as amendment 2, alongside the pending ledger amendment):

1. **Branch tables, pre-registered.** Every module M1–M11 + S1–S10 gets an explicit `IF finding crosses X → ALSO run Y` table written into the spec BEFORE the run. Examples seeded from what Michael had to prod for at M2: a band result → automatically run band-free splits AND the same cut on the signal population, not just trades; an exit verdict → automatically run the path-level view (dip depth, rescue rate, day-by-day grid 7/14/21/28); any "insufficient n" → automatically report the n it WOULD need and which future milestone reaches it at current fill rate.
2. **Mandatory curiosity section per module.** Each module's report section ends with "the three questions this result raises," and any of the three computable from already-loaded data MUST be answered in the same run. Not optional prose — a checklist the run self-audits.
3. **The formulaic sweep IS the deliverable.** The session's job is to execute the criteria exhaustively and surface pattern candidates; conversation is for verdicts and new hypotheses, not for extracting the next obvious cut. A milestone report with an empty curiosity section is an incomplete run.
4. **Registry stamping** (Part 1) is a numbered step of the protocol — after the ledger update, before the footer.

Approval path: Michael approves alongside the M2 report + the M3 ledger amendment (one sitting, three approvals). Then a Dell brief implements Part 1's registry + banner consumer; the spec amendments are doc edits.
