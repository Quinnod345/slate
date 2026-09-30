# Slate

A film studio any agent can run. Slate turns a brief into a finished, gated film: story, voice, motion, sound, QA and
delivery, each checked by a machine before it counts as done, and each a command that Claude Code, Codex, OpenClaw or
a person in Terminal can run the same way.

It's built from the pipeline behind three brand films, with every brand pulled out: brands are data packs, and each
one gets its own look, motion, voice and sound during onboarding.

> **Status:** 0.1.0 is being built. The film state, story and timing, voice, sound, generation and capture, QA, After
> Effects, render, jobs and packaging modules are in; doctor and connect, brand onboarding, formats, the finish stages
> and `install.sh` are next. `CHANGELOG.md` keeps the list.

## Download

One command clones Slate and runs its installer:

```sh
git clone https://github.com/Quinnod345/slate.git ~/code/slate && ~/code/slate/install.sh
```

The repository is private for now, so your git credentials need access to it (`gh auth setup-git` once, if git asks).
The installer asks before it installs anything.

## Connect it: five steps

1. **Download.** The command above.
2. **Connect Claude.** Slate's skills, command line and MCP server go into Claude Code, and into Codex and OpenClaw when
   they're on the machine: `slate connect claude`.
3. **Connect Higgsfield.** You sign in yourself, in your own Terminal; Slate prints the one command and never opens a
   browser for you. An optional Cloud API key goes into the Keychain: `slate connect higgsfield --api-key`.
4. **Connect ElevenLabs.** An API key, typed into Terminal (never into a chat) and stored in the Keychain:
   `slate connect elevenlabs`.
5. **Connect After Effects.** `slate connect aftereffects` finds After Effects and aerender, checks permissions and
   fonts, installs Higgsfield's AE bridge and renders a 1-second smoke test. Building scenes in After Effects needs its
   "Allow Scripts to Write Files and Access Network" setting, which only you turn on (in After Effects' scripting
   settings); finishing works without it, and Slate never changes it.

Then run `slate doctor`. It names anything missing, with the fix, and goes green when nothing is.

## What you need first

The five steps sit on top of a few things:

| Thing | Free or paid | Who sets it up |
|---|---|---|
| Homebrew tools: ffmpeg, Rubber Band (arm64), uv, Node 24 or later | Free | The installer, asking first |
| The Whisper model (1.6 GB), Slate's Remotion engine and headless Chrome | Free | The installer, asking first |
| GarageBand's sound library (the sampled instruments Slate's sound design plays) | Free | You, from GarageBand's Sound Library |
| Xcode command-line tools (only for iOS app captures) | Free | You |
| After Effects | Paid (Creative Cloud) | You |
| After Effects' "Allow Scripts to Write Files and Access Network" setting (to build scenes in AE) | Free | You, in After Effects' scripting settings |
| ElevenLabs: Creator works; Pro or higher for lossless voice | Paid | You |
| A Higgsfield Cloud API balance (generated plates, optional) | Paid, per generation | You |
| A Remotion licence, only if a company's published film needs one (see below) | Free for individuals, small teams and evaluation | You, when you decide to publish |
| Brand fonts | Licensed per brand | The brand |

## Talking to it

Open a new Claude Code session (in Terminal, or the Code tab of the Claude desktop app) and ask for the film you want:

> Make a 45-second launch video for Acme.

That's a scoped brief, so Slate goes straight into production: it keeps your words as the brief, checks the brand has
an approved pack (or onboards it first), and works through the stages with `slate status` telling any agent, in any
session, where the film stands and what's next. An open request ("make a video") gets one question first: a full
production, or a quick generated clip?

You approve at two sittings: pre-production (the checklist, claims, script, voice shortlist and look test) and the
final cut. Hero moments, plates and sound proofs arrive in review packets you answer when you like.

Codex and OpenClaw read the same skills and run the same commands. In a regular Claude chat, Slate's knowledge-only
skill plans, writes and voices a film with you; with Slate's desktop extension installed (`slate mcpb build`, then
double-click the `.mcpb` file it writes in `dist/`), the chat can also start renders, QA and delivery as background
jobs on this Mac.

A few commands worth knowing:

```sh
slate status              # where the film stands, stage by stage, and the one next command
slate guide               # Slate's reference guides (the same files the skills read)
slate job status <id>     # long work runs as detached jobs; this is how you check on one
slate budget show         # spend this month against the caps, and each film's budget
```

