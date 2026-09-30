#!/usr/bin/env bash
# Undo Slate's installer, and only that:
#
#   ~/code/slate/uninstall.sh [--dry-run] [--home PATH] [--yes] [--keep-backups]
#
# Removes Slate's skill links (in ~/.claude/skills and ~/.agents/skills), the Higgsfield links the installer made,
# the CLI shims (~/.local/bin/slate, /opt/homebrew/bin/slate), Slate's MCP registrations and its launchd job runner.
# Anything the installer moved aside goes back where it was, when that spot is free again. Your other skills, this
# checkout and its environment, your films, brand packs and Slate's data folder all stay. Run with --dry-run first to
# see the list.
#
# It runs `slate uninstall` from this checkout's environment. If that environment is gone, a plain fallback removes
# Slate's skill links and shims (the two things it can recognise without Python) and prints the rest to do by hand.
set -euo pipefail
export BROWSER=none

CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PY="$CHECKOUT/.venv/bin/python"

if [ -x "$PY" ] && "$PY" -c 'import slate.install' 2>/dev/null; then
  exec "$PY" -m slate uninstall "$@"
fi

DRY_RUN=0
TARGET_HOME="$HOME"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --home) TARGET_HOME="$2"; shift ;;
    --home=*) TARGET_HOME="${1#--home=}" ;;
    *) ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
# This account's real home, from the user database (HOME can point anywhere). A different home is a sandbox, and a
# sandbox never reaches this Mac's /opt/homebrew/bin.
eval "ACCOUNT_HOME=~$(id -un)"
real_path() { (cd "$1" 2>/dev/null && pwd -P) || printf '%s\n' "$1"; }
SANDBOX=0
[ "$(real_path "$TARGET_HOME")" = "$(real_path "$ACCOUNT_HOME")" ] || SANDBOX=1
say "Slate's environment isn't there, so this is the plain fallback: it removes only links into $CHECKOUT/skills"
say "and shims that carry Slate's marker."
for root in "$TARGET_HOME/.claude/skills" "$TARGET_HOME/.agents/skills"; do
  [ -d "$root" ] || continue
  for entry in "$root"/*; do
    [ -L "$entry" ] || continue
    case "$(readlink "$entry")" in
      "$CHECKOUT/skills/"*)
        if [ "$DRY_RUN" = 1 ]; then say "  would remove $entry"; else rm "$entry"; say "  removed $entry"; fi ;;
    esac
  done
done
for shim in "$TARGET_HOME/.local/bin/slate" "/opt/homebrew/bin/slate"; do
  if [ "$SANDBOX" = 1 ] && [ "$shim" = "/opt/homebrew/bin/slate" ]; then
    shim="$TARGET_HOME/opt/homebrew/bin/slate"
  fi
  { [ -f "$shim" ] && [ ! -L "$shim" ] && grep 'slate-shim v1' "$shim" >/dev/null 2>&1; } || continue
  if [ "$DRY_RUN" = 1 ]; then say "  would remove $shim"; else rm "$shim"; say "  removed $shim"; fi
done
say "Still to do by hand, where they apply:"
say "  claude mcp remove --scope user slate"
say "  codex mcp remove slate"
say "  openclaw mcp unset slate"
say "  launchctl bootout gui/$(id -u)/dev.slate.jobs && rm ~/Library/LaunchAgents/dev.slate.jobs.plist"
say "  Higgsfield links in ~/.agents/skills that point at ~/.claude/skills (the installer made them)"
