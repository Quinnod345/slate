# Slate plugin

Slate is a film studio any agent can run: a brief becomes a finished, gated film, with story, voice, motion, sound, QA
and delivery each checked by a machine before it counts as done. This plugin brings Slate's skills and its MCP server
to Claude Code (and to Codex, OpenClaw and other clients that read Agent Plugins). It's for people other than Slate's
author, who links the skills from his checkout instead.

## Before you install it

The plugin is the front door, not the studio. Slate itself (its command line, render engine and job queue) runs on
your Mac, so install it first, with one command in Terminal:

```sh
git clone https://github.com/Quinnod345/slate.git ~/code/slate && ~/code/slate/install.sh
```

Then run `slate doctor` and fix anything it names. The plugin's MCP server runs `slate mcp`, so `slate` has to be on
your PATH (the installer links it into `~/.local/bin`).

## Install

In Claude Code:

```
/plugin marketplace add Quinnod345/slate
/plugin install slate@slate
```

In Codex, add the same repository as a plugin marketplace. In OpenClaw, `openclaw plugins install` with the repository
as the marketplace. The repository is private for now, so your git credentials need access to it.

## What's inside

- `skills/`: Slate's skills, copied from the repository's `skills/` folder by `slate skills build`. Don't edit them
  here; edit the source and rebuild. Each copy carries a Codex sidecar (`agents/openai.yaml`).
- `.mcp.json` (Claude Code) and `mcp.json` (Agent Plugins): both start `slate mcp`, a thin server over Slate's command
  line and job queue. Long work (renders, voicing, QA, delivery) always runs as a background job.
- `.claude-plugin/plugin.json` (Claude Code) and `plugin.json` (Agent Plugins): the two manifests over the same folder.
- `claude-ai/`: knowledge-only editions of each skill, zipped for uploading to a regular Claude chat.
- `openclaw/entries.json`: the skill and MCP entries for OpenClaw's config.

There's no `bin/` folder on purpose: Claude's chat and Cowork refuse to install a plugin that has one. The `slate`
command comes from the install above.

## Licence

Slate is proprietary for now (see `LICENSE`). Using it needs Quinn O'Donnell's permission; access to the repository
comes with that permission.
