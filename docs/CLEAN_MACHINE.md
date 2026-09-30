# The clean-machine test

Phase 5 ("Everywhere") is done when each agent runs `slate demo` and Slate installs cleanly on a Mac that has never
seen it. This page is how we prove the second half. It has three parts: a scripted fresh-home install that anyone can
run in a minute or so, a manual After Effects checklist (AE is paid, and only a person starts it), and a fresh macOS
virtual machine, which is the only test that proves anything about apps, fonts and sound libraries.

## 1. The scripted fresh-home test

```sh
scripts/fresh_home_test.sh [--keep] [--online] [--report FILE]
```

It installs Slate into a brand-new home exactly the way the one-line installer does, then checks everything that
landed. Nothing outside its temp folder changes, and it proves that at the end.

What it does:

1. Snapshots this checkout's working tree (without `.venv`, `node_modules` or `.git`) into a throwaway git repo that
   stands in for GitHub. If the checkout has no skills yet, the snapshot gets one neutral probe skill, so the links
   still have something to prove.
2. Makes a fresh `HOME` and a minimal `PATH`: the system folders plus uv, nothing from Homebrew, no agent CLIs.
3. Seeds that home with one skill that isn't Slate's and a stale hand-made copy of a Slate skill in
   `~/.agents/skills` (the copy drift the inventory found on this Mac).
4. Pipes `install.sh` into bash with `SLATE_REPO` pointing at the snapshot, the same as
   `curl … | bash`, with `--agents claude,codex,openclaw`.
5. Checks, installs again, uninstalls, and checks again.

