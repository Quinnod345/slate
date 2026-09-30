#!/usr/bin/env bash
# The clean-machine test, scripted: install Slate into a brand-new home the way `curl ... | bash` would, then check
# everything that landed. docs/CLEAN_MACHINE.md explains it, what it can't prove, and the manual After Effects half.
#
#   scripts/fresh_home_test.sh [--keep] [--online] [--report FILE]
#
# How it works:
#   - snapshots this checkout's working tree (no .venv, node_modules or .git) into a throwaway git repo, which stands
#     in for GitHub;
#   - makes a fresh HOME and a minimal PATH (the system folders plus uv, nothing from Homebrew), seeds that home with
#     one unrelated skill and one stale hand-copied Slate skill (the drift the inventory found on this Mac);
#   - pipes install.sh into bash with SLATE_REPO pointing at the snapshot, exactly like the one-line installer;
#   - checks the clone, the environment, the shims, the skill links, that ~/.codex/skills is untouched, the MCP
#     registration commands (shown only: the agents' real CLIs would change this Mac's settings), the launchd job
#     runner (shown only), doctor's expected reds (no keys in a new home, After Effects not proven), a second run that
#     changes nothing, uninstall putting everything back, and that nothing outside the temp folder changed.
#
# Offline by default: uv reads this Mac's own package cache (UV_OFFLINE=1), so the test needs no network and proves
# nothing about downloads. --online uses an empty cache and downloads like a new Mac would.
#
# Each check prints PASS, FAIL or UNCHECKED. Exit 0 when everything passed, 1 when anything failed, 3 when nothing
# failed but something couldn't be checked (UNCHECKED is never a pass).
set -euo pipefail
export BROWSER=none

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
KEEP=0
ONLINE=0
REPORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=1 ;;
    --online) ONLINE=1 ;;
    --report) REPORT="$2"; shift ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'fresh_home_test: unknown option %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/slate-fresh-home.XXXXXX")"
ROOT="$(cd "$ROOT" && pwd -P)"
FRESH="$ROOT/home"
SNAP="$ROOT/source"
MINBIN="$ROOT/bin"
LOGS="$ROOT/logs"
CHECKS="$ROOT/checks"
mkdir -p "$FRESH" "$SNAP" "$MINBIN" "$LOGS" "$CHECKS"
cleanup() { if [ "$KEEP" = 1 ]; then printf 'kept: %s\n' "$ROOT"; else rm -rf "$ROOT"; fi; }
trap cleanup EXIT

RESULTS="$ROOT/results.tsv"
: > "$RESULTS"
record() {  # record STATUS name evidence. A status that isn't one of the three is a checker that broke: a FAIL.
  local status="$1" evidence="$3"
  case "$status" in PASS|FAIL|UNCHECKED) ;; *) evidence="the check itself broke (got '$status'): $evidence"; status=FAIL ;; esac
  printf '%s\t%s\t%s\n' "$status" "$2" "$evidence" >> "$RESULTS"
  printf '%-9s %s: %s\n' "$status" "$2" "$evidence"
}
record_line() {  # record_line name "STATUS<tab>evidence" (what the Python checkers print)
  local tab line="$2"
  tab="$(printf '\t')"
  record "${line%%"$tab"*}" "$1" "${line#*"$tab"}"
}
PY_HOST="$SRC/.venv/bin/python"
[ -x "$PY_HOST" ] || PY_HOST="$(command -v python3)"

# --- The Python checkers (written to files first: a heredoc inside $( ) trips macOS's bash 3.2 on an apostrophe) ---

cat > "$CHECKS/mcp.py" <<'PYEOF'
"""The MCP commands a sandbox install shows, and the job runner it shows (never runs)."""
import json, sys
text = open(sys.argv[1]).read()
shim = sys.argv[2]
def first_json(text):
    """The first complete JSON value in text (native frameworks may print around it)."""
    dec, i = json.JSONDecoder(), 0
    while True:
        starts = [q for q in (text.find("{", i), text.find("[", i)) if q >= 0]
        if not starts:
            return None
        try:
            return dec.raw_decode(text, min(starts))[0]
        except ValueError:
            i = min(starts) + 1
res = first_json(text)
if not isinstance(res, dict):
    print("FAIL\tinstall --dry-run --json returned no JSON")
    print("FAIL\tinstall --dry-run --json returned no JSON")
    sys.exit()
