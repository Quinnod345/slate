# Agents, the CLI and connectors

Slate has one source of truth and many front doors. The same skills, CLI and MCP server serve every agent on the
machine, so behaviour doesn't depend on which agent picked up the request.

## CLI first

Every stage is a command, and every command has `--json` (one object on stdout, logs on stderr) and the same exit
codes: 0 ok, 1 a gate failed or a refusal, 2 usage, 3 a missing prerequisite, 4 spend refused (budget or approval),
5 busy (After Effects open, a job running). Every agent can run a shell command; not every agent can see MCP servers,
so the CLI is the contract and MCP is a convenience. `slate --help` lists the commands and `slate <command> --help`
explains each.

## The agents

| Agent | How it reaches Slate | Worth knowing |
|---|---|---|
| Claude Code | skills in its skills folder, the MCP server, the CLI | It can run sub-agents; when the agent can, acts are built in parallel, otherwise in order. |
| Codex | skills in its agents skills folder, `codex mcp add`, the CLI | Loads AGENTS.md files from its home and from the git root down, concatenated, with a size cap. |
| OpenClaw (and the agents it deploys) | skills in the shared agents folder, its MCP settings, the CLI | Its gateway's PATH may not include the user's local bin, so the CLI is linked where it can see it. Skill env and API-key values reach host runs only, not the sandbox. |
| Research slices (`codex exec` with the user config ignored) | skills and the CLI only; no user MCP servers | Killed after ten minutes, stdin a pipe: everything long is a detached job the slice submits and polls, and nothing ever prompts. |
| A Claude chat or Cowork | a knowledge-only skill; the Slate desktop extension submits jobs | Plans, writes and voices; starts renders only through the extension. |

`slate doctor` checks each agent's context (`--context`) and names anything missing, including a skill listed twice.

Slate announces itself to OpenClaw agents through its skill's own name and description. It never adds lines to an
agent's AGENTS.md (one is already at its 20,000-character injection cap) or TOOLS.md (no longer injected).

## Paid stages from the OpenClaw gateway go through the job queue

The OpenClaw gateway runs its agents' shell commands behind an egress proxy that allows only the hosts its owner listed,
and by default the voice and generation vendors aren't among them: a paid call made inside the gateway fails at the
proxy's tunnel (a 403), whatever the key. The job runner is a launchd agent outside the gateway, so from inside it:

- **Submit every paid stage as a job, never inline.** Use the stage's `--detach` (`slate voice tts --detach`,
  `slate gen run --detach`), or `slate job submit` for a stage without one, or the MCP server's `job_submit`; then
  follow it with `slate job status <id>`. The runner makes the vendor call outside the proxy, through the same ledger.
- **Free work runs where you are**: timing, the gates, Whisper, renders and the mix make no vendor call.
- **A vendor row in `slate doctor --context openclaw-gateway`** that can't reach a vendor is the proxy, not the
  network. The fixes are the queue (above) or the owner adding the vendors' hosts to the gateway's allowlist in their
  own security config, which Slate never edits.

## MCP

`slate mcp` is a thin stdio server over the CLI and the job queue. Use it where the agent has it; the CLI does
everything it does.

## Connectors: the chat lane

- **ElevenLabs** and **Higgsfield** each have a hosted MCP connector for chat clients. Both sign in with OAuth in a
  browser, so they're for people in chat, never for pipelines or headless agents, which call the APIs with keys.
- Generations made through a chat connector bypass Slate's ledger. The ledger catches them by reconciling balance
  snapshots at the start and end of each session (budgets.md).
- The ElevenLabs connector also exposes third-party image and video models; generations there bill ElevenLabs credits
  and bypass the ledger too.
- Higgsfield points agents such as Claude Code and OpenClaw to its CLI and skills rather than its hosted connector; its
  CLI bills app credits, the unfunded pool (routing.md).

## On claude.ai

A plugin's skills load in chat and Cowork; agents and hooks load only in Cowork and Code; remote MCP servers appear on
the Connectors tab (authenticated by OAuth, not editable after adding), and local servers load only in a locally
running Cowork session. The claude.ai copy of Slate's skills has its references inlined (`slate skills build`).

## Keys

Keys come from the environment first, then the macOS Keychain. They are set by a person in their own Terminal with
`slate connect`, never typed into chat, never printed, logged, written to a file or put in a URL. A missing key stops
the command with exit code 3 and says which `slate connect` to run.

## Off-limits

Company operations servers (ticketing, internal dashboards, company databases) are not part of Slate and are never
used for film work.
