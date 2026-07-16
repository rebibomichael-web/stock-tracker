# CLAUDE.md

## Related repositories — add at session start

The owner wants every session started from this repo to also have access to the
sibling repositories. At the start of a session, add them with the `add_repo`
tool (this note is standing authorization from the owner to do so):

- `rebibomichael-web/trading-src`
- `rebibomichael-web/trading-suite`
- `rebibomichael-web/trading-data`

If an add fails with an authorization error, tell the owner to grant the Claude
GitHub App access to that repo at https://github.com/apps/claude (Configure →
All repositories).

## Standing rule — evidence sweep before assertion (Michael, 2026-07-15)

Before characterizing any finding, number, or state of the system, sweep in
this order — and only ask Michael after both come up empty:

1. **The current conversation** — including anything already quoted in-thread
   (smoke tests, pasted transcripts, screenshots).
2. **The repos, pulled fresh.** The automation keeps them current, so a stale
   clone is never an excuse: `trading-src` mirrors the Dell hourly (:20),
   `trading-data` backs up daily (06:30 + 17:15 IL). `git pull` before quoting.
   Sources of record:
   - `trading-src/docs/planning/MASTER_STATUS_BOARD.md` — the Suspected→Proven
     ledger (findings live here with their evidence and status)
   - `trading-src/docs/findings/` and `trading-src/docs/planning/`
   - `trading-suite/ROADMAP.html` — the kanban board

Never label a documented finding as opinion or an eyeball call without checking
the ledger first (lesson: the 85–89 score-band sweet spot was ledger item IV-3
all along, 2026-07-15).