want = {
    "claude": ["claude", "mcp", "add", "--scope", "user", "slate", "--", shim, "mcp"],
    "codex": ["codex", "mcp", "add", "slate", "--", shim, "mcp"],
    "openclaw": ["openclaw", "mcp", "add", "slate", "--command", shim, "--arg", "mcp", "--connect-timeout", "30"],
}
got = {e["client"]: e for e in res.get("mcp", [])}
bad = [c for c, argv in want.items() if got.get(c, {}).get("add") != argv]
ran = [c for c, e in got.items() if e.get("did") not in ("shown", "skipped", "already registered")]
if bad or ran:
    print("FAIL\twrong commands for %s; ran for %s" % (bad, ran))
else:
    print("PASS\t" + " | ".join(" ".join(want[c][:6]) + " ..." for c in want) + " (shown, not run)")
ja = res.get("jobs_agent") or {}
print(("PASS" if ja.get("did") == "shown" else "FAIL") + "\tjob runner " + str(ja.get("did")) + ": "
      + " && ".join(ja.get("commands", [])))
PYEOF

cat > "$CHECKS/doctor.py" <<'PYEOF'
"""Doctor in a new home: no key is green, ElevenLabs' is red, After Effects isn't proven. Other reds are listed."""
import json, sys
text = open(sys.argv[1]).read()
def first_json(text):
    """The first complete JSON value in text (native frameworks may print around it)."""
    dec, i = json.JSONDecoder(), 0
    while True:
        starts = [q for q in (text.find("{", i), text.find("[", i)) if q >= 0]
        if not starts:
            return None
        try:
            return dec.raw_decode(text, min(starts))[0]
        except ValueError:
            i = min(starts) + 1
res = first_json(text)
if res is None:
    print("FAIL\tdoctor --json returned no JSON")
    sys.exit()
checks = res if isinstance(res, list) else next(
    (res[k] for k in ("checks", "results", "items") if isinstance(res.get(k), list)), [])
# Doctor's rows are {id, status, summary, fix}: keys are key.*, After Effects is ae.* (slate.doctor's own ids).
keys = [c for c in checks if str(c.get("id", "")).startswith("key.")]
ae = [c for c in checks if str(c.get("id", "")).startswith("ae.")]
problems = []
if not keys:
    problems.append("no key rows (key.*) in the report")
problems += ["%s is green in a home with no keys" % c.get("id") for c in keys if c.get("status") == "green"]
if not any(c.get("id") == "key.elevenlabs" and c.get("status") == "red" for c in keys):
    problems.append("key.elevenlabs is not red, yet every voice line needs it")
if not ae:
    problems.append("no After Effects rows (ae.*) in the report")
elif all(c.get("status") == "green" for c in ae):
    problems.append("every After Effects row is green in a home that has never opened AE")
others = [str(c.get("id")) for c in checks if c.get("status") == "red" and c not in keys and c not in ae]
pairs = lambda rows: ", ".join("%s=%s" % (c.get("id"), c.get("status")) for c in rows)
if problems:
    print("FAIL\t" + "; ".join(problems))
else:
    print("PASS\t%d rows; keys not green (%s); After Effects not all green (%s); other reds, expected in a new home: %s"
          % (len(checks), pairs(keys), pairs(ae), ", ".join(others) or "none"))
PYEOF

cat > "$CHECKS/second.py" <<'PYEOF'
"""A third run, after install.sh ran twice: nothing to change, nothing moved aside."""
import json, sys
text = open(sys.argv[1]).read()
def first_json(text):
    """The first complete JSON value in text (native frameworks may print around it)."""
    dec, i = json.JSONDecoder(), 0
    while True:
        starts = [q for q in (text.find("{", i), text.find("[", i)) if q >= 0]
        if not starts:
            return None
        try:
            return dec.raw_decode(text, min(starts))[0]
        except ValueError:
            i = min(starts) + 1
res = first_json(text)
if not isinstance(res, dict):
    print("FAIL\tno JSON from slate install")
    sys.exit()
links = res["links"]
ok = links["changed"] == 0 and not links["backups"] and not links["errors"]
print(("PASS" if ok else "FAIL") + "\t%d changed, %d already right, %d moved aside"
      % (links["changed"], links["kept"], len(links["backups"])))
PYEOF