## The command line is the contract

Every stage is a `slate` command with `--json` output and shared exit codes (0 ok, 1 gate failed or refused, 2 usage,
3 missing prerequisite, 4 spend refused, 5 busy). Skills, the MCP server and the desktop extension are front doors onto
it, never a second implementation, because some agents (Oneiro's research slices among them) can run a shell but can't
see MCP servers.

- **Claude Code:** the skills, linked into `~/.claude/skills`; the MCP server (`claude mcp add --scope user slate --
  slate mcp`); the command in `~/.local/bin`.
- **Codex:** the skills in `~/.agents/skills` (only there: Codex also reads `~/.codex/skills`, and a skill in both is
  listed twice); `codex mcp add slate -- slate mcp`.
- **OpenClaw:** the skills in `~/.agents/skills`; `openclaw mcp set` for the server; the command also linked into
  `/opt/homebrew/bin`, because the gateway's PATH lacks `~/.local/bin`.
- **Claude chats:** the knowledge-only skill, uploaded from `plugins/slate/claude-ai/`, and the desktop extension.
- **Everyone else:** the plugin in `plugins/slate`, from this repository's marketplace
  (`/plugin marketplace add Quinnod345/slate`, then `/plugin install slate@slate`).

`slate skills build` generates each client's extras from `skills/`; CI fails when a generated copy drifts.

## Spend is explicit

Every paid call goes through one ledger: estimate first, budget check, a row with the vendor's request id before Slate
waits, so a restart never pays twice. Each film has a budget and each mode a monthly cap. Slate never buys credits,
upgrades a plan or starts a licence; when a balance runs out, the work stops and asks. Tests, evals and `slate demo`
replay recorded vendor answers (`SLATE_FAKE_VENDORS=1`) and spend nothing.

## The Remotion licence

Slate's default renderer is Remotion. Remotion is free for individuals, companies of up to three people, non-profits
and evaluation. **A company of four or more needs its own Remotion company licence** to use Slate's Remotion path for
its work. Or it can use Slate's paths that need no Remotion at all: the open web renderer (`slate render --engine
web`, three.js and GSAP captured frame by frame from local pages; still being built) or After Effects (`--engine ae`).
Slate never buys or starts a licence for you.

Other dependencies keep their own licences too; GSAP, for one, is under its own no-charge Standard License.

## The browser guardrail

Slate never opens a web browser. It's enforced in code, not left to good intentions:

1. Every child process Slate starts gets `BROWSER=none`, and Remotion Studio always runs with `--no-open`. Previews
   are stills and MP4s.
2. `slate connect higgsfield` never runs a browser sign-in from an agent; it prints the command for you to run in your
   own Terminal.
3. Web captures and any other web step go through Aside. The open web renderer's headless browser only ever loads
   Slate's own local pages.
4. `slate doctor` reports your default browser and flags any step that would open it.
5. An eval checks that no browser other than Aside is ever launched.

## Where things live

| What | Where | Moved by |
|---|---|---|
| Code, skills, formats, neutral brand packs, the model registry | This checkout (`~/code/slate`) | |
| Films, one folder each | `~/Movies/Slate/<film-id>/`, outside iCloud | `SLATE_FILMS` |
| Engine installs, job queue, ledger, budgets, caches, logs | `~/Library/Application Support/Slate/` | `SLATE_DATA` |
| Real brand packs (company packs stay on your own Mac) | `~/code/slate-brands/` | `SLATE_BRANDS` |
| API keys | Your login Keychain, or the environment | `slate connect` |
| The plugin, with generated skill copies and knowledge-only zips | `plugins/slate/` | `slate skills build` |
| The desktop extension's source | `mcpb/` (packed into `dist/`) | `slate mcpb build` |

A film's folder holds everything it knows: `film.json` (format, clock, brand, budget, pins), `film.lock.json` (what
made every derived file), the brief, the one hand-edited `script/script.json`, and the voice, timing, scenes, sound,
renders, masters, QA, review and delivery folders.

## Working on Slate

`AGENTS.md` is the guide for any agent (or person) changing this repository: the contract in
`docs/ARCHITECTURE.md`, the rules, and how to run the tests. `docs/PLAN.md` is the plan, `docs/DECISIONS.md` the
decisions behind it.

## Licence

Proprietary for now; see `LICENSE`. Slate is Quinn O'Donnell's, meant to be shared more widely later.
