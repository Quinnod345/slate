# Installing Slate: a guide for agents

For any agent with a shell (Claude Code, Codex, OpenClaw, or anything else) asked to install, update, check or remove
Slate on a Mac. Everything here runs from a shell. None of it needs a browser, and none of it spends money.

## The rules

These hold for every step on this page, whoever asked.

1. **Never type, paste, print or store an API key.** Keys go in only through `slate connect elevenlabs` and
   `slate connect higgsfield --api-key`, which the person runs in their own Terminal: macOS asks them for the key
   there, and it goes straight into their login Keychain. If a step needs a key, stop and tell the person which of
   those two commands to run. Never ask for a key in chat.
2. **Never open a browser.** No `open <url>`, no default browser, no web sign-in. Every Slate child process runs with
   `BROWSER=none`. Higgsfield's sign-in is printed by `slate connect higgsfield` for the person to run; it's never run
   for them. Any web step goes through Aside.
3. **Never touch After Effects' preferences, and never start After Effects or aerender yourself.** Slate changes an
   AE preference only through `slate ae prefs`, with AE closed, when the person says go. The "Allow Scripts to Write
   Files and Access Network" setting is the person's alone: Slate never changes it. Never quit AE for them.
4. **Never put skills in `~/.codex/skills`.** Both Codex binaries read it as well as `~/.agents/skills`, so a skill in
   both folders is listed twice.
5. **Nothing prompts.** When a step needs a person's yes (loading the job runner as a login item, setting up the
   owner's personal skills, installing Homebrew tools), the installer prints it as a next step instead of asking.
   Ask the person, then run the step again with the flag they agreed to. Never pass `--yes` on your own.
6. **Leave every other skill as it is.** The installer only creates, replaces or removes its own links and shims.
   Anything else in its way is moved to a dated backup folder under Slate's data folder and reported, never deleted.
7. **Don't install into this Mac's real home to test something.** Use `--home <temp folder>` (a sandbox: it writes
   only inside that folder, and shows instead of running anything that would reach the real session), or
   `--dry-run`.

## Is Slate already here?

```sh
slate --version                     # the CLI is on PATH
slate install --check --json        # every agent wired? (skills, shims, MCP); exit 0 when all good
slate doctor --json                 # everything else: tools, keys (presence only), After Effects, engine
```

If `slate` isn't found but `~/code/slate` exists, the checkout is there and only the link is missing:
`~/code/slate/install.sh` puts it back.

## Install

One command, safe to run again any time:

```sh
curl -fsSL https://raw.githubusercontent.com/Quinnod345/slate/main/install.sh | bash
```

or, from a checkout that's already there:

```sh
~/code/slate/install.sh [--dry-run] [--agents claude,codex,openclaw] [--yes] [--exclude-agent ID]
```

While the repo is private, `raw.githubusercontent.com` answers the `curl` above with a 404, so use the GitHub CLI
instead (the person signs in once with `gh auth login`, then `gh auth setup-git`):

```sh
gh repo clone Quinnod345/slate ~/code/slate && ~/code/slate/install.sh
```

Any other git URL works too: `SLATE_REPO=<url>` tells the piped installer where to clone from.

What it does, in order:

1. Clones Slate into `~/code/slate` (or fast-forwards it, never merges), or uses the checkout it's run from.
2. Makes the checkout's own Python environment with uv (`~/code/slate/.venv`), with the `ocr` extra (Apple Vision).
3. Runs `slate install`, which:
   - symlinks Slate's skills from the one checkout (no copies) into `~/.claude/skills` for Claude Code and into
     `~/.agents/skills` for Codex and OpenClaw;
   - links Higgsfield's skills, where they're installed for Claude Code, into `~/.agents/skills` so Codex and
     OpenClaw get them too;
   - writes a small shim at `~/.local/bin/slate` and `/opt/homebrew/bin/slate` (the OpenClaw gateway's PATH lacks
     `~/.local/bin`) that runs the checkout's environment;
   - registers Slate's MCP server with each agent that's here: `claude mcp add --scope user slate -- <shim> mcp`,
     `codex mcp add slate -- <shim> mcp`, `openclaw mcp add slate --command <shim> --arg mcp --connect-timeout 30`
     (OpenClaw starts the server once to check it before saving, and `slate mcp` can take longer than OpenClaw's
     default 5 seconds to answer on a busy Mac);
   - loads the launchd job runner (`slate jobs agent install`), only with a yes;
   - offers, only with the owner's yes, to set up visionary and goal-fidelity for every personal agent
     (`scripts/setup_quinn_skills.sh`), when those skills are on this machine.
