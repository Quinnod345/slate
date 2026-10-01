# Slate plugin

Slate is a film studio any agent can run: a brief becomes a finished, gated film, with story, voice, motion, sound, QA
and delivery each checked by a machine before it counts as done. This plugin brings Slate's skills, its MCP server and
the voice and generation vendors' hosted connectors to Claude Code (and to Codex, OpenClaw and other clients that read
Agent Plugins). It's for people other than Slate's author, who links the skills from his checkout instead.

Everything in this folder is written by `slate skills build` from Slate's repository
(do not edit by hand: slate skills build).

## Before you install it

The plugin is the front door, not the studio. Slate itself (its command line, render engine and job queue) runs on
your Mac, so install it first, with one command in Terminal:

```sh
git clone https://github.com/Quinnod345/slate.git ~/code/slate && ~/code/slate/install.sh
```

Then run `slate doctor` and fix anything it names. The plugin's MCP server runs `slate mcp`, so `slate` has to be on
your PATH (the installer links it into `~/.local/bin`).

It needs Claude Code 2.1.0 or later, both the terminal build and the one the desktop app bundles (they
update separately); `slate doctor` checks both.

## Install

In Claude Code:

```
/plugin marketplace add Quinnod345/slate
/plugin install slate@slate
```

**Turn on auto-update for Slate's marketplace** (`/plugin`, Marketplaces): it's off by default for third-party
marketplaces, and without it you won't get fixes.

The repository is private for now, so your git credentials need access to it (`gh auth setup-git` once, if git asks).

Codex and OpenClaw don't need the plugin: the installer above links Slate's skills where both read them and registers
Slate's MCP server with each. Reading this repository as their plugin marketplace isn't confirmed yet (whether Codex
takes a Claude-style marketplace's sources, and whether OpenClaw expands Claude's path placeholders in a bundle; this
plugin uses none). Slate's repository records every client behaviour that's still unconfirmed, with the hands-on check
that settles it, in `tests/packaging/client_behaviours.json`.

## What's inside

- `skills/`: Slate's skills, copied from the repository's `skills/` folder. Each copy carries a Codex sidecar
  (`agents/openai.yaml`).
- `.mcp.json` (Claude Code) and `mcp.json` (Agent Plugins): Slate's own server, which runs `slate mcp`, a thin server
  over Slate's command line and job queue (long work such as renders, voicing, QA and delivery always runs as a
  background job), and the vendors' hosted connectors (below).
- `.claude-plugin/plugin.json` (Claude Code) and `plugin.json` (Agent Plugins): the two manifests over the same folder.
- `claude-ai/`: knowledge-only editions of each skill, zipped for uploading to a regular Claude chat.
- `openclaw/entries.json`: the skill and MCP entries for OpenClaw's config.

There's no `bin/` folder on purpose: Claude's chat and Cowork refuse to install a plugin that has one. The `slate`
command comes from the install above. There's no media either (fonts, sound banks, LUTs, audio, video): claude.ai caps
a plugin at 200 MB and 5,000 files, and `slate skills build` refuses media in a skill.

## The hosted connectors

- **ElevenLabs** (`https://api.elevenlabs.io/v1/mcp`): voices, music and its creative models.
- **Higgsfield** (`https://mcp.higgsfield.ai/mcp`): image and video generation.

Each signs in with OAuth in your own browser the first time you connect it (in Claude Code, `/mcp`; on claude.ai, the
plugin's Connectors tab). No API key goes in any file here. They're for people working in a chat; Slate's own stages
call the vendors' APIs with the keys you gave `slate connect`, through its ledger.

- **Generations made through a connector bypass Slate's ledger.** A working session starts and ends with
  `slate ledger snapshot --all --session <label>` (for Higgsfield, also
  `slate ledger snapshot --provider higgsfield --balance <n> --session <label>` with the balance its connector shows),
  then `slate ledger reconcile --session <label>`, so that spend is caught.
- **Slate never calls some of Higgsfield's connector tools**: `sync_agents`, `tiktok_connect`, `tiktok_reconnect`,
  `tiktok_prepare_publish`, `tiktok_publish_status`, `create_website`, `deploy_website`, `publish_website`. They
  upload a person's skills and a profile, or publish to TikTok or a website. Deny them wherever an agent runs films on
  its own (the rules below do).

## Permissions for unattended runs

An agent that stops to ask before each command stalls an unattended run. These are the commands Slate's agents run
themselves:

| Command | What it's for |
|---|---|
| `slate` | every stage (Slate starts its own renders, mixes and After Effects steps) |
| `ffmpeg` | checking and cutting media by hand |
| `ffprobe` | reading a file's streams and length |
| `whisper` | word timings checked by hand |
| `.venv/bin/python` | Slate's own Python, from the checkout (its tests and tools) |
| `node engine/cli.mjs` | the engine's renders and checks run directly, from the checkout |

aerender isn't on the list on purpose: After Effects work starts only on a person's go, and Slate runs aerender itself
when they give it.

Claude Code, in `~/.claude/settings.json` (or a project's `.claude/settings.json`):

```json
{
 "permissions": {
  "allow": [
   "Bash(slate *)",
   "Bash(ffmpeg *)",
   "Bash(ffprobe *)",
   "Bash(whisper *)",
   "Bash(.venv/bin/python *)",
   "Bash(node engine/cli.mjs *)",
   "mcp__plugin_slate_slate"
  ],
  "deny": [
   "mcp__plugin_slate_higgsfield__sync_agents",
   "mcp__plugin_slate_higgsfield__tiktok_connect",
   "mcp__plugin_slate_higgsfield__tiktok_reconnect",
   "mcp__plugin_slate_higgsfield__tiktok_prepare_publish",
   "mcp__plugin_slate_higgsfield__tiktok_publish_status",
   "mcp__plugin_slate_higgsfield__create_website",
   "mcp__plugin_slate_higgsfield__deploy_website",
   "mcp__plugin_slate_higgsfield__publish_website"
  ]
 }
}
```

Codex, in a rules file such as `~/.codex/rules/slate.rules`:

```
prefix_rule(pattern=["slate"], decision="allow")
prefix_rule(pattern=["ffmpeg"], decision="allow")
prefix_rule(pattern=["ffprobe"], decision="allow")
prefix_rule(pattern=["whisper"], decision="allow")
prefix_rule(pattern=[".venv/bin/python"], decision="allow")
prefix_rule(pattern=["node", "engine/cli.mjs"], decision="allow")
```

## Licence

Slate is proprietary for now (see `LICENSE`). Using it needs Quinn O'Donnell's permission; access to the repository
comes with that permission.
