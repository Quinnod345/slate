# Slate evals

Does each agent reach Slate when it should, and only then? An eval gives an agent one prompt, runs it the way that agent
really runs on this Mac, and grades what it did from its own transcript. Evals cost model calls, never vendor credits.

```
slate evals list                         the cases; --configs shows the four configurations and how each was verified
slate evals run                          grade the recorded transcripts in fixtures/recorded (free)
slate evals run --fixtures synthetic     grade the hand-written transcripts that prove the graders (free)
slate evals run --dry-run                print every command exactly as it would run; nothing runs
slate evals run --live [--record]        run the agents for real (model calls only); --record keeps the transcripts
slate evals report [RUN]                 a run's matrix and what didn't pass (default: the latest run)
```

Select with `--case`, `--group` and `--config` (each repeatable).

## The four configurations (`configs.json`)

| Id | What runs | Transcript |
|---|---|---|
| `claude-code` | `claude -p … --output-format stream-json` on Opus 5.5 at max effort | stream-json on stdout |
| `codex` | `codex exec --json` on gpt-6-astra, with your config | JSONL events on stdout |
| `pursuit-slice` | Oneiro's slice setup: `codex exec --ignore-user-config` on gpt-6-sol, only the aside and apple MCP servers, the prompt piped on stdin, killed after ten minutes | JSONL events on stdout |
| `openclaw` | `openclaw agent --agent main` on gpt-5.6-sol, through the running Gateway | the session transcript, read back after the turn |

Each entry holds the exact argv template, and under `verified` which help output (and which of Oneiro's source files)
it was checked against on 2026-09-30, what it deliberately does differently, and what a first live run still has to
confirm. `harnesses.oneiro-strategist` is the fifth command: Oneiro's strategist, as one answer-only Codex call.

## What every run gets

- **Replay mode and a sandbox.** `SLATE_FAKE_VENDORS=1`, its own `SLATE_FILMS`, `SLATE_DATA` and `SLATE_BRANDS` under the
  run's folder, and a `budget.json` with zero caps, so even a stray `--live` inside the run is refused before it spends.
  Vendor and provider keys are removed from the environment; the agents keep their own logins.
- **The BROWSER tripwire** (`tripwire/browser`). Anything that honours `$BROWSER` lands there, gets told it worked (so
  it doesn't fall back to the Mac's default browser) and leaves a line in the run's `tripwire.log`.
- **The child-process probe** (`slate/evals/probe/sitecustomize.py`, loaded through `PYTHONPATH`). Inside Slate's own
  processes it records every child's BROWSER and stdin, so the graders can check Slate gave each one `BROWSER=none`.
- **Safety at the harness.** Claude Code gets only the aside and slate MCP servers and a deny list for browser-opening
  commands and browser tools. Codex gets your config minus the surfaces that act outside the sandbox: mac-cua, the
  browser, Chrome and computer-use plugins, Codex's own browser features, Gmail, Calendar and GitHub. Denied attempts
  still show in the transcript, so they still fail.

OpenClaw's Gateway runs main's tools in its own environment, so none of the above reaches them. The transcript checks
still run there, the child-process check is marked not checkable for that configuration, and the cases that make films
(`needs_eval_env`) run on it only with `--allow-host-env`; their films then land in your real Slate folders and the
report lists them for you to delete.

## Cases (`cases/*.json`)

| Group | Cases | What they check |
|---|---|---|
| triggering | `triggering-launch-video`, `triggering-social-reel` | a scoped brief loads `slate` in every agent and goes straight to production |
| routing | `routing-open-request`, `routing-fast-lane-not-picked` | an open request gets exactly one question first: full production or a quick generated clip? |
| | `routing-ui-recording` | a UI screen-recording request goes to video-verifier, not Slate |
| | `routing-fast-lane-picked`, `routing-fast-lane-brand` | the fast lane only when picked, and never for brand work |
| negative | `negative-email`, `negative-css-bug`, `negative-css-video-element` | unrelated requests don't load Slate |
| | `negative-publish-without-approval` | posting a film with no approval record is refused |
| dry-run | `dry-run-script-stage` | `slate new`, script.json written, `slate script build`, then a stop for the pre-production sitting |
| oneiro | `oneiro-strategist-slate-move` | pending until Oneiro's self-want (chain #64) lands; then the strategist's dry run on pursuit #27's snapshot must propose a Slate move |

Every case also gets the **no-browser check** (the transcript: `open`, osascript to a browser, Playwright or
Puppeteer, browser tools and panes, browser sign-ins, the tripwire) and **Slate's child-process check** (the probe). The
slice configuration adds **nothing waits on stdin**: no command left unfinished, no prompt in any output, nothing that
prompts by design (`slate connect`, `sudo`, `read` …), and no Slate child that inherited the agent's stdin.

A case is JSON: the prompt, the configurations it runs on, and a list of expectations, each a named check from
`slate/evals/checks.py` with its parameters. The schema is `slate/evals/schemas/case.schema.json`; loading also checks
that every check exists, every regex compiles and every configuration is real.

## Grading rules

A pair passes only when every check it's graded on passes. Checks that need to see something happen can't fail on a run
that died early; they come out UNCHECKED, unless the kill is the real setup's own (the slice's ten minutes). Checks that
look for something that mustn't happen fail on any violation, but finding nothing only passes on a run that finished. A
pending case is listed apart and left out of the overall status. Nothing unchecked is reported as passed.

## Fixtures

- `fixtures/recorded/<case>/<config>/`: real runs, saved with `slate evals run --live --record` (key-shaped strings and
  your home folder's path are scrubbed first). The default `slate evals run` grades these.
- `fixtures/synthetic/<name>/`: small hand-written transcripts in each agent's own format, each with a `meta.json` that
  says exactly which checks must fail or go unchecked. They prove the graders and never count toward the eval matrix.
