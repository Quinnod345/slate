#!/usr/bin/env bash
# Slate's installer. One command, and safe to run again any time:
#
#   curl -fsSL https://raw.githubusercontent.com/Quinnod345/slate/main/install.sh | bash
#   ~/code/slate/install.sh [--dry-run] [--yes] [--agents claude,codex,openclaw] [--home PATH]
#                           [--exclude-agent ID] [--no-quinn-skills] [--no-jobs-agent] ...
#
# What it does, in order:
#   1. finds the Slate checkout: the folder this script lives in, or ~/code/slate when it's piped in from curl
#      (cloned the first time, fast-forwarded after that);
#   2. makes the checkout's own Python environment with uv (asking before it installs uv with Homebrew);
#   3. hands over to `slate install`: Slate's skills symlinked for Claude Code (~/.claude/skills), Codex and OpenClaw
#      (~/.agents/skills, never ~/.codex/skills), Higgsfield's skills shared with Codex and OpenClaw, the CLI shim in
#      ~/.local/bin and /opt/homebrew/bin, the MCP server registered with each agent that's here, the launchd job
#      runner (a login item, so only with a yes), and the offer to set up visionary and goal-fidelity;
#   4. ends with `slate doctor`. In a real Terminal it offers `slate doctor --install`, which asks before each tool.
#
# Anything in the way that isn't Slate's is moved to a dated backup folder and reported, never deleted. Every other
# skill stays exactly as it is. In an agent (Claude Code, Codex, OpenClaw) nothing prompts: each question becomes a
# printed next step. Nothing here opens a browser, asks for a key, or touches After Effects' preferences.
#
# Exit codes: 0 installed (doctor may still list things to fix), 1 something failed or was refused, 3 a prerequisite
# is missing (git or uv). Environment: SLATE_REPO (clone URL), SLATE_CHECKOUT (where it lives), SLATE_PYTHON (the
# Python uv should use), SLATE_NONINTERACTIVE=1 (never ask).
set -euo pipefail
export BROWSER=none

REPO_URL="${SLATE_REPO:-https://github.com/Quinnod345/slate.git}"
DRY_RUN=0
HOME_ARG=""
PASS=()

say()  { printf '%s\n' "$*"; }
note() { printf 'install.sh: %s\n' "$*" >&2; }
die()  { local code="$1"; shift; printf 'install.sh: %s\n' "$*" >&2; exit "$code"; }
run()  { if [ "$DRY_RUN" = 1 ]; then say "  would run: $*"; else "$@"; fi; }

usage() { sed -n '2,26p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null | sed 's/^# \{0,1\}//' || true; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; PASS+=("$1") ;;
    --home) [ $# -ge 2 ] || die 2 "--home needs a folder"; HOME_ARG="$2"; PASS+=("$1" "$2"); shift ;;
    --home=*) HOME_ARG="${1#--home=}"; PASS+=("$1") ;;
    -h|--help) usage; exit 0 ;;
    *) PASS+=("$1") ;;
  esac
  shift
done

TARGET_HOME="${HOME_ARG:-$HOME}"

# --- Who is running this? ----------------------------------------------------------------------------------------

in_agent() {
  [ -n "${SLATE_NONINTERACTIVE:-}" ] && return 0
  [ -n "${SLATE_CONTEXT:-}" ] && return 0
  [ -n "${CLAUDECODE:-}" ] && return 0
  [ -n "${CLAUDE_CODE_ENTRYPOINT:-}" ] && return 0
  # No `grep -q`: under pipefail its early exit can fail the pipeline even on a match.
  if env | grep -E '^(CODEX_[A-Z_]*|OPENCLAW[A-Z_]*)=' >/dev/null 2>&1; then return 0; fi
  return 1
}

# Questions go to the terminal itself, never stdin: under `curl | bash`, stdin is this script.
can_ask() {
  [ "$DRY_RUN" = 0 ] || return 1
  in_agent && return 1
  ( : </dev/tty ) 2>/dev/null
}