4. Ends with `slate doctor`, which lists what's still missing and how to fix it.

`--dry-run` prints every action and changes nothing. `--agents` limits it to some agents (default: the ones on this
Mac). Exit codes: 0 installed (doctor may still list things to fix), 1 something failed or was refused, 2 usage,
3 a prerequisite is missing (git or uv; the message says how to get it).

An agent that stops to ask before each command stalls an unattended run. The commands Slate's agents run themselves
(slate, ffmpeg and ffprobe, Slate's own Python, the engine's node renders, Whisper) are listed with ready-made rules for
Claude Code's settings and for Codex in the plugin's README, "Permissions for unattended runs"
(`plugins/slate/README.md`, written by `slate skills build`). Offer them to the person; adding a permission is their
call, never the agent's. aerender isn't on the list on purpose: After Effects starts only on a person's go.

### What needs the person

Ask for each of these; don't do them yourself.

| What | Why it needs a person | What to tell them |
|---|---|---|
| Loading the job runner | It adds a login item | "Say yes and I'll run `slate install --yes`," or they run `slate jobs agent install` |
| visionary and goal-fidelity | They're the owner's own skills | `scripts/setup_quinn_skills.sh --yes`, adding `--exclude-agent <id>` for each company OpenClaw agent that must never get them |
| Homebrew tools, the engine, the Whisper model | Downloads onto their Mac | In their own Terminal: `slate doctor --install` (it asks before each one) |
| ElevenLabs key | A secret | In their own Terminal: `slate connect elevenlabs` |
| Higgsfield | A sign-in, and an optional key | In their own Terminal: `slate connect higgsfield` (prints the sign-in), then `--api-key` for the Cloud API |
| After Effects | Paid, and theirs to start | `slate connect aftereffects`; AE's scripting setting is theirs to turn on |
| `~/.local/bin` on PATH | Their shell settings | Add `export PATH="$HOME/.local/bin:$PATH"` to `~/.zprofile` |

## Update

```sh
slate update [--dry-run]
```

Fast-forwards the checkout (it refuses, with the way out, when the checkout has diverged or local changes are in the
way), syncs the environment when the dependencies changed, re-links skills with the updated installer, checks the
Node engine against its Remotion pin, and runs doctor. Every agent reaches Slate through symlinks into the checkout
and one shim, so one update reaches all of them at once and nothing is registered again.

## Remove

```sh
slate uninstall --dry-run      # see the list first
slate uninstall                # or ~/code/slate/uninstall.sh
```

Removes Slate's skill links, the Higgsfield links it made, the shims, its MCP registrations and its job runner (with
a yes, from an agent), and puts back anything it had moved aside when that spot is free. The checkout, its
environment, films, brand packs and Slate's data folder all stay.

## When something goes wrong

| You see | What it means | What to do |
|---|---|---|
| `uv isn't installed` (exit 3) | uv makes Slate's environment | Ask the person to run `brew install uv`, then run `install.sh` again |
| `… exists but isn't a git checkout` | Something else is at `~/code/slate` | Tell the person; set `SLATE_CHECKOUT` to another folder, or they move it |
| `the ocr extra … didn't install` | OCR gates will say UNCHECKED | `slate doctor --install` offers it, or `uv pip install --python ~/code/slate/.venv/bin/python -e "$HOME/code/slate[ocr]"` |
| `moved aside and linked` | Something that wasn't Slate's was in the way | Nothing: it's in the backup folder named in the output, and uninstall puts it back |
| `An MCP server called 'slate' is already registered … and it isn't Slate's` | Another tool uses that name | Tell the person; only they decide whether to remove it |
| `waiting for a yes` / `waiting for the owner's yes` | A step needs a person | Ask, then rerun with `--yes` |
| `slate: Slate's Python environment is missing` (exit 3) | The shim can't find `.venv` | Run `~/code/slate/install.sh` again |
| `diverged` or `Local changes … would be overwritten` from `slate update` | Git won't fast-forward | Tell the person; don't reset, stash or force anything yourself |
