#!/usr/bin/env bash
# Set up visionary and goal-fidelity in every personal agent, once, with the owner's yes (PLAN decision 10).
#
#   scripts/setup_quinn_skills.sh --yes [--exclude-agent ID]... [--no-exclusions] [--repo DIR]
#   scripts/setup_quinn_skills.sh --dry-run          (shows every step; changes nothing)
#
# 1. Makes ~/code/quinn-skills its own small git repo holding the two skills, copied from where they live now. It's
#    separate from Slate and shareable, and Claude maintains it. Once it exists it's the source of truth: a second
#    run never overwrites it.
# 2. Symlinks each skill into ~/.claude/skills (Claude Code) and ~/.agents/skills (Codex and OpenClaw). The copies
#    they replace are moved to a dated backup folder, never deleted. Nothing is linked into ~/.codex/skills; a copy
#    found there is moved aside too, because Codex reads both folders and would list the skill twice.
# 3. Keeps company agents out. ~/.agents/skills reaches every agent on the OpenClaw gateway, so each agent named with
#    --exclude-agent gets a skill allowlist pinned to exactly what it sees today (it stays as it is), and is checked
#    afterwards. With several OpenClaw agents and no decision (--exclude-agent, or --no-exclusions), the
#    ~/.agents/skills links wait and the next step is printed. Pinning also means skills that agent learns later
#    need adding to its list by hand: `openclaw config get agents.entries.<id>.skills` shows it.
#
# Every other skill stays exactly as it is. Safe to run again. In an agent nothing prompts; without --yes nothing
# changes. Environment: OPENCLAW_BIN (the openclaw CLI; set it empty to skip OpenClaw), SLATE_DATA (where backups go),
# QUINN_SKILLS_REPO (the repo folder).
#
# Exit codes: 0 done (or nothing to do), 1 refused or a check failed, 2 usage, 3 a skill or tool is missing.
set -euo pipefail
export BROWSER=none

SKILLS="visionary goal-fidelity"
REPO_DIR="${QUINN_SKILLS_REPO:-$HOME/code/quinn-skills}"
DATA_DIR="${SLATE_DATA:-$HOME/Library/Application Support/Slate}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$DATA_DIR/backups/$STAMP-quinn-skills"
YES=0
DRY_RUN=0
INTERACTIVE=0
NO_EXCLUSIONS=0
EXCLUDES=""

