"""Reading the shell commands an agent ran.

Agents wrap commands in all sorts of ways. Codex runs `/bin/zsh -lc "cd x && slate new …"`, Claude Code runs the line
as written, and OpenClaw's exec runs whatever the model typed. The graders need the programs and their arguments, so
this module:

- unwraps `bash -lc '…'`, `zsh -c …` and `eval …` (recursively);
- drops heredoc bodies, because they're data, not commands (a script that *mentions* `open https://…` launches nothing);
- splits on `&&`, `||`, `;`, `|`, `&`, subshells and backticks;
- peels off `VAR=value` assignments and wrappers such as `env`, `sudo`, `nohup`, `timeout 30` and `caffeinate -i`.

It's a reader, not a shell. When quoting is broken it falls back to splitting on whitespace, so a grader never crashes
on an odd command; it may see a little less, and the checks that depend on it say so in their evidence.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
_PUNCT = "();<>|&"
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_WRITE_OPS = {">", ">>", ">|", "&>", "&>>"}

# Wrappers that run the next word as the program, with the flags of theirs that take a value.
_WRAPPERS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"},
    "env": {"-C", "-S", "-P"},
    "nohup": set(),
    "time": {"-f", "-o"},
    "command": set(),
    "exec": {"-a"},
    "builtin": set(),
    "nice": {"-n"},
    "caffeinate": {"-t", "-w"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "gtimeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "arch": set(),
    "xargs": {"-I", "-n", "-P", "-L", "-s", "-E", "-d"},
}
_TAKES_DURATION = {"timeout", "gtimeout"}
_UV_VALUE_FLAGS = {"--project", "--directory", "--with", "--with-editable", "--with-requirements", "--python", "-p",
                   "--env-file", "--package", "--extra", "--group", "--index", "--index-url", "--extra-index-url",
                   "--find-links", "-f", "--config-file", "--cache-dir", "-w"}

# Programs that name a file without reading it (a skill isn't "loaded" by `ls`-ing its folder).
NON_READING = {"ls", "stat", "test", "[", "find", "realpath", "readlink", "file", "wc", "du", "touch", "mkdir", "ln",
               "rm", "chmod", "which", "type", "cd", "pushd", "echo", "printf"}

# `<root>skills/<name>/SKILL.md`: ~/.agents/skills, ~/.claude/skills, a repo's skills/, OpenClaw's workshop-skills/…
SKILL_FILE_RE = re.compile(r"(?:^|[/\s'\"=:(])[\w.-]*skills/(?P<name>[A-Za-z0-9][\w.:-]*)/SKILL\.md\b")

PYTHONS = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
_CODE_WRITE_RES = (
    re.compile(r"open\(\s*(['\"])(?P<p>[^'\"]+)\1\s*,\s*(['\"])[wax]"),
    re.compile(r"Path\(\s*(['\"])(?P<p>[^'\"]+)\1\s*\)\s*\.write_(?:text|bytes)"),
    re.compile(r"(?:writeFileSync|writeFile|appendFileSync)\(\s*(['\"`])(?P<p>[^'\"`]+)\1"),
)


@dataclass
class Simple:
    """One simple command: the program, its arguments, and what surrounds it."""

    program: str  # basename of the program, e.g. "slate", "open", "osascript"
    argv: list[str]  # the program as written, then its arguments (wrappers and assignments removed)
    assignments: dict[str, str] = field(default_factory=dict)  # VAR=value for this command only
    redirects: list[tuple[str, str]] = field(default_factory=list)  # (operator, target)
    unset: list[str] = field(default_factory=list)  # env -u NAME
    wrappers: list[tuple[str, list[str]]] = field(default_factory=list)  # peeled off: (name, its flags)

    @property
    def args(self) -> list[str]:
        return self.argv[1:]

    @property
    def text(self) -> str:
        return " ".join(self.argv)


@dataclass
class SlateCall:
    """A `slate …` invocation: its subcommand words (flags removed) and every argument as written."""

    words: list[str]
    args: list[str]
    live: bool

    def sub_is(self, sub: str) -> bool:
        """True when the subcommand starts with `sub` ("new", "script build"). An empty `sub` matches any call."""
        want = sub.split()
        return self.words[: len(want)] == want

    @property
    def sub(self) -> str:
        return " ".join(self.words[:2])


def strip_heredocs(text: str) -> tuple[str, list[str]]:
    """Remove heredoc bodies (data, not commands). Returns the text without them, and the bodies."""
    lines = text.split("\n")
    kept: list[str] = []
    bodies: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        i += 1
        for match in _HEREDOC_RE.finditer(line):
            terminator = match.group(2)
            body: list[str] = []
            while i < len(lines) and lines[i].strip() != terminator:
                body.append(lines[i])
                i += 1
            i += 1  # the terminator line
            bodies.append("\n".join(body))
    return "\n".join(kept), bodies


def _tokens(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=_PUNCT)
    lex.whitespace_split = True
    return list(lex)


def _is_separator(tok: str) -> bool:
    return bool(tok) and all(c in "();|&" for c in tok)


def _is_redirect(tok: str) -> bool:
    core = tok.lstrip("0123456789")
    return bool(core) and all(c in _PUNCT for c in core) and ("<" in core or ">" in core)


def parse(text: str, _depth: int = 0) -> list[Simple]:
    """Every simple command in `text`, with shell wrappers unwrapped."""
    if not text or not text.strip():
        return []
    body, _bodies = strip_heredocs(text)
    body = body.replace("\\\n", " ").replace("`", " ; ").replace("\n", " ; ")
    try:
        toks = _tokens(body)
    except ValueError:  # unbalanced quotes: read what we can
        toks = body.split()
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in toks:
        if _is_separator(tok):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        segments.append(current)
    out: list[Simple] = []
    for seg in segments:
        simple = _simple(seg)
        if simple is None:
            continue
        inner = _inner_command(simple)
        if inner is not None and _depth < 4:
            out.extend(parse(inner, _depth + 1))
            continue
        out.append(simple)
    return out


def _simple(seg: list[str]) -> Simple | None:
    words: list[str] = []
    redirects: list[tuple[str, str]] = []
    i = 0
    while i < len(seg):
        tok = seg[i]
        if _is_redirect(tok):
            target = seg[i + 1] if i + 1 < len(seg) else ""
            redirects.append((tok, target))
            i += 2
            continue
        words.append(tok)
        i += 1
    assignments: dict[str, str] = {}
    unset: list[str] = []

    def take_assignments() -> None:
        while words and _ASSIGN_RE.match(words[0]):
            key, _, value = words.pop(0).partition("=")
            assignments[key] = value

    take_assignments()
    wrappers: list[tuple[str, list[str]]] = []
    for _ in range(8):  # nested wrappers: `sudo env A=1 nohup slate …`
        if not words or os.path.basename(words[0]) not in _WRAPPERS:
            break
        name = os.path.basename(words.pop(0))
        takes = _WRAPPERS[name]
        flags: list[str] = []
        while words and words[0].startswith("-") and words[0] != "-":
            flag = words.pop(0)
            flags.append(flag)
            if name == "env" and flag == "-u" and words:
                unset.append(words.pop(0))
            elif name == "env" and flag.startswith("--unset="):
                unset.append(flag.split("=", 1)[1])
            elif flag in takes and words:
                words.pop(0)
        if name in _TAKES_DURATION and words:
            words.pop(0)
        wrappers.append((name, flags))
        take_assignments()
    if not words:
        if assignments or unset or redirects or wrappers:
            return Simple(wrappers[-1][0] if wrappers else "", [wrappers[-1][0]] if wrappers else [], assignments,
                          redirects, unset, wrappers)
        return None
    return Simple(os.path.basename(words[0]), words, assignments, redirects, unset, wrappers)


def _inner_command(simple: Simple) -> str | None:
    """The command string a shell wrapper runs: `bash -lc '…'`, `zsh -c …`, `eval …`."""
    if simple.program == "eval":
        return " ".join(simple.args) or None
    if simple.program not in SHELLS:
        return None
    args = simple.args
    for i, arg in enumerate(args):
        if arg == "--":
            break
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return args[i + 1] if i + 1 < len(args) else None
        if not arg.startswith("-"):
            break  # `bash script.sh`: a script we can't see into
    return None


def slate_call(simple: Simple) -> SlateCall | None:
    """The Slate invocation in a simple command, however it was spelled: `slate`, `python -m slate`, `uv run slate`."""
    argv = simple.argv
    prog = simple.program
    rest: list[str] | None = None
    if prog == "slate":
        rest = argv[1:]
    elif PYTHONS.match(prog) and "-m" in argv:
        i = argv.index("-m")
        if i + 1 < len(argv) and argv[i + 1] == "slate":
            rest = argv[i + 2:]
    elif prog == "uv" and "run" in argv:
        k = argv.index("run") + 1
        while k < len(argv):  # skip uv's own options (and their values) to reach the command it runs
            tok = argv[k]
            if tok.startswith("--") and "=" in tok:
                k += 1
            elif tok in _UV_VALUE_FLAGS:
                k += 2
            elif tok.startswith("-"):
                k += 1
            else:
                break
        if k < len(argv) and os.path.basename(argv[k]) == "slate":
            rest = argv[k + 1:]
    elif prog == "uvx" and len(argv) > 1 and argv[1] == "slate":
        rest = argv[2:]
    if rest is None:
        return None
    words: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--film":
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        words.append(tok)
        i += 1
    return SlateCall(words=words, args=list(rest), live="--live" in rest)


def code_argument(simple: Simple) -> str | None:
    """The inline code of `python -c …`, `node -e …`, `perl -e …`, `ruby -e …`, `osascript -e …` (joined)."""
    prog = simple.program
    flags = {"-c"} if PYTHONS.match(prog) else {"-e", "--eval", "-p"} if prog in ("node", "deno", "bun", "perl", "ruby",
                                                                                    "osascript") else set()
    if not flags:
        return None
    parts = [simple.args[i + 1] for i, a in enumerate(simple.args[:-1]) if a in flags]
    return "\n".join(parts) if parts else None


def writes(simple: Simple) -> list[str]:
    """Paths this command writes to: redirects, tee, cp/mv destinations, touch, sed -i, and inline code that opens a
    file for writing."""
    out = [target for op, target in simple.redirects
           if op.lstrip("0123456789") in _WRITE_OPS and target and not target.startswith("&") and target != "/dev/null"]
    prog = simple.program
    plain = [a for a in simple.args if not a.startswith("-")]
    if prog == "tee":
        out += plain
    elif prog in ("cp", "mv", "install", "rsync", "ditto") and len(plain) >= 2:
        out.append(plain[-1])
    elif prog == "touch":
        out += plain
    elif prog in ("sed", "gsed", "perl") and any(a.startswith("-i") for a in simple.args):
        out += plain[1:]
    code = code_argument(simple)
    if code:
        for rx in _CODE_WRITE_RES:
            out += [m.group("p") for m in rx.finditer(code)]
    return out


def skill_names(text: str) -> list[str]:
    """Skill names whose SKILL.md this text names, in order."""
    return [m.group("name") for m in SKILL_FILE_RE.finditer(text or "")]


def skills_read_by(simple: Simple) -> list[str]:
    """Skills whose SKILL.md this command reads (cat, sed, head, rg …); naming it with ls or stat doesn't count."""
    if simple.program in NON_READING:
        return []
    return skill_names(" ".join(simple.argv))
