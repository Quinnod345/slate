# Slate's desktop extension

This folder is the source of Slate's extension for the Claude desktop app. `slate mcpb build` packs it into
`dist/slate-<version>.mcpb`; double-click that file (or drag it into Settings, Extensions) to install it.

With it, a regular Claude chat can use the Slate installed on this Mac: see where a film stands, start a new film from
a request, write and build the script, voice it, and start renders, QA sweeps, review packets and delivery as
background jobs. Long work always runs in Slate's job queue, so a chat never waits on a render, and a job keeps going
after the chat moves on.

## How it works

- `manifest.json` describes the extension. Its `version` and `tools` come from Slate itself: `slate mcpb build --check`
  fails when they drift, and `slate mcpb build --sync` rewrites them. Everything else is written by hand.
- `server/slate-mcp` is the launcher. It holds no Slate code: it starts the `slate mcp` installed on this Mac (the path
  in the extension's settings, then `~/.local/bin/slate`, then `/opt/homebrew/bin/slate`), so the chat and the command
  line always run the same Slate with the same films, job queue, ledger and keys. When Slate isn't installed, it says
  so and exits.

## What it won't do

Spending vendor credits needs the person's yes first (the chat shows the estimate, then passes `spend=true`). After
Effects steps, approvals, budget changes and connecting accounts stay with the person, in their own terminal; the
server refuses them. Nothing opens a browser.

Install Slate before the extension; see the README at the root of the checkout.
