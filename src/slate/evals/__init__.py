"""Slate's evals: does each agent reach Slate when it should, and only then?

An eval gives an agent one prompt, runs it the way that agent really runs on this Mac, and grades what it did from its
own transcript. Four configurations (docs/PLAN.md, "Quality: gates, tests and evals"):

- Claude Code on Opus 5.5 (`claude -p`, stream-json);
- Codex CLI on gpt-6-astra (`codex exec --json`);
- the pursuit-slice setup Oneiro's engine uses (`codex exec --ignore-user-config` on gpt-6-sol, aside and apple MCPs
  only, stdin a pipe, killed after ten minutes);
- OpenClaw main on gpt-5.6-sol (`openclaw agent`, through the running Gateway).

Evals cost model calls, never vendor credits: every run gets replay mode (`SLATE_FAKE_VENDORS=1`), sandbox film and data
folders and a zero-cap budget, and nothing runs live unless someone passes `--live`. By default `slate evals run`
grades recorded transcripts in `evals/fixtures/`; `--dry-run` prints the exact commands.

The pieces: `suite` (configurations and cases on disk), `transcript` and `shell` (reading what an agent did),
`checks` (the graders), `commands` (building each command), `runner`, `report`, `preconditions`, `strategist` (Oneiro's
strategist dry run) and `cli` (`slate evals list|run|report`).
"""

from __future__ import annotations


def register(sub) -> None:
    """Add `slate evals list|run|report` to the CLI (imported lazily so `slate --help` stays fast)."""
    from slate.evals.cli import register as _register  # noqa: PLC0415

    _register(sub)