cat > "$CHECKS/uninstall.py" <<'PYEOF'
"""How many links and shims uninstall removed, and whether it put the seeded old copy back."""
import json, sys
text = open(sys.argv[1]).read()
def first_json(text):
    """The first complete JSON value in text (native frameworks may print around it)."""
    dec, i = json.JSONDecoder(), 0
    while True:
        starts = [q for q in (text.find("{", i), text.find("[", i)) if q >= 0]
        if not starts:
            return None
        try:
            return dec.raw_decode(text, min(starts))[0]
        except ValueError:
            i = min(starts) + 1
res = first_json(text)
if not isinstance(res, dict):
    print("0 0")
    sys.exit()
removed = sum(1 for r in res.get("removed", []) if r.get("did") == "removed")
restored = sum(1 for r in res.get("restored", []) if r.get("did") == "put back" and r.get("original") == sys.argv[2])
print(removed, restored)
PYEOF

cat > "$CHECKS/report.py" <<'PYEOF'
import json, sys
rows = [line.rstrip("\n").split("\t", 2) for line in open(sys.argv[1]) if line.strip()]
json.dump({"checks": [{"status": s, "name": n, "evidence": e} for s, n, e in rows]}, open(sys.argv[2], "w"), indent=1)
PYEOF

# --- What the real machine looks like before (so we can prove nothing outside the sandbox changed) ----------------

fingerprint() {  # fingerprint PATH: how a path looks now (missing, a link and its target, or a file's hash)
  if [ -L "$1" ]; then printf 'link:%s' "$(readlink "$1")"
  elif [ -f "$1" ]; then printf 'file:%s' "$(shasum -a 256 "$1" | cut -c1-16)"
  elif [ -d "$1" ]; then printf 'dir'
  else printf 'missing'; fi
}
REAL_HOME="$(eval "cd ~$(id -un)" && pwd -P)"
WATCH="$REAL_HOME/.local/bin/slate /opt/homebrew/bin/slate $REAL_HOME/Library/LaunchAgents/dev.slate.jobs.plist"
BEFORE=""
for p in $WATCH; do BEFORE="$BEFORE $p=$(fingerprint "$p")"; done
agent_loaded() { /bin/launchctl print "gui/$(id -u)/dev.slate.jobs" >/dev/null 2>&1 && echo loaded || echo "not loaded"; }
AGENT_BEFORE="$(agent_loaded)"

# --- The stand-in for GitHub ---------------------------------------------------------------------------------------

rsync -a --exclude .venv --exclude node_modules --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude '*.pyc' --exclude .DS_Store "$SRC/" "$SNAP/"
PROBE=""
if ! ls "$SNAP"/skills/*/SKILL.md >/dev/null 2>&1; then
  # A checkout without skills can't show links working, so the snapshot gets one neutral probe skill.
  PROBE="fresh-home-probe"
  mkdir -p "$SNAP/skills/$PROBE"
  printf -- '---\nname: %s\ndescription: A probe the fresh-home test links to prove the mechanism.\n---\n\nProbe.\n' \
    "$PROBE" > "$SNAP/skills/$PROBE/SKILL.md"
fi
git -C "$SNAP" init --quiet
git -C "$SNAP" add -A
git -C "$SNAP" -c user.name="fresh-home test" -c user.email="fresh-home@localhost" commit --quiet -m "snapshot"
SNAP_HEAD="$(git -C "$SNAP" rev-parse HEAD)"
SKILLS="$(cd "$SNAP/skills" && for d in */; do if [ -f "$d/SKILL.md" ]; then printf '%s ' "${d%/}"; fi; done)"
FIRST_SKILL="${SKILLS%% *}"

# --- A new Mac's home and PATH -----------------------------------------------------------------------------------

UV="$(command -v uv || true)"
[ -n "$UV" ] || { [ -x /opt/homebrew/bin/uv ] && UV=/opt/homebrew/bin/uv; }
if [ -z "$UV" ]; then
  record UNCHECKED install "uv isn't on this Mac, so the installer can't make an environment here"
  exit 3
fi
ln -s "$UV" "$MINBIN/uv"
MINPATH="$MINBIN:/usr/bin:/bin:/usr/sbin:/sbin"
UVPY="$(HOME="$REAL_HOME" "$UV" python find '>=3.11' 2>/dev/null || true)"
if [ "$ONLINE" = 1 ]; then
  UVCACHE="$ROOT/uv-cache"; UVOFF=""