ask() {  # ask "question" default(y|n)
  local reply=""
  printf '%s' "$1" >/dev/tty
  IFS= read -r reply </dev/tty || reply=""
  [ -z "$reply" ] && reply="$2"
  case "$reply" in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

if ! can_ask; then export GIT_TERMINAL_PROMPT=0; fi

# --- 1. The checkout ---------------------------------------------------------------------------------------------

script_dir() {
  local src="${BASH_SOURCE[0]:-}"
  [ -n "$src" ] && [ -f "$src" ] || return 1
  (cd "$(dirname "$src")" && pwd -P)
}

CHECKOUT=""
if dir="$(script_dir)" && [ -f "$dir/pyproject.toml" ] && [ -d "$dir/src/slate" ]; then
  CHECKOUT="$dir"
  say "Slate checkout: $CHECKOUT"
else
  CHECKOUT="${SLATE_CHECKOUT:-$TARGET_HOME/code/slate}"
  command -v git >/dev/null 2>&1 || die 3 "git isn't installed. It comes with Apple's command-line tools: run \`xcode-select --install\`, then this again."
  if [ -d "$CHECKOUT/.git" ]; then
    [ -d "$CHECKOUT/src/slate" ] || die 1 "$CHECKOUT is a git repo but not a Slate checkout, so it's left alone. Set SLATE_CHECKOUT to another folder."
    say "Updating the Slate checkout at $CHECKOUT (fast-forward only)"
    run git -C "$CHECKOUT" pull --ff-only || die 1 "git couldn't fast-forward $CHECKOUT. Look at \`git -C $CHECKOUT status\`, sort it out, then run this again."
  elif [ -e "$CHECKOUT" ]; then
    die 1 "$CHECKOUT exists but isn't a git checkout, so the installer won't touch it. Move it aside (or set SLATE_CHECKOUT), then run this again."
  else
    say "Cloning Slate from $REPO_URL into $CHECKOUT"
    run mkdir -p "$(dirname "$CHECKOUT")"
    run git clone --quiet "$REPO_URL" "$CHECKOUT" || die 1 "git couldn't clone $REPO_URL. For a private repo, sign in to GitHub first (\`gh auth login\`, then \`gh auth setup-git\`), then run this again."
  fi
  if [ "$DRY_RUN" = 1 ] && [ ! -d "$CHECKOUT/src/slate" ]; then
    say "  then: make its environment with uv and run \`slate install --dry-run\` to list every link."
    exit 0
  fi
fi
cd "$CHECKOUT"

# --- 2. The environment ------------------------------------------------------------------------------------------

find_uv() {
  command -v uv 2>/dev/null && return 0
  local c
  for c in /opt/homebrew/bin/uv /usr/local/bin/uv "$TARGET_HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}

UV="$(find_uv || true)"
if [ -z "$UV" ]; then
  if can_ask && command -v brew >/dev/null 2>&1 \
      && ask "uv isn't installed, and Slate's environment is made with it. Install it with Homebrew now? [y/N] " n; then
    brew install uv
    UV="$(find_uv || true)"
  fi
  if [ -z "$UV" ] && [ "$DRY_RUN" = 0 ]; then
    die 3 "uv isn't installed. Install it (\`brew install uv\`, or see docs.astral.sh/uv), then run this again."
  fi
fi

PY="$CHECKOUT/.venv/bin/python"
# Which Python uv builds the environment from: SLATE_PYTHON, else UV_PYTHON, else any 3.11 or newer (uv may fetch one).
PY_REQUEST="${SLATE_PYTHON:-${UV_PYTHON:->=3.11}}"
py_ok() { [ -x "$1" ] && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; }

if py_ok "$PY"; then
  say "Environment: $CHECKOUT/.venv (kept)"
else
  say "Making Slate's environment in $CHECKOUT/.venv"
  if [ -e "$CHECKOUT/.venv" ]; then
    run "${UV:-uv}" venv --quiet --clear --python "$PY_REQUEST" "$CHECKOUT/.venv"
  else
    run "${UV:-uv}" venv --quiet --python "$PY_REQUEST" "$CHECKOUT/.venv"
  fi
fi
say "Installing Slate and its Python packages into that environment"
if [ "$DRY_RUN" = 1 ]; then
  run "${UV:-uv}" pip install --python "$PY" -e "$CHECKOUT[ocr]"
elif ! "$UV" pip install --quiet --python "$PY" -e "$CHECKOUT[ocr]"; then
  note "the ocr extra (ocrmac, Apple Vision) didn't install, so OCR gates will say UNCHECKED until it does."
  "$UV" pip install --quiet --python "$PY" -e "$CHECKOUT" || die 1 "uv couldn't install Slate into $CHECKOUT/.venv (see above)."
fi

# --- 3. slate install ---------------------------------------------------------------------------------------------

find_python() {
  local c
  for c in "${SLATE_PYTHON:-}" python3.13 python3.12 python3.11 python3.14 python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 && py_ok "$(command -v "$c")"; then command -v "$c"; return 0; fi
  done
  [ -n "$UV" ] && "$UV" python find '>=3.11' 2>/dev/null && return 0
  return 1
}

EXTRA=()
if can_ask; then EXTRA+=(--interactive); fi
RC=0
if [ "$DRY_RUN" = 1 ] && ! py_ok "$PY"; then
  # No environment yet, so show the plan with any Python 3.11+ on this Mac: the installer needs only the standard library.
  if SHOWPY="$(find_python)"; then
    PYTHONPATH="$CHECKOUT/src" "$SHOWPY" -m slate install --no-doctor ${PASS[@]+"${PASS[@]}"} || RC=$?
  else
    say "  then: \`slate install --dry-run\` lists every link (no Python 3.11+ here to show it now)."
  fi
  exit "$RC"
fi
"$PY" -m slate install --no-doctor ${EXTRA[@]+"${EXTRA[@]}"} ${PASS[@]+"${PASS[@]}"} || RC=$?

# --- 4. slate doctor ----------------------------------------------------------------------------------------------

if [ "$DRY_RUN" = 1 ]; then
  say "  then: slate doctor"
  exit "$RC"
fi

say ""
if HOME="$TARGET_HOME" "$PY" -m slate doctor --help >/dev/null 2>&1; then
  if can_ask && ask "Run \`slate doctor --install\` now? It installs the free tools that are missing (Homebrew tools, the engine, the Whisper model) and asks before each one. [y/N] " n; then
    HOME="$TARGET_HOME" "$PY" -m slate doctor --install </dev/tty || true
  else
    HOME="$TARGET_HOME" "$PY" -m slate doctor || true
    say ""
    say "When you're at a Terminal, \`slate doctor --install\` installs the free tools it lists (it asks first)."
  fi
else
  say "slate doctor isn't in this checkout yet, so the final check was skipped (UNCHECKED, not passed)."
fi
exit "$RC"
