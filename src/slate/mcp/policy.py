"""What Slate's MCP server may run, and how an agent's arguments are checked before they reach the command line.

Two lists, both small on purpose:

- `RUN`: quick stages the `run` tool may start. Those that touch media (QA sweeps, review packets, delivery) still go
  to the job queue, so nothing blocks a chat.
- `JOBS`: long stages the `job_submit` tool may queue: renders, voicing, alignment, the score, the mix, verification,
  QA, review and delivery.

Each stage names the options it accepts, with a pattern for each value. Anything else is refused before the command
line sees it: `--go` (After Effects starts only on a person's go), `--by`, `--approved-by` and `--accept` (a person's
decisions), `--live` (spend is `spend=true`, never a flag), `--out` and `--dir` (nothing is written outside the film).
The same checks hold whatever client calls the server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from slate.errors import Refused, SpendRefused, UsageError

INT = r"[0-9]{1,7}"
SMALL_INT = r"[0-9]{1,3}"
NUMBER = r"-?[0-9]{1,3}(?:\.[0-9]{1,3})?"
ID = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}"
IDS = ID + r"(?:," + ID + r"){0,199}"
SHOT = r"[A-Za-z][A-Za-z0-9_.-]{0,31}"
GATE = r"[a-z][a-z0-9_.:-]{0,63}"
PART = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}"
APPROVAL = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}"
MODEL = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}"
JOB_ID = re.compile(r"^j-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
MONTH = re.compile(r"^[0-9]{4}-(?:0[1-9]|1[0-2])$")
MAX_ARGS = 64
MAX_ARG_LEN = 400


@dataclass(frozen=True)
class Stage:
    """One stage an MCP tool may start.

    how: "inline" runs now and returns its result; "detach" asks the stage to queue itself (`--detach`, so the
    stage's own planning, disk reservation and resources apply); "queue" is submitted to the job queue by the server.
    """

    name: str
    words: tuple[str, ...]
    how: str
    summary: str
    module: str
    flags: tuple[str, ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    positional: str | None = None
    max_positional: int = 0
    min_positional: int = 0
    reserved: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    kind: str | None = None
    resources: tuple[str, ...] = ()
    paid: bool = False
    paid_when: tuple[tuple[str, str], ...] = ()

    @property
    def cli(self) -> str:
        return "slate " + " ".join(self.words)

    def is_paid(self, argv: list[str]) -> bool:
        if self.paid:
            return True
        return any(f"{opt}={value}" in argv for opt, value in self.paid_when)


_QA = Stage("qa", ("qa",), "detach", "every quality gate (or only the named ones), run as a background job",
            "slate.qa.cli", flags=("--quick",), positional=GATE, max_positional=40,
            reserved=("triage", "psnr", "unpark"), extra=("--detach",))


_REVIEW = Stage("review", ("review",), "queue",
                "the review packet for the current version: phone cut, contact sheet, what changed, open questions",
                "slate.finish.cli", kind="review")
_DELIVER = Stage("deliver", ("deliver",), "queue", "the delivery set and its manifest (written last)",
                 "slate.finish.cli", kind="deliver")

RUN: dict[str, Stage] = {s.name: s for s in (
    Stage("script lint", ("script", "lint"), "inline", "check script/script.json against the script gates; writes nothing",
          "slate.story.cli"),
    Stage("script build", ("script", "build"), "inline",
          "gate the script and write script/storyboard.json and SCRIPT.md (refuses on any failed gate)",
          "slate.story.cli", flags=("--check", "--from-takes")),
    Stage("timing", ("timing",), "inline", "shots, voice cues, words, events and the timeline, on the film's clock",
          "slate.timing.cli"),
    Stage("timing misses", ("timing", "misses"), "inline", "word lookups that fell back to an estimate (empty at hand-in)",
          "slate.timing.cli"),
    Stage("timing show", ("timing", "show"), "inline", "one shot on the clock: frames, read, words, events",
          "slate.timing.cli", positional=SHOT, min_positional=1, max_positional=1),
    _QA,
    Stage("qa list", ("qa",), "inline", "the gates, where each comes from, and its latest status", "slate.qa.cli",
          extra=("--list",)),
    Stage("qa triage", ("qa", "triage"), "inline", "what isn't passing, most severe first, with the next step for each",
          "slate.qa.cli"),
    Stage("voice estimate", ("voice", "tts"), "inline", "what voicing the lines would cost; nothing is spent",
          "slate.voice.cli", options=(("--only", IDS),), extra=("--estimate",)),
    Stage("score estimate", ("score", "compose"), "inline", "what composing the score would cost; nothing is spent",
          "slate.sound.cli", positional=PART, max_positional=40, extra=("--dry-run",)),
    _REVIEW,
    _DELIVER,
)}

JOBS: dict[str, Stage] = {s.name: s for s in (
    Stage("render", ("render",), "detach", "render every act, one act, or a frame range (chunked and resumable)",
          "slate.render.cli", flags=("--draft", "--resume", "--fresh"),
          options=(("--act", SMALL_INT), ("--from", INT), ("--to", INT), ("--engine", "remotion|web"),
                   ("--samples", SMALL_INT), ("--sub", SMALL_INT)), extra=("--detach",)),
    Stage("voice tts", ("voice", "tts"), "detach", "voice every line, or only --only these shots (spends)",
          "slate.voice.cli", flags=("--force",), options=(("--only", IDS), ("--seed", INT), ("--approval", APPROVAL)),
          extra=("--detach",), paid=True),
    Stage("voice align", ("voice", "align"), "detach", "word timings for the takes (Whisper; ElevenLabs spends)",
          "slate.voice.cli", options=(("--only", IDS), ("--engine", "whisper|elevenlabs"), ("--model", MODEL)),
          extra=("--detach",), paid_when=(("--engine", "elevenlabs"),)),
    Stage("timing words", ("timing", "words"), "queue", "time every word of the takes, then rebuild the timing",
          "slate.timing.cli", flags=("--realign",), options=(("--engine", "whisper|elevenlabs"),), kind="whisper",
          resources=("whisper",), paid_when=(("--engine", "elevenlabs"),)),
    Stage("score compose", ("score", "compose"), "queue", "compose the score's parts on Eleven Music (spends)",
          "slate.sound.cli", flags=("--force",), positional=PART, max_positional=40, kind="music",
          resources=("elevenlabs_music",), paid=True),
    Stage("score assemble", ("score", "assemble"), "queue", "place, join and repair the takes into the score",
          "slate.sound.cli", kind="score"),
    Stage("mix", ("mix",), "queue", "the delivery mix: the film's loudness targets, four stems that sum to it",
          "slate.sound.cli", flags=("--no-score", "--calibrate"), options=(("--under", NUMBER), ("--duck", NUMBER)),
          kind="mix"),
    Stage("verify", ("verify",), "queue", "the sound gates on the mix", "slate.sound.cli", positional=GATE,
          max_positional=40, kind="verify"),
    _QA,
    _REVIEW,
    _DELIVER,
)}

# Options an agent may never pass through the server, with the reason it's told.
REFUSED_OPTIONS: dict[str, str] = {
    "--live": "Spending is decided by spend=true on job_submit, after the person has seen the estimate; --live isn't "
              "passed through.",
    "--go": "After Effects steps start only on a person's go, in their own terminal.",
    "--by": "Approvals and acceptances are a person's to record, in their own terminal.",
    "--approved-by": "That's a person's decision, recorded with the command line in their own terminal.",
    "--accept": "Accepting a REVIEW item is a person's call: slate qa triage --accept GATE:ITEM --by NAME --reason TEXT.",
    "--repin": "Re-pinning a film's voice needs a person's approval: run it with the command line.",
    "--reason": "Reasons go with a person's decision, recorded with the command line.",
    "--out": "Nothing is written outside the film folder from here.",
    "--dir": "Nothing is written outside the film folder from here.",
    "--film": "Pass the film as the tool's film argument.",
    "--json": "The server always asks for JSON.",
    "--detach": "The server decides what runs as a job.",
    "--wait": "Jobs never block a chat: poll with job_status.",
    "--in-queue": "That flag belongs to the job runner.",
}


def stage(name: str, table: dict[str, Stage], *, tool: str) -> Stage:
    """Look a stage up, or refuse with the allowlist and, for well-known stages, where they belong instead."""
    found = table.get((name or "").strip())
    if found:
        return found
    other = JOBS if table is RUN else RUN
    if (name or "").strip() in other:
        where = "job_submit" if other is JOBS else "run"
        raise Refused(f"{name!r} isn't a {tool} stage; use {where} for it.",
                      hint=f"{tool} runs: {', '.join(table)}.")
    head = (name or "").strip().split(" ")[0]
    reasons = {
        "ae": "After Effects steps start only on a person's go, in their own terminal (slate ae ... --go).",
        "master": "Masters start with a person: slate master, in their own terminal.",
        "approve": "Approvals are a person's: slate approve <what> <path> --by NAME, in their own terminal.",
        "budget": "Budgets change only by a person, in their own terminal (the budget tool shows them).",
        "connect": "Accounts are connected by a person, in their own Terminal: slate connect <service>.",
        "gen": "Generated shots run from the command line, where the estimate, the takes and the judging live.",
        "plates": "Generated plates run from the command line, where the estimate, the takes and the judging live.",
        "brand": "Brand onboarding runs from the command line, with a person approving each reference.",
        "jobs": "The queue itself is managed from the command line; job_status shows it.",
    }
    raise Refused(f"{name!r} isn't something the MCP server runs.",
                  hint=reasons.get(head, "") + (" " if head in reasons else "")
                  + f"{tool} runs: {', '.join(table)}.")


def check_args(st: Stage, args: list[str] | None) -> list[str]:
    """An agent's extra arguments, checked against the stage's allowlist. Returns them in canonical form
    (`--opt=value`, flags, then positionals), ready to append to the command."""
    args = list(args or [])
    if len(args) > MAX_ARGS:
        raise UsageError(f"Too many arguments ({len(args)}); {MAX_ARGS} is the most one stage takes here.")
    options = dict(st.options)
    flags_out: list[str] = []
    opts_out: list[str] = []
    positionals: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if not isinstance(arg, str):
            raise UsageError("Every argument must be a string.")
        if len(arg) > MAX_ARG_LEN or any(ch in arg for ch in "\x00\n\r"):
            raise UsageError(f"Argument {arg[:40]!r} is too long or holds a line break.")
        if arg.startswith("--"):
            name, has_value, value = arg.partition("=")
            if name in REFUSED_OPTIONS:
                raise Refused(f"{name} isn't passed through the MCP server.", hint=REFUSED_OPTIONS[name])
            if name == "--dry-run":
                if has_value:
                    raise UsageError("--dry-run takes no value.")
                flags_out.append(name)
            elif name in st.flags:
                if has_value:
                    raise UsageError(f"{name} takes no value.")
                if name not in flags_out:
                    flags_out.append(name)
            elif name in options:
                if not has_value:
                    i += 1
                    if i >= len(args):
                        raise UsageError(f"{name} needs a value.")
                    value = args[i]
                    if not isinstance(value, str):
                        raise UsageError(f"{name} needs a string value.")
                if not re.fullmatch(options[name], value or ""):
                    raise UsageError(f"{name} {value!r} isn't a value {st.cli} accepts here.")
                opts_out.append(f"{name}={value}")
            else:
                allowed = sorted({*st.flags, *options, "--dry-run"})
                raise Refused(f"{name} isn't allowed for {st.cli} through the MCP server.",
                              hint=f"Allowed here: {', '.join(allowed)}. Anything else, run the command line.")
        elif arg.startswith("-") and arg != "-":
            raise UsageError(f"Short options like {arg!r} aren't accepted here; use the long form.")
        else:
            if st.positional is None:
                raise UsageError(f"{st.cli} takes no plain arguments here (got {arg!r}).")
            if arg in st.reserved or not re.fullmatch(st.positional, arg):
                raise UsageError(f"{arg!r} isn't a name {st.cli} accepts here.")
            positionals.append(arg)
        i += 1
    if len(positionals) > st.max_positional:
        raise UsageError(f"{st.cli} takes at most {st.max_positional} names here (got {len(positionals)}).")
    if len(positionals) < st.min_positional:
        raise UsageError(f"{st.cli} needs {st.min_positional} name{'s' if st.min_positional > 1 else ''} here.")
    return flags_out + opts_out + positionals


def require_spend(st: Stage, argv: list[str], spend: bool) -> None:
    """A stage that spends runs only with spend=true: the agent has shown the person the estimate and they agreed."""
    if st.is_paid(argv) and not spend:
        estimate = {"voice tts": "run with 'voice estimate'", "score compose": "run with 'score estimate'"}
        how = estimate.get(st.name, "the command's --dry-run or --estimate")
        raise SpendRefused(
            f"{st.cli} spends vendor credits, so it needs spend=true.",
            hint=f"Show the person what it costs first ({how}), and submit again with spend=true once they've said "
                 "yes. The film's budget and the monthly caps still apply.")


def check_job_id(job_id: str) -> str:
    job_id = (job_id or "").strip()
    if not JOB_ID.match(job_id):
        raise UsageError(f"{job_id!r} isn't a job id (they look like j-20260930-141502-a3f9).",
                         hint="job_status without an id lists recent jobs.")
    return job_id


def check_month(month: str | None) -> str | None:
    if month is None or month == "":
        return None
    if not MONTH.match(month):
        raise UsageError(f"{month!r} isn't a month; use YYYY-MM.")
    return month


def check_value(what: str, value: str | None) -> str | None:
    """A free-text value that becomes one argument: no leading dash, no line breaks, bounded."""
    if value is None:
        return None
    value = str(value)
    if value.startswith("-"):
        raise UsageError(f"{what} can't start with a dash.")
    if any(ch in value for ch in "\x00"):
        raise UsageError(f"{what} holds a null character.")
    return value


__all__ = ["Stage", "RUN", "JOBS", "REFUSED_OPTIONS", "stage", "check_args", "require_spend", "check_job_id",
           "check_month", "check_value"]