else
  # This account's own cache, whatever HOME says (a test runner may point HOME at a temp folder).
  UVCACHE="${UV_CACHE_DIR:-$(HOME="$REAL_HOME" "$UV" cache dir 2>/dev/null)}"; UVOFF=1
fi

# Seeds: one skill that isn't Slate's (it must come through untouched), and a stale hand-made copy of a Slate skill
# in ~/.agents/skills (the installer must move it aside, never delete it, and uninstall must put it back).
mkdir -p "$FRESH/.claude/skills/someone-elses-skill" "$FRESH/.agents/skills/$FIRST_SKILL"
printf -- '---\nname: someone-elses-skill\ndescription: not part of Slate\n---\n' \
  > "$FRESH/.claude/skills/someone-elses-skill/SKILL.md"
printf -- '---\nname: %s\ndescription: an old copy\n---\nold copy\n' "$FIRST_SKILL" \
  > "$FRESH/.agents/skills/$FIRST_SKILL/SKILL.md"
OTHER_SUM="$(shasum -a 256 "$FRESH/.claude/skills/someone-elses-skill/SKILL.md" | cut -c1-16)"

in_fresh() {  # run a command as the new Mac's user: fresh HOME, minimal PATH, nothing else from this session
  env -i HOME="$FRESH" PATH="$MINPATH" USER="${USER:-$(id -un)}" LOGNAME="${LOGNAME:-$(id -un)}" SHELL=/bin/bash \
    TMPDIR="${TMPDIR:-/tmp}" LANG=en_US.UTF-8 BROWSER=none SLATE_NONINTERACTIVE=1 SLATE_FAKE_VENDORS=1 \
    SLATE_REPO="file://$SNAP" UV_CACHE_DIR="$UVCACHE" ${UVOFF:+UV_OFFLINE=1} ${UVPY:+UV_PYTHON="$UVPY"} \
    ${UVPY:+SLATE_PYTHON="$UVPY"} UV_PYTHON_DOWNLOADS=never GIT_TERMINAL_PROMPT=0 "$@"
}

# --- 1. The one-line install, piped into bash --------------------------------------------------------------------

set +e
in_fresh /bin/bash -s -- --agents claude,codex,openclaw < "$SRC/install.sh" > "$LOGS/install1.log" 2>&1
RC1=$?
set -e
CO="$FRESH/code/slate"
SHIM="$FRESH/.local/bin/slate"
if [ "$RC1" = 0 ]; then record PASS "installer (curl | bash)" "exit 0; log $LOGS/install1.log"
else record FAIL "installer (curl | bash)" "exit $RC1: $(tail -5 "$LOGS/install1.log" | tr '\n' ' ')"; fi

if [ -d "$CO/.git" ] && [ "$(git -C "$CO" rev-parse HEAD 2>/dev/null)" = "$SNAP_HEAD" ]; then
  record PASS clone "$CO at ${SNAP_HEAD:0:10}"
else record FAIL clone "no clone of the snapshot at $CO"; fi

if in_fresh "$CO/.venv/bin/python" -c 'import sys, slate, numpy, scipy, PIL, jsonschema, mcp; assert sys.version_info >= (3, 11)' 2>"$LOGS/env.err"; then
  record PASS environment "$CO/.venv: $("$CO/.venv/bin/python" -c 'import sys; print("Python %d.%d" % sys.version_info[:2])') with slate, numpy, scipy, Pillow, jsonschema, mcp"
else record FAIL environment "$(tr '\n' ' ' < "$LOGS/env.err" | cut -c1-300)"; fi

if in_fresh "$CO/.venv/bin/python" -c 'import ocrmac' 2>/dev/null; then record PASS "ocr extra" "ocrmac imports (OCR gates can run)"
elif [ -n "$UVOFF" ] && grep -q 'network was disabled' "$LOGS/install1.log" \
    && grep -q "the ocr extra (ocrmac, Apple Vision) didn't install" "$LOGS/install1.log"; then
  record UNCHECKED "ocr extra" "offline, this Mac's uv cache can't resolve ocrmac's pyobjc frameworks; the installer said so and installed the base package (run with --online to check the download)"
else record FAIL "ocr extra" "ocrmac isn't in the environment, so OCR gates would stay UNCHECKED"; fi

