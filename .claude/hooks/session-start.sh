#!/bin/bash
# SessionStart hook: report bootstrap state of the four-repo trading platform
# (see CLAUDE.md "The four-repo system") and hand the agent the exact steps.
# Standing authorization to add/clone these repos lives in CLAUDE.md
# ("Related repositories") — keep the REPOS list below in sync with it.
#
# What this hook can and cannot do: PRIVATE repos can only be cloned after the
# model calls the add_repo MCP tool (that is what grants the session's git
# proxy scope) — a shell hook has no channel to the MCP layer, so it never
# attempts those clones. It only pre-warms clones of PUBLIC repos, reports
# per-repo state, and prints the bootstrap directive. A hook clone is NOT
# registration: the agent must still call add_repo + register_repo_root for
# every sibling, or its CLAUDE.md/skills never load and GitHub API tools on
# it fail.
#
# Testing note: an existing session already has scope, so the fresh-session
# path (empty scope, private clones impossible) can only be exercised by a
# brand-new remote session after this file is on the default branch.
set -uo pipefail

# Web/remote sessions only — on the owner's machines the siblings already
# live in the home directory.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

OWNER="rebibomichael-web"
# Keep in sync with CLAUDE.md "Related repositories — add at session start".
# The full four-repo set: the current repo is skipped at runtime, so this
# file can be copied verbatim to the sibling repos.
REPOS="stock-tracker trading-src trading-suite trading-data"
PUBLIC="stock-tracker trading-suite"

# Identify the session's own repo by origin URL (dir basename alone is
# unreliable: forks/worktrees rename the checkout dir).
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
CURRENT_NAME="$(basename "$PROJECT_DIR")"
CURRENT_URL="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"

is_public() { case " $PUBLIC " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# True if nothing under $1/.git changed in the last 120s. A dir that fails
# rev-parse but is still changing is likely a clone mid-transfer (possibly
# started by a previous turn) — never delete those; deleting a live clone is
# the exact hazard the add_repo tool guidance warns about.
quiescent() {
  [ -e "$1/.git" ] || return 0
  [ -z "$(find "$1/.git" -newermt '-120 seconds' -print -quit 2>/dev/null)" ]
}

ONDISK=""
TOCLONE=""
for name in $REPOS; do
  [ "$name" = "$CURRENT_NAME" ] && continue
  case "$CURRENT_URL" in
    *"/$name" | *"/$name.git") continue ;;
  esac
  dest="/workspace/$name"
  if git -C "$dest" rev-parse HEAD >/dev/null 2>&1; then
    echo "[bootstrap] $name: on disk at $dest"
    ONDISK="$ONDISK $name"
    continue
  fi
  if [ -e "$dest" ] && ! quiescent "$dest"; then
    echo "[bootstrap] $name: $dest is still changing — a clone may be in progress; leaving it alone"
    continue
  fi
  # Provably-quiescent leftover from a dead clone — safe to clear (it would
  # otherwise block the agent's later clone into this path).
  [ -e "$dest" ] && rm -rf "$dest"
  if is_public "$name"; then
    if err="$(git clone --depth 1 "https://github.com/$OWNER/$name" "$dest" 2>&1)"; then
      echo "[bootstrap] $name: cloned to $dest (public — still needs add_repo + register_repo_root)"
      ONDISK="$ONDISK $name"
    else
      echo "[bootstrap] $name: clone failed: $(printf '%s' "$err" | tail -2 | tr '\n' ' ')"
      TOCLONE="$TOCLONE $name"
    fi
  else
    echo "[bootstrap] $name: private — cannot be cloned until add_repo grants session scope"
    TOCLONE="$TOCLONE $name"
  fi
done

echo ""
echo "AGENT ACTION REQUIRED — bootstrap the sibling repos (skip any step already done this session):"
if [ -n "$TOCLONE" ]; then
  echo "  Missing from disk:$TOCLONE — for each:"
  echo "    1. call add_repo with {owner: \"$OWNER\", repo: \"<name>\"}"
  echo "    2. run the clone command from add_repo's response (target /workspace/<name>;"
  echo "       trading-src's tests expect trading-data at /workspace/trading-data)"
  echo "    3. call register_repo_root with {owner, repo, directory: \"/workspace/<name>\"}"
fi
if [ -n "$ONDISK" ]; then
  echo "  Already on disk:$ONDISK — for each, call add_repo then register_repo_root with"
  echo "  its /workspace path (loads its CLAUDE.md/skills, enables GitHub API tools on it)."
fi
echo "This is standing authorization from the owner (see CLAUDE.md 'Related repositories')."