say()  { printf '%s\n' "$*"; }
warn() { printf 'setup_quinn_skills: %s\n' "$*" >&2; }
die()  { local code="$1"; shift; printf 'setup_quinn_skills: %s\n' "$*" >&2; exit "$code"; }
act()  { if [ "$DRY_RUN" = 1 ]; then say "  would: $*"; else say "  $*"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --interactive) INTERACTIVE=1 ;;
    --no-exclusions) NO_EXCLUSIONS=1 ;;
    --exclude-agent) [ $# -ge 2 ] || die 2 "--exclude-agent needs an agent id"; EXCLUDES="$EXCLUDES $2"; shift ;;
    --exclude-agent=*) EXCLUDES="$EXCLUDES ${1#--exclude-agent=}" ;;
    --repo) [ $# -ge 2 ] || die 2 "--repo needs a folder"; REPO_DIR="$2"; shift ;;
    -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die 2 "unknown option: $1 (see --help)" ;;
  esac
  shift
done

# Links are absolute, so the repo folder must be too.
case "$REPO_DIR" in /*) ;; *) REPO_DIR="$(pwd -P)/$REPO_DIR" ;; esac
REPO_DIR="${REPO_DIR%/}"

# A Python for reading OpenClaw's JSON: Slate's own environment when it's there, else the system's.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYJSON=""
for c in "$SCRIPT_DIR/../.venv/bin/python" "$(command -v python3 2>/dev/null || true)" /usr/bin/python3; do
  if [ -n "$c" ] && [ -x "$c" ]; then PYJSON="$c"; break; fi
done
if [ "${OPENCLAW_BIN+set}" = set ]; then OPENCLAW="$OPENCLAW_BIN"; else OPENCLAW="$(command -v openclaw 2>/dev/null || true)"; fi

in_agent() {
  [ -n "${SLATE_NONINTERACTIVE:-}${SLATE_CONTEXT:-}${CLAUDECODE:-}${CLAUDE_CODE_ENTRYPOINT:-}" ] && return 0
  if env | grep -E '^(CODEX_[A-Z_]*|OPENCLAW[A-Z_]*)=' >/dev/null 2>&1; then return 0; fi
  return 1
}
can_ask() { [ "$INTERACTIVE" = 1 ] && ! in_agent && ( : </dev/tty ) 2>/dev/null; }
ask() {
  local reply=""
  printf '%s' "$1" >/dev/tty
  IFS= read -r reply </dev/tty || reply=""
  case "$reply" in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

real_dir() { (cd "$1" 2>/dev/null && pwd -P); }

# --- The owner's yes ---------------------------------------------------------------------------------------------

if [ "$YES" = 0 ] && [ "$DRY_RUN" = 0 ]; then
  if can_ask && ask "Set up visionary and goal-fidelity in every personal agent (a repo of their own in $REPO_DIR, symlinked; originals backed up, never deleted)? [y/N] "; then
    YES=1
  else
    die 1 "this changes your skill folders, so it waits for the owner's yes. Nothing changed. See the plan with --dry-run, then run it with --yes once they've said yes."
  fi
fi

say "visionary and goal-fidelity -> $REPO_DIR$([ "$DRY_RUN" = 1 ] && printf ' (dry run: nothing changes)')"

# --- 1. Where each skill lives now --------------------------------------------------------------------------------

source_for() {  # the folder to copy a skill from: the repo's own copy first, then a real copy in an agent folder
  local name="$1" c
  if [ -f "$REPO_DIR/skills/$name/SKILL.md" ]; then printf '%s\n' "$REPO_DIR/skills/$name"; return 0; fi
  for c in "$HOME/.claude/skills/$name" "$HOME/.agents/skills/$name" "$HOME/.codex/skills/$name"; do
    if [ -f "$c/SKILL.md" ]; then real_dir "$c"; return 0; fi
  done
  return 1
}

for name in $SKILLS; do
  source_for "$name" >/dev/null || die 3 "can't find $name: no SKILL.md in $REPO_DIR/skills, ~/.claude/skills, ~/.agents/skills or ~/.codex/skills."
done

# --- 2. Company agents on the OpenClaw gateway (only reads here: nothing changes until every check has passed) -----

json_py() {  # json_py '<python expression over d>' < json
  [ -n "$PYJSON" ] || die 3 "no Python to read OpenClaw's JSON with (python3 isn't installed)."
  "$PYJSON" -c "
import json, sys
raw = sys.stdin.read()
starts = [i for i in (raw.find('{'), raw.find('[')) if i >= 0]
d = json.loads(raw[min(starts):]) if starts else None
out = $1
if isinstance(out, (list, tuple)):
    print('\n'.join(str(x) for x in out))
elif out is not None:
    print(out)
"
}

OC_AGENTS=""
if [ -n "$OPENCLAW" ] && [ -x "$OPENCLAW" ]; then
  if entries="$("$OPENCLAW" config get agents.entries --json 2>/dev/null)"; then
    OC_AGENTS="$(printf '%s' "$entries" | json_py "sorted(d) if isinstance(d, dict) else []" || true)"
  fi
fi
OC_COUNT=0
for a in $OC_AGENTS; do OC_COUNT=$((OC_COUNT + 1)); done

AGENTS_OK=1
if [ "$OC_COUNT" -gt 0 ]; then
  for id in $EXCLUDES; do
    case " $(printf '%s ' $OC_AGENTS)" in *" $id "*) ;; *) die 2 "OpenClaw has no agent called '$id' (it has: $(printf '%s ' $OC_AGENTS)). Nothing changed." ;; esac
  done
  if [ -z "$EXCLUDES" ] && [ "$NO_EXCLUSIONS" = 0 ] && [ "$OC_COUNT" -gt 1 ]; then
    AGENTS_OK=0
  fi
elif [ -n "$EXCLUDES" ]; then
  warn "OpenClaw isn't here (or has no agents), so there's nothing to exclude: ignoring --exclude-agent."
  EXCLUDES=""
fi

PINS=""  # one line per agent to pin: "<id> <JSON list of the skills it sees today>"
check_agent() {  # decide, without changing anything, how to keep one OpenClaw agent exactly as it is
  local id="$1" current visible
  if current="$("$OPENCLAW" config get "agents.entries.$id.skills" --json 2>/dev/null)" \
      && [ "$(printf '%s' "$current" | json_py "'list' if isinstance(d, list) else ''")" = list ]; then
    if printf '%s' "$current" | json_py "[n for n in d if n in ('visionary', 'goal-fidelity')]" | grep . >/dev/null; then
      die 1 "OpenClaw agent '$id' has an allowlist that names visionary or goal-fidelity. Take them out of it first (openclaw config get agents.entries.$id.skills). Nothing changed."
    fi
    say "  OpenClaw agent '$id' already has its own skill allowlist without them: nothing to change there."
    return 0
  fi
  # One parse, straight to the JSON list: if anything about it fails, nothing is pinned, linked or created.
  visible="$("$OPENCLAW" skills list --agent "$id" --json 2>/dev/null \
    | json_py "json.dumps(sorted({s['name'] for s in d['skills'] if not s.get('blockedByAgentFilter') and s.get('name') not in ('visionary', 'goal-fidelity')}), separators=(',', ':'))")" \
    || die 1 "couldn't list the skills OpenClaw agent '$id' sees, so nothing changed."
  case "$visible" in \[*\]) ;; *) die 1 "OpenClaw's skill list for '$id' didn't make sense, so nothing changed." ;; esac
  PINS="$PINS$id $visible
"
}

if [ "$AGENTS_OK" = 1 ]; then
  for id in $EXCLUDES; do check_agent "$id"; done
fi

# --- 3. The repo -------------------------------------------------------------------------------------------------

write_repo_docs() {
  cat > "$REPO_DIR/README.md" <<'EOF'
# quinn-skills

Two of Quinn's skills, kept in one small repo of their own so every personal agent reads the same copy:

- `visionary`: reframe the problem, generate genuinely non-obvious ideas across many lenses, kill the median answer,
  and push one idea to a distinctive signature.
- `goal-fidelity`: capture every requirement of a multi-part request up front and verify each one before finishing,
  so a big goal is never half-delivered.

## How they're wired

Each skill folder is symlinked, never copied, into:

- `~/.claude/skills/<name>` for Claude Code;
- `~/.agents/skills/<name>` for Codex and OpenClaw (Oneiro).

Never `~/.codex/skills`: both Codex binaries read that folder as well, so a skill there would be listed twice. Company
agents don't get these skills: an OpenClaw agent kept out has a skill allowlist that doesn't name them.

Slate's `scripts/setup_quinn_skills.sh` set this up. It's safe to run again: when everything is in place it changes
nothing.

## Maintaining them

Claude maintains these skills. Edit them here, in this repo, and every agent sees the change at once. Keep each
`SKILL.md` under 500 lines with spec-only frontmatter (`name` and `description`), use plain relative paths, and don't
name tools only one agent has. They're meant to be shareable: no company material, no machine-specific paths and no
secrets. Commit each change with a message that says what changed and why.
EOF
  cat > "$REPO_DIR/AGENTS.md" <<'EOF'
# Working in quinn-skills

This repo holds two shareable skills (`skills/visionary`, `skills/goal-fidelity`) that every personal agent reads
through symlinks. Edit them here, never a copy elsewhere. Keep `SKILL.md` under 500 lines with spec-only frontmatter
(`name`, `description`), agent-neutral wording, and no company material, machine paths or secrets. Commit every
change with a clear message. Don't add these skills to any company agent.
EOF
  printf '.DS_Store\n' > "$REPO_DIR/.gitignore"
}

git_commit() {  # git_commit "message": uses the person's own git identity when there is one
  if git -C "$REPO_DIR" config user.email >/dev/null 2>&1; then
    git -C "$REPO_DIR" commit --quiet -m "$1"
  else
    git -C "$REPO_DIR" -c user.name="quinn-skills setup" -c user.email="quinn-skills-setup@localhost" \
      commit --quiet -m "$1"
  fi
}

if [ ! -d "$REPO_DIR/.git" ]; then
  if [ -e "$REPO_DIR" ] && [ -n "$(ls -A "$REPO_DIR" 2>/dev/null)" ]; then
    die 1 "$REPO_DIR exists and isn't a git repo, so it's left alone. Move it aside, or pass --repo DIR."
  fi
  command -v git >/dev/null 2>&1 || die 3 "git isn't installed (it comes with Apple's command-line tools: xcode-select --install)."
  act "create the git repo $REPO_DIR"
  for name in $SKILLS; do act "copy $name from $(source_for "$name") into $REPO_DIR/skills/$name"; done
  if [ "$DRY_RUN" = 0 ]; then
    mkdir -p "$REPO_DIR/skills"
    for name in $SKILLS; do cp -R "$(source_for "$name")" "$REPO_DIR/skills/$name"; done
    write_repo_docs
    git -C "$REPO_DIR" init --quiet
    git -C "$REPO_DIR" add -A
    git_commit "Start quinn-skills: visionary and goal-fidelity, shared by every personal agent"
  fi
else
  say "  $REPO_DIR is already a repo; it's the source of truth and stays as it is."
  for name in $SKILLS; do
    if [ ! -f "$REPO_DIR/skills/$name/SKILL.md" ]; then
      act "add $name to the repo from $(source_for "$name")"
      if [ "$DRY_RUN" = 0 ]; then
        mkdir -p "$REPO_DIR/skills"
        cp -R "$(source_for "$name")" "$REPO_DIR/skills/$name"
        git -C "$REPO_DIR" add "skills/$name"
        git_commit "Add $name"
      fi
    fi
  done
fi

# --- 4. Pin each company agent to what it sees today ------------------------------------------------------------

while read -r id json; do
  [ -n "$id" ] || continue
  count="$(printf '%s' "$json" | json_py "len(d)")"
  act "pin OpenClaw agent '$id' to the $count skills it sees today (openclaw config set agents.entries.$id.skills ...)"
  if [ "$DRY_RUN" = 0 ]; then
    "$OPENCLAW" config set "agents.entries.$id.skills" "$json" --strict-json --expect-current-absent >/dev/null \
      || die 1 "OpenClaw wouldn't take the allowlist for '$id', so nothing was linked into ~/.agents/skills."
  fi
done <<EOF
$PINS
EOF

# --- 5. The links ------------------------------------------------------------------------------------------------

link_one() {  # link_one <root> <label> <name>
  local root="$1" label="$2" name="$3" target src
  target="$root/$name"
  src="$REPO_DIR/skills/$name"
  if [ -L "$target" ] && [ "$(readlink "$target")" = "$src" ]; then
    say "  kept $target (already linked)"
    return 0
  fi
  if [ -e "$target" ] || [ -L "$target" ]; then
    act "move $target to $BACKUP_DIR/$label/$name (backed up, not deleted)"
    if [ "$DRY_RUN" = 0 ]; then
      mkdir -p "$BACKUP_DIR/$label"
      mv "$target" "$BACKUP_DIR/$label/$name"
    fi
  fi
  act "link $target -> $src"
  if [ "$DRY_RUN" = 0 ]; then
    mkdir -p "$root"
    ln -s "$src" "$target"
  fi
}

for name in $SKILLS; do link_one "$HOME/.claude/skills" claude-skills "$name"; done
if [ "$AGENTS_OK" = 1 ]; then
  for name in $SKILLS; do link_one "$HOME/.agents/skills" agents-skills "$name"; done
  for name in $SKILLS; do
    dup="$HOME/.codex/skills/$name"
    if [ -e "$dup" ] || [ -L "$dup" ]; then
      act "move $dup to $BACKUP_DIR/codex-skills/$name (Codex reads both folders and would list $name twice)"
      if [ "$DRY_RUN" = 0 ]; then mkdir -p "$BACKUP_DIR/codex-skills"; mv "$dup" "$BACKUP_DIR/codex-skills/$name"; fi
    fi
  done
fi

# --- 6. Check ----------------------------------------------------------------------------------------------------

STATUS=0
if [ "$DRY_RUN" = 0 ]; then
  check_root() {
    local root="$1" name
    for name in $SKILLS; do
      if [ "$(real_dir "$root/$name" || true)" != "$(real_dir "$REPO_DIR/skills/$name")" ]; then
        warn "CHECK FAILED: $root/$name doesn't reach $REPO_DIR/skills/$name."
        STATUS=1
      fi
    done
  }
  check_root "$HOME/.claude/skills"
  if [ "$AGENTS_OK" = 1 ]; then check_root "$HOME/.agents/skills"; fi
  if [ "$AGENTS_OK" = 1 ] && [ -n "$EXCLUDES" ]; then
    for id in $EXCLUDES; do
      seen="$("$OPENCLAW" skills list --agent "$id" --json 2>/dev/null \
        | json_py "[s['name'] for s in d.get('skills', []) if s.get('name') in ('visionary', 'goal-fidelity') and not s.get('blockedByAgentFilter')]" || printf 'unknown')"
      if [ -n "$seen" ]; then
        warn "CHECK FAILED: OpenClaw agent '$id' can still see: $seen. Fix its allowlist before anything else."
        STATUS=1
      else
        say "  checked: OpenClaw agent '$id' doesn't see them."
      fi
    done
  fi
fi

if [ "$AGENTS_OK" = 0 ]; then
  say ""
  say "Waiting: ~/.agents/skills reaches every agent on the OpenClaw gateway ($(printf '%s ' $OC_AGENTS | sed 's/ $//')),"
  say "and company agents must never get these skills. Name the ones to keep out, then run this again:"
  say "  $0 --yes --exclude-agent <id>   (repeat for each), or --no-exclusions if every agent may have them."
  say "Claude Code already has them."
fi
if [ "$STATUS" = 0 ] && [ "$DRY_RUN" = 0 ]; then
  say "Done. New sessions in Claude Code, Codex and OpenClaw pick them up; every other skill is as it was."
  [ -d "$BACKUP_DIR" ] && say "Originals are in $BACKUP_DIR."
fi
exit "$STATUS"
