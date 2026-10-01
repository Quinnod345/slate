# Working on Slate

This file is for any agent, or person, changing this repository. Slate is a film studio any agent can run: a brief
becomes a finished, gated film, and every stage is a `slate` command. Read the contract before you change anything.

## Read first

1. `docs/ARCHITECTURE.md`: the contract. Hard rules (§1), who owns which paths (§2), the shared foundation you import
   and never copy (§3), the command-line surface (§4), the film folder (§5), gate results (§6), brand packs (§7), the
   Node engine boundary (§8), jobs (§9), testing (§10), style (§11) and the cross-module interfaces (§12).
2. `docs/PLAN.md`: the plan Slate is built to; `docs/PLAN_TRACKER.md` tracks every point in it.
3. `docs/DECISIONS.md`: the decisions behind the plan. Don't reverse one quietly; a change gets a new record.
4. The module you're changing, and its report in `docs/build_reports/` if it has one.

## The rules

These hold for every change and every test. Most are enforced in code; the rest are on you.

- **No brand in the core.** Nothing specific to any company's brand in this repository: no names, colours, copy,
  voice ids, logos, fonts, sample data or banned-word lists. Brands are data packs (`brands/<name>/brand.json`), and
  tests use the neutral demo pack or fixtures. Company packs live outside the repository, in `~/code/slate-brands/`.
- **No literal frame sizes or rates in logic.** Width, height and frame rate come from `film.json` or the format.
- **Secrets only through `slate.secrets`**: the environment, then the macOS Keychain. Never print, log, write or return
  a key, never put one in a URL, and never ask for one outside `slate connect` in a person's own Terminal.
- **No browsers.** Every child process goes through `slate.procs.run` (it sets `BROWSER=none`, uses no shell and closes
  stdin). Nothing opens a web browser; web steps go through Aside. The open web renderer's headless browser only ever
  loads Slate's own local pages.
- **After Effects waits for a person.** AE steps start only on a person's go, and autonomous films never touch AE. AE's
  preferences change only through `slate.ae.prefs` (byte-safe, one key, AE closed, never the scripting preference).
  Never quit AE for someone; ask them to save and quit.
- **Spend is explicit.** Every paid call goes through `slate.ledger` (estimate, budget check, in-flight row, result).
  Tests, evals and demos run in replay mode (`SLATE_FAKE_VENDORS=1`) and spend nothing. Slate never buys credits,
  upgrades a plan or starts a licence.
- **Gates, not vibes.** Every check returns a `GateResult`. Only PASS counts as passed; UNCHECKED is never reported as
  a pass, and no gate quietly falls back to another engine.
- **Long work is a job.** Renders, masters, alignment, generation batches and anything else that outlasts a session go
  through the job queue (`slate.jobs.client.submit`, or a command's `--detach`).
- **Perfect, don't replace.** Code that revises approved work edits that artifact; it never swaps the approach.
- **Voices.** Never whisper, soft, quiet or gentle audio tags. Clones only of the owner's own verified voice.
- **Portable text.** Skills, `--help` and error messages never name one agent's own tools; say it in words any agent
  understands.
- **No stubs presented as done.** If something can't be built yet, build everything up to that point, make the command
  refuse with a clear next step, and test the refusal.
- **Never render on the work Mac.** Rendering is local to this Mac.
- **Quinn's voice** in docs, comments, help text and messages: clear, specific, warm, no filler.

## Writing a command

- A module exposes `register(sub)` and adds commands with `slate.cli.add_command` (a leaf) or `slate.cli.add_group` (a
  command with stages). `slate/cli.py` lists the modules.
- A handler takes the parsed arguments and returns an exit code. It prints people's text with `slate.log.say` and
  machine output with `slate.log.result` (or `emit`) under `--json`: exactly one JSON object on stdout, everything
  else on stderr.
- Refusals raise `slate.errors` types so the exit code is right: `Refused` 1, `UsageError` 2, `MissingPrerequisite`
  3, `SpendRefused` 4, `Busy` 5. Give each a `hint`: the one next step.
- Honour the global flags: `--json`, `--film`, `--live`, `--dry-run` (say what would happen, change nothing).
- Read dynamic paths at call time (`paths.data()`, `paths.films()`), never with `from slate.paths import DATA`: they
  follow `SLATE_DATA`, `SLATE_FILMS` and `SLATE_BRANDS`.
- A new MCP tool wraps a command that already exists; the command line is the contract. The allowlist lives in
  `src/slate/mcp/policy.py`.

## Running things

Python is 3.11 or later, in `.venv`. Node packages are already in `engine/node_modules`; don't install anything
globally, and don't run `npm install`.

```sh
# every Python test, offline and in replay mode (tests/conftest.py points Slate at temporary folders);
# test file names must be unique across tests/, so prefix yours with your module's name
.venv/bin/python -m pytest tests -q -p no:cacheprovider

# one module's tests
.venv/bin/python -m pytest tests/<module> -q -p no:cacheprovider

# the slow ones too (a whole install into a fresh home, a minute or more); the default run skips them
SLATE_TEST_SLOW=1 .venv/bin/python -m pytest tests/install -q -p no:cacheprovider

# the engine's tests
cd engine && npx --no-install vitest run

# the whole pipeline on one neutral 15-second reel, every stage for real, vendors replayed (a few minutes), and the
# same run as a test
.venv/bin/python -m slate demo
SLATE_TEST_INTEGRATION=1 .venv/bin/python -m pytest tests/integration -q -p no:cacheprovider

# generated copies and manifests still match their sources
.venv/bin/python -m slate skills build --check
.venv/bin/python -m slate mcpb build --check

# every lesson mapped to where it lives (the knowledge ledger)
.venv/bin/python -m slate knowledge check
```

Tests stay fast and offline: anything that runs a real model, a long render, a real install or the network goes
behind an opt-in switch, with recorded fixtures by default.

To run `slate` by hand, point it at scratch folders first, so nothing touches the real runtime folder, films or brand
packs:

```sh
export SLATE_DATA=$(mktemp -d) SLATE_FILMS=$(mktemp -d) SLATE_BRANDS=$(mktemp -d) SLATE_FAKE_VENDORS=1
.venv/bin/python -m slate status
```

Keep the machine usable: one heavy process at a time, short renders while testing, nothing left running when you're
done.

## Skills

The ten skills live in `skills/<name>/`: `SKILL.md` plus `references/`, in the open Agent Skills format.

- Frontmatter is spec-only (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`); the name
  matches the folder; the description stays under 1,024 characters; SKILL.md stays under 500 lines, with depth in
  `references/`, linked by relative paths.
- After editing a skill, run `slate skills build`. It regenerates the plugin's copies (`plugins/slate/skills/`, with
  Codex sidecars), OpenClaw's entries and the knowledge-only zips for Claude chats. Never edit those by hand; CI runs
  `slate skills build --check` and fails when they drift.
- `slate guide <topic>` prints a skill's reference files, so an agent without skills reads the same text.

## Git

Change only what your task covers. Don't commit, push or rewrite history unless you're asked to; when you are, branch
first, and never commit a key, a film's media or company material.
