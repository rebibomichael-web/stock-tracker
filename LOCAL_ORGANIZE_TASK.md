# First local session — organize the trading project and create the standing file map

**How to use (Michael):** install Claude Code on your PC, open a terminal in the trading
project's root folder, run `claude`, and paste this whole file as the first task.

## Goal

One canonical file — **`PROJECT_MAP.md`** in the project root — that always reflects
where every file lives and where every new file goes (including downloads). Every future
session keeps it current. This file is the answer to "where does this go?" forever.

## Step 1 — Inventory (read-only, nothing moves yet)

- Walk the entire project tree. List every file with size and last-modified date.
- Group by kind: source code, backtest outputs, planning docs, findings/experiment
  records, journal/database files, market-data files, downloads, one-off scripts, junk.
- Flag duplicates and stale copies (e.g. multiple versions of the same script,
  `*_old`, `*_backup`, `(1)` download copies). Flag only — delete nothing.
- Check the Downloads folder and desktop for project files that never got filed.

## Step 2 — Propose the target structure (get a yes before moving)

Seed proposal — adjust to what Step 1 actually finds:

```
trading-src/
├── src/                  # swing_trader.py, leap_strategy.py, shared modules
├── backtests/            # backtest runners, configs, dated output files
├── data/
│   └── inbox/            # ← landing zone: every download goes here first, then gets filed
├── journal/              # SQLite FIFO journal and exports
├── docs/
│   ├── planning/         # MASTER_STATUS_BOARD.md, MASTER_LOG, CHANGE_LIST_CONSOLIDATED, TODO
│   └── findings/         # tech_anal_vs_atr_findings.md, audits, R1_HANDOFF, experiment records
├── _attic/               # quarantine for suspected junk — never deleted, just parked
├── PROJECT_MAP.md        # ← the one standing file
└── CLAUDE.md             # standing instructions every session reads
```

Show Michael the proposal plus the full move list (old path → new path) and wait for
approval before touching anything.

## Step 3 — Reorganize (only after approval)

- Use `git mv` so history is kept. Move, never delete — true junk goes to `_attic/`.
- After moving, grep the codebase for hard-coded old paths and fix them.
- Verify the hourly auto-sync to the `trading-src` GitHub repo still points at the right
  root and still runs. Do not break the sync.
- Commit the reorganization as its own commit, before any other work.

## Step 4 — Write `PROJECT_MAP.md`

It must contain:

1. The directory tree with a one-line purpose for each folder.
2. A **routing table** — "when a new file arrives, where does it go":

   | Incoming file | Goes to |
   |---|---|
   | Broker statement / trade export | `journal/` (or per Step 1 findings) |
   | Downloaded market data / scans | `data/` |
   | Anything downloaded, unsorted | `data/inbox/` → file it from there same day |
   | New experiment result / findings | `docs/findings/` |
   | Planning / status updates | `docs/planning/` |
   | New strategy code | `src/` |

3. Pointers to canonical copies: the findings file, the status board, this map itself.
4. A "Last updated" date at the top.

## Step 5 — Write / extend `CLAUDE.md` (this is what makes it permanent)

Add this stanza so every future session enforces the order automatically:

```markdown
## File organization — standing rule
PROJECT_MAP.md is the single source of truth for where files live. Whenever you
create, move, or receive a file, place it per the map's routing table and update
PROJECT_MAP.md in the same commit. When Michael downloads a file and asks where
it goes, answer from the routing table. If a file doesn't fit any rule, put it in
data/inbox/, then propose a new rule for the map.

## Related repository
The deployed dashboard lives in the separate repo `rebibomichael-web/stock-tracker`
(app.py on Render). Its R1 findings history is on branch claude/intelligent-curie-j1hsfo.
```

## Known inventory (from cloud-session records — incomplete seed, verify in Step 1)

~135 files were pushed to `trading-src` on 2026-07-02, including: `swing_trader.py`
(ATR gate, exit modes, Item 4b machinery), `leap_strategy.py`, `MASTER_STATUS_BOARD.md`,
planning docs (`MASTER_LOG`, `CHANGE_LIST_CONSOLIDATED`, TODO), and a SQLite FIFO journal
exists somewhere in the project. `tech_anal_vs_atr_findings.md` and
`R1_HANDOFF_2026-07-02.md` currently live in the `stock-tracker` repo, branch
`claude/intelligent-curie-j1hsfo` — copy them into `docs/findings/` as part of Step 3
so the hourly sync carries them into `trading-src`.

## Boundaries

- Delete nothing, ever — `_attic/` instead.
- Move only after Michael approves the move list.
- Keep the hourly auto-sync working.
- Do NOT start the R1 experiment in this task. R1 runs as a separate task afterwards,
  per `R1_HANDOFF_2026-07-02.md` (which by then should be in `docs/findings/`).