check_shim() {  # check_shim LABEL PATH
  local label="$1" path="$2" ver
  if [ -f "$path" ] && [ -x "$path" ] && grep -q 'slate-shim v1' "$path" && grep -q "$CO" "$path"; then
    ver="$(in_fresh "$path" --version 2>&1 || true)"
    case "$ver" in slate\ *) record PASS "$label" "$path runs this checkout: $ver" ;;
      *) record FAIL "$label" "$path is there but \`slate --version\` said: $ver" ;; esac
  else record FAIL "$label" "no Slate shim at $path"; fi
}
check_shim "shim ~/.local/bin" "$SHIM"
check_shim "shim /opt/homebrew/bin (sandboxed)" "$FRESH/opt/homebrew/bin/slate"

LINK_PROBLEMS=""
for s in $SKILLS; do
  for root in .claude/skills .agents/skills; do
    t="$FRESH/$root/$s"
    [ -L "$t" ] && [ "$(readlink "$t")" = "$CO/skills/$s" ] || LINK_PROBLEMS="$LINK_PROBLEMS ~/$root/$s"
  done
done
if [ -z "$LINK_PROBLEMS" ]; then record PASS "skill links" "$(printf '%s' "$SKILLS" | wc -w | tr -d ' ') skills linked into ~/.claude/skills and ~/.agents/skills${PROBE:+ (probe skill: the checkout has none yet)}"
else record FAIL "skill links" "wrong or missing:$LINK_PROBLEMS"; fi

if [ ! -e "$FRESH/.codex/skills" ]; then record PASS "never ~/.codex/skills" "nothing was created there"
else record FAIL "never ~/.codex/skills" "$(ls -A "$FRESH/.codex/skills" | tr '\n' ' ')"; fi

BACKED="$(find "$FRESH/Library/Application Support/Slate/backups" -path "*agents-skills/$FIRST_SKILL/SKILL.md" 2>/dev/null | head -1 || true)"
if [ -n "$BACKED" ] && grep -q 'old copy' "$BACKED"; then record PASS "stale copy moved aside" "$BACKED"
else record FAIL "stale copy moved aside" "the old ~/.agents/skills/$FIRST_SKILL wasn't found in the backups"; fi

if [ "$(shasum -a 256 "$FRESH/.claude/skills/someone-elses-skill/SKILL.md" | cut -c1-16)" = "$OTHER_SUM" ] \
    && [ ! -L "$FRESH/.claude/skills/someone-elses-skill" ]; then
  record PASS "other skills untouched" "someone-elses-skill is exactly as it was"
else record FAIL "other skills untouched" "someone-elses-skill changed"; fi

# --- 2. MCP and the job runner: shown, never run, from a sandbox home -----------------------------------------------

set +e
in_fresh "$SHIM" install --dry-run --json --agents claude,codex,openclaw --no-doctor > "$LOGS/dryrun.json" 2>"$LOGS/dryrun.err"
MCP_CHECK="$("$PY_HOST" "$CHECKS/mcp.py" "$LOGS/dryrun.json" "$SHIM")"
set -e
record_line "MCP registration commands" "$(printf '%s\n' "$MCP_CHECK" | sed -n 1p)"
if [ ! -e "$FRESH/.claude.json" ] && [ ! -e "$FRESH/.codex/config.toml" ] && [ ! -e "$FRESH/.openclaw" ]; then
  record PASS "no agent settings written" "no ~/.claude.json, ~/.codex/config.toml or ~/.openclaw in the new home"
else record FAIL "no agent settings written" "an agent's settings appeared in the new home"; fi
JOB_LINE="$(printf '%s\n' "$MCP_CHECK" | sed -n 2p)"
if [ -e "$FRESH/Library/LaunchAgents/dev.slate.jobs.plist" ]; then
  record FAIL "job runner (shown)" "a plist was written into the new home's LaunchAgents"
else record_line "job runner (shown)" "$JOB_LINE"; fi

# --- 3. Doctor: the reds a new home should have ---------------------------------------------------------------------

if ! in_fresh "$SHIM" doctor --help >/dev/null 2>&1; then
  record UNCHECKED "doctor's expected reds" "\`slate doctor\` isn't a command in this checkout yet, so its reds (keys, After Effects) weren't checked"
else
  set +e
  in_fresh "$SHIM" doctor --json > "$LOGS/doctor.json" 2>"$LOGS/doctor.err"
  DOC="$("$PY_HOST" "$CHECKS/doctor.py" "$LOGS/doctor.json")"
  set -e
  record_line "doctor's expected reds" "$DOC"
fi

# --- 4. Run it again: nothing changes -------------------------------------------------------------------------------

