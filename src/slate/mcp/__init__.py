"""Slate's MCP server: a thin, narrow front door over the command line and the job queue.

The command line is the contract (Oneiro's research slices can't see MCP servers, and every agent can run a shell), so
every tool here maps to a `slate` command and says which one in its result. The server exists for clients that work
better with typed tools, above all the Claude desktop app through Slate's desktop extension (`mcpb/`).

Three rules shape it:

- Long work always becomes a job. Renders, voicing, the score, QA sweeps, review packets and delivery go to the queue
  and return a job id at once; nothing blocks a chat while a render runs.
- A small allowlist. Only the stages in `slate.mcp.policy` run, with their arguments checked first. After Effects
  steps, approvals, budget changes and connecting accounts are a person's to do in their own terminal.
- Spend is explicit. A stage that spends needs `spend=true`, which an agent passes only after the person has seen the
  estimate and said yes; the ledger's budgets still apply on top.

Modules: `policy` (the allowlist and argument checks), `tools` (what each tool does, as plain functions), `server`
(the MCP wiring), `mcpb` (the desktop extension) and `cli` (`slate mcp`, `slate mcpb build`). Importing this package
doesn't import the MCP SDK; only serving does, so `slate --help` stays fast.
"""