| Check | What PASS proves |
|---|---|
| installer (curl \| bash) | The piped installer runs start to finish in an agent-like context: no prompts, exit 0 |
| clone | It cloned the repo into `~/code/slate` at the right commit |
| environment | uv built `.venv` with Python 3.11+ and Slate's packages (numpy, scipy, Pillow, jsonschema, mcp) |
| ocr extra | ocrmac (Apple Vision) is in the environment, so OCR gates can run |
| shim ~/.local/bin | `~/.local/bin/slate` is Slate's shim and `slate --version` runs through it |
| shim /opt/homebrew/bin (sandboxed) | The gateway's link was made, inside the sandbox (the real `/opt/homebrew/bin` is never touched) |
| skill links | Every skill is a symlink into the one checkout, in `~/.claude/skills` and `~/.agents/skills` |
| never ~/.codex/skills | Nothing was created there |
| stale copy moved aside | The seeded old copy went to the dated backup folder instead of being deleted |
| other skills untouched | The unrelated skill is byte for byte what it was |
| MCP registration commands | The exact `claude mcp add --scope user …`, `codex mcp add …` and `openclaw mcp add slate --command … --arg mcp --connect-timeout 30` commands, shown and not run: the agents' real CLIs would change this Mac's own settings whatever `HOME` says |
| no agent settings written | No `~/.claude.json`, Codex config or OpenClaw folder appeared |
| job runner (shown) | The launchd commands were shown, not run: `launchctl` acts on the real login session |
| doctor's expected reds | Doctor ran through the shim; no key reads as present (`key.elevenlabs` red, the optional keys yellow), and After Effects isn't all green (its prefs can't be read in a home that never opened AE). Every other red is listed as evidence |
| second run changes nothing | `install.sh` again keeps the environment; `slate install` then reports 0 changed and nothing moved aside |
| uninstall | Every link and shim removed, the seeded old copy put back, the unrelated skill kept |
| real Mac untouched | The real `~/.local/bin/slate`, `/opt/homebrew/bin/slate`, the job runner's plist and its launchd state are exactly as before |

It prints PASS, FAIL or UNCHECKED for each check and exits 0 when everything passed, 1 when anything failed, and 3
when nothing failed but something couldn't be checked. UNCHECKED is never a pass; each one says why. Two can happen
today:

- **ocr extra**, offline: uv reads this Mac's own package cache (`UV_OFFLINE=1`) so the test needs no network, and
  that cache can't resolve ocrmac's pyobjc frameworks. The installer says so and installs the base package, and doctor
  lists the extra for `slate doctor --install`. `--online` downloads into an empty cache, like a new Mac would, and
  turns this into a PASS or a FAIL.
- **doctor's expected reds**, in a checkout where `slate doctor` isn't wired in yet.

`tests/install/test_install_fresh_home.py` runs the script inside the test suite and fails on any FAIL. It builds a
real environment, so it takes a minute or more, and the default test run skips it; ask for it by name:

```sh
SLATE_TEST_SLOW=1 .venv/bin/python -m pytest tests/install/test_install_fresh_home.py -p no:cacheprovider
```

### What it can't prove

A fresh `HOME` on this Mac still shares everything outside the home folder:

- `/Applications`: After Effects, Xcode, the Claude and ChatGPT apps. Doctor shows AE as installed in a fresh home,
  because it is, for every account on this Mac.
- `/Library/Fonts` and the system fonts, so a brand font installed for everyone looks present.
- The GarageBand and Logic sound library in `/Library/Application Support`, which proof 2's tonal roles need.
- Homebrew in `/opt/homebrew`: ffmpeg, Node, uv, whisper, the Higgsfield CLI.
- uv's package cache and its Python builds (the test borrows them to stay offline).
- macOS permissions (Automation, Full Disk Access), which belong to apps rather than homes.

That's why a new user account on this Mac proves nothing about apps, fonts or sounds, and why part 3 exists.

## 2. The manual After Effects checklist

After Effects is paid, it's the person's to start, and Adobe's terms bar operations a person didn't start. So this
half is a checklist for the person, with Slate's commands to confirm each step. Nothing here edits AE's preferences
by hand, and nothing quits AE for you.

1. **Installed.** After Effects from Creative Cloud. `slate doctor --only aftereffects` shows `ae.app` green with the
   version and build.
2. **aerender.** It ships with AE. `ae.aerender` goes green; doctor never runs it.
3. **Preferences exist.** Open After Effects once yourself, then quit it (saving your work), so it writes its
   preferences. `ae.scripting-pref` stops saying "unknown". `slate ae prefs check` reads them byte-safe.
4. **The scripting setting.** In AE, Settings > Scripting & Expressions > "Allow Scripts to Write Files and Access
   Network". Turn it on yourself if you want to build scenes in AE through the bridge; finishing films works without
   it. Slate never changes this setting, and its prefs tool refuses to.
5. **Automation permission.** macOS asks, the first time a supervised AE step runs, whether the app you run Slate
   from (Terminal, Claude, Codex) may control After Effects. Allow it in System Settings > Privacy & Security >
   Automation. Doctor never launches AE to test it, so `ae.automation` stays yellow (not checked yet) until the
   smoke test in step 8 passes, or until doctor runs while AE is already open and can simply ask it.
6. **The AE bridge.** `slate connect aftereffects` installs Higgsfield's bridge (pinned) and registers it;
   `slate ae bridge status` shows it; `ae.bridge` goes green.
7. **Fonts.** Each brand's fonts, installed by reference from its licence source (never copied into a repo).
   `slate doctor --brand <name>` checks them, and a font substitution fails the font gate.
8. **The smoke test.** With AE free, `slate ae smoke --go` builds a one-second comp, renders it with aerender and
   measures it in pixels. `ae.smoke` goes green, and the AE lane is proven on this Mac.
9. **Colour.** The built project's working space is Rec.709 Gamma 2.4 (sRGB darkened the teal on the films). Check
   it in the project `slate ae build --go` makes.
10. **Masters.** `slate ae masters --go` turns AE's disk cache off for the render and restores it afterwards, with AE
    closed, through `slate ae prefs` only. If AE is open it stops with exit 5 and asks you to save and quit.

## 3. Why a fresh macOS VM is the ideal

A virtual machine with a clean macOS install is the only place Slate meets a Mac with nothing on it: no apps, no
Homebrew, no fonts, no sound library, no caches and no granted permissions. On Apple silicon, the Virtualization
framework runs one well (UTM or tart are the usual front ends).

What the VM proves that nothing else here can:

- every download works: uv and its Python, the packages including the ocr extra, Homebrew and its tools, the engine's
  `npm ci`, Remotion's headless Chrome, the Whisper model;
- doctor's reds on a truly new Mac are exactly the ones it should have, with a fix for each;
- the first-run macOS prompts (Automation, file access) come up where the docs say they will;
- Gatekeeper and quarantine don't block a downloaded binary Slate depends on;
- the one-line installer works for a private repo once `gh auth setup-git` has run.

The procedure:

1. Create a macOS VM (the same major version as this Mac), with no Apple ID and no migrated data.
2. Install Apple's command-line tools (`xcode-select --install`) for git.
3. Sign in to GitHub if the repo is private (`gh auth login`, `gh auth setup-git`), then run the one-line installer.
4. Run `slate doctor --json` and keep the report: every red should be something a new Mac really lacks, each with its
   fix.
5. Run `slate doctor --install` and answer its questions; then doctor again.
6. Run `scripts/fresh_home_test.sh --online` inside the VM too.
7. Record what After Effects needs there (it's paid, and a VM's GPU is limited), and work through part 2 where AE can
   run.

Limits worth knowing: AE in a VM needs its own licence seat and runs on a paravirtualised GPU, so renders there prove
the toolchain, not the speed; and Apple's sound content is a multi-gigabyte download from GarageBand's Sound Library.