set +e
in_fresh "$CO/install.sh" --agents claude,codex,openclaw > "$LOGS/install2.log" 2>&1
RC2=$?
in_fresh "$SHIM" install --json --agents claude,codex,openclaw --no-doctor > "$LOGS/install3.json" 2>"$LOGS/install3.err"
SECOND="$("$PY_HOST" "$CHECKS/second.py" "$LOGS/install3.json")"
set -e
if [ "$RC2" = 0 ] && grep -q '(kept)' "$LOGS/install2.log"; then
  record_line "second run changes nothing" "$SECOND"
else record FAIL "second run changes nothing" "install.sh from the checkout exited $RC2: $(tail -3 "$LOGS/install2.log" | tr '\n' ' ')"; fi

# --- 5. Uninstall puts everything back ------------------------------------------------------------------------------

slate_bits() {  # the Slate links and shims in the new home right now
  local s root t out=""
  for s in $SKILLS; do for root in .claude/skills .agents/skills; do
    t="$FRESH/$root/$s"
    if [ -L "$t" ] && [ "$(readlink "$t")" = "$CO/skills/$s" ]; then out="$out ~/$root/$s"; fi
  done; done
  if [ -e "$SHIM" ]; then out="$out ~/.local/bin/slate"; fi
  if [ -e "$FRESH/opt/homebrew/bin/slate" ]; then out="$out (sandboxed)/opt/homebrew/bin/slate"; fi
  printf '%s' "$out"
}
HAD="$(slate_bits)"
set +e
in_fresh "$CO/uninstall.sh" --json > "$LOGS/uninstall.json" 2>"$LOGS/uninstall.err"
RCU=$?
UNDONE="$("$PY_HOST" "$CHECKS/uninstall.py" "$LOGS/uninstall.json" "$FRESH/.agents/skills/$FIRST_SKILL")"
set -e
REMOVED="${UNDONE%% *}"
RESTORED="${UNDONE##* }"
LEFT="$(slate_bits)"
if [ -z "$HAD" ]; then
  record FAIL uninstall "nothing was installed, so there was nothing to take away (see the install checks above)"
elif [ "$RCU" = 0 ] && [ -z "$LEFT" ] && [ "${REMOVED:-0}" -gt 0 ] && [ "${RESTORED:-0}" = 1 ] \
    && grep -q 'old copy' "$FRESH/.agents/skills/$FIRST_SKILL/SKILL.md" 2>/dev/null \
    && [ -f "$FRESH/.claude/skills/someone-elses-skill/SKILL.md" ]; then
  record PASS uninstall "removed $REMOVED links and shims, put the old copy of $FIRST_SKILL back, kept someone-elses-skill"
else record FAIL uninstall "exit $RCU; removed ${REMOVED:-0}, restored ${RESTORED:-0}; still there:${LEFT:- nothing}; see $LOGS"; fi

# --- 6. Nothing outside the temp folder changed -------------------------------------------------------------------

AFTER=""
for p in $WATCH; do AFTER="$AFTER $p=$(fingerprint "$p")"; done
if [ "$BEFORE" = "$AFTER" ] && [ "$AGENT_BEFORE" = "$(agent_loaded)" ]; then
  record PASS "real Mac untouched" "~/.local/bin/slate, /opt/homebrew/bin/slate, the job runner's plist and launchd state are as they were"
else record FAIL "real Mac untouched" "before:$BEFORE [$AGENT_BEFORE] after:$AFTER [$(agent_loaded)]"; fi

# --- Summary ----------------------------------------------------------------------------------------------------

PASSES="$(grep -c '^PASS' "$RESULTS" || true)"
FAILS="$(grep -c '^FAIL' "$RESULTS" || true)"
UNCHECKED="$(grep -c '^UNCHECKED' "$RESULTS" || true)"
printf '\nfresh-home test: %s passed, %s failed, %s unchecked (logs in %s%s)\n' "$PASSES" "$FAILS" "$UNCHECKED" \
  "$LOGS" "$([ "$KEEP" = 1 ] || printf ', removed on exit: use --keep to look')"
if [ -n "$REPORT" ]; then "$PY_HOST" "$CHECKS/report.py" "$RESULTS" "$REPORT"; fi
if [ "$FAILS" != 0 ]; then exit 1; fi
if [ "$UNCHECKED" != 0 ]; then exit 3; fi
exit 0
