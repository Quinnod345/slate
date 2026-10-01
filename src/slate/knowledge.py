"""The knowledge ledger: every lesson the films taught, and where Slate keeps it now.

`knowledge/lessons.json` gives every item of the harvest (the film inventory's lessons; every rule in the films' motion
law, character-motion spec, sound notes, voice-tag notes and act NOTES.md files; and the second harvest of what the film
session added after the first inventory) an id and one destination:

- `code_default`  a real file and symbol that implements it (`file`, `symbol`, `line`), with `terms` that must appear
                  inside that symbol when the lesson names a value;
- `gate`          a real gate (a name in `slate.qa.GATES`, or a module's gate function: `file`, `symbol`) and the
                  pytest node ids that exercise it (`tests`). `enforced: "construction"` marks a rule the code makes
                  impossible to break (the gate is the code path itself, and its tests prove it), so no gate name is
                  looked for in the file;
- `skill_rule`    the skill file that states it (`target`, a path under skills/) and the key `terms` a reader finds
                  there;
- `dropped`       the `reason`: the plan overrides it, it's one film's content, or it's a fact with nothing to carry;
- `gap`           not reflected anywhere yet: the `module` and the exact `change` that would close it (listed in
                  knowledge/GAPS.md for the integrator);
- `pending`       being built in this run: the planned `file` (and `symbol`), the `module` building it and `what`. It
                  counts against the check like a gap until it's re-mapped; the check says when the planned file has
                  landed.

A lesson may also carry `also`: more destinations of the same shapes, checked the same way. Near-duplicates are merged:
the surviving lesson keeps every source and lists the ids it `absorbed`, and `slate knowledge show` finds it by any of
them.

`slate knowledge check` proves the ledger against the checkout as it is, gate by gate (docs/ARCHITECTURE.md §6):

- knowledge.mapped        every harvested item is in the ledger exactly once (as a lesson or an absorbed id), with a
                          destination of a known type;
- knowledge.gaps          no lesson is a gap or pending (each gap names its module and the change that closes it);
- knowledge.destinations  every destination resolves: files and symbols exist, a symbol holds its terms, a skill file
                          holds the rule's key terms, gates exist and their tests exist;
- knowledge.tests         with --run-tests: every gate-type lesson's tests pass, run in one pytest batch;
- knowledge.components    every component in knowledge/components.json lands on a real module path (or one of the two
                          places outside the core it names);
- knowledge.left_behind   nothing the plan left behind is in the repo: no company, product or character names (held here
                          only as hashes, so this file can't carry them either), none of the films' scene files, the
                          render-farm script, the retired v2 sound design, the rejected 3D pass or the website tooling.
                          The ledger may cite source paths in the film project, but its own words (lesson texts, notes,
                          reasons, changes) are held to the same rule.

Only PASS counts. The harvest runs twice (docs/PLAN.md, "Knowledge ledger"): the ledger's `meta.harvests` records each
run, and `meta.next_harvest` says when the next one is due.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from slate import __version__, gates, jsonio, log, paths
from slate.errors import MissingPrerequisite, UsageError
from slate.gates import GateResult

LEDGER = "knowledge/lessons.json"
GAPS = "knowledge/GAPS.md"
COMPONENTS = "knowledge/components.json"
HARVEST = ("knowledge/harvest/inventory_lessons.json", "knowledge/harvest/doc_rules.json",
           "knowledge/harvest/second_harvest.json")
TYPES = ("code_default", "gate", "skill_rule", "dropped", "gap", "pending")
#: The destination types a lesson can land on and still count as unfinished: the check fails while any lesson has one.
OPEN_TYPES = ("gap", "pending")
ENGINE = {"name": "slate knowledge", "version": __version__}

# ------------------------------------------------------------------------------------------ what the plan left behind
#: Company, product and character names, and the licensed face, as SHA-256 prefixes of each word (and one pair of
#: words), the way the kit's and the brand module's own guards hold them: a check that spells the names would carry
#: them into the repo itself. The plan (§2, "Left behind") names this content; hashing the repo's words finds it.
GUARDED_WORDS = frozenset({"2ad60e2919a3ae8f", "e252b79c39aa92dd", "fd73d009d4b851d4", "ff9b5c834ecab632",
                           "18be576b3f695aea", "6689e46b91238025"})
GUARDED_PAIRS = frozenset({"1ed8c4f3b421977d"})
_GUARDED_LENGTHS = frozenset({4, 5, 6, 7, 9, 10})

#: File names only the films' own projects have: the render farm's script, the rejected 3D pass and the films' scenes,
#: the retired v2 sound design (its plumbing was ported under Slate's own names), and the website's build tooling.
LEFT_BEHIND_NAMES = {
    "work.sh": "the second machine's render farm (WORK is never used for renders)",
    "A1Lines.tsx": "the rejected 3D pass",
    "Act1Home.tsx": "a film's scene",
    "Act1Close.tsx": "a film's scene",
    "SOUND_V2.md": "the retired v2 sound design (kept in the film project as a record only)",
    "engine_v2.py": "the retired v2 sound design's cue renderer",
    "visual_sfx_v2.py": "the retired v2 sound design's tables",
    "map_a.py": "the retired v2 sound design's mapping",
    "mapping_A.json": "the retired v2 sound design's mapping",
    "compare_swell.py": "the retired v2 sound design's repair tools",
    "repair_score_A.py": "the retired v2 sound design's repair tools",
    "doc_tables.py": "the retired v2 sound design's doc tables",
    "framer-push.mjs": "the website tooling",
    "framer-files.mjs": "the website tooling",
    "controlscheck.mjs": "the website tooling",
    "kitcheck.mjs": "the website tooling",
    "copyaudit.py": "the website tooling",
    "cmspec.py": "the website tooling",
    "mkpages.py": "the website tooling",
    "pagecopy.py": "the website tooling",
    "framer.d.ts": "the website tooling",
    "tsconfig.framer-files.json": "the website tooling",
}
#: Folder paths from the film project's layout; none of them has a place in Slate.
LEFT_BEHIND_PATHS = {
    "pipeline/r3f/": "the film project's engine folder (its scenes stay there)",
    "src/acts/act": "a film's scenes",
    "sound/v2/": "the retired v2 sound design",
    "framer-code/": "the website tooling",
    "framer/": "the website tooling",
}
#: In the film project (read in place, never written): the files whose copies must not turn up in Slate under any name.
#: Checked by content hash when the film project is on this Mac.
LEFT_BEHIND_ROOTS = ("pipeline/r3f/src/acts", "pipeline/r3f/src/b", "pipeline/r3f/src/home", "pipeline/r3f/work.sh",
                     "pipeline/r3f/src/A1Lines.tsx", "sound/v2/tools", "framer-code", "tools")
#: Files that name the left-behind content on purpose, because they record where Slate came from.
PROVENANCE = {
    "docs/": "the plan and its records say where Slate came from",
    "knowledge/harvest/": "the harvest, verbatim, with its source paths in the film project",
    "knowledge/components.json": "every inventoried component, with its source path in the film project",
    "knowledge/registry_proposal.json": "measurement evidence, cited by its path in the film project",
    "knowledge/lessons.json": "the ledger: every lesson with its source paths in the film project (its own words are "
                              "scanned field by field)",
}
#: The ledger's own words, scanned for guarded names even though the file cites source paths.
LEDGER_TEXT_FIELDS = ("text", "note", "reason", "change", "what")
SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".remotion"})
TEXT_EXT = frozenset({".py", ".md", ".json", ".ts", ".tsx", ".mjs", ".js", ".jsx", ".jsxinc", ".sh", ".toml",
                      ".yaml", ".yml", ".txt", ".html", ".css", ".svg", ".cfg", ".ini", ".plist", ".xml"})
_WORD = re.compile(r"[a-z0-9]+")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------------ loading
def ledger_path(repo: Path | None = None) -> Path:
    return Path(repo or paths.REPO) / LEDGER


def load(repo: Path | None = None) -> dict:
    """The ledger. MissingPrerequisite (exit 3) when the checkout has none."""
    p = ledger_path(repo)
    if not p.is_file():
        raise MissingPrerequisite(f"There's no knowledge ledger at {p}.",
                                  hint="The knowledge module writes knowledge/lessons.json from the harvest.")
    data = jsonio.read(p)
    if not isinstance(data, dict) or not isinstance(data.get("lessons"), list):
        raise UsageError(f"{p} isn't a ledger: it needs a 'lessons' list.")
    return data


def lessons(data: dict) -> list[dict]:
    return [x for x in data.get("lessons") or [] if isinstance(x, dict)]


def find(data: dict, lesson_id: str) -> dict | None:
    """A lesson by its own id or by an id it absorbed."""
    for x in lessons(data):
        if x.get("id") == lesson_id or lesson_id in (x.get("absorbed") or []):
            return x
    return None


def destinations(lesson: dict) -> list[dict]:
    """A lesson's destination first, then its `also` destinations."""
    out = [lesson.get("destination")] if isinstance(lesson.get("destination"), dict) else []
    out += [d for d in lesson.get("also") or [] if isinstance(d, dict)]
    return out


def harvested_ids(repo: Path) -> list[str] | None:
    """Every id in the harvest files the checkout has, or None when it has none of them."""
    found, ids = False, []
    for rel in HARVEST:
        p = repo / rel
        if p.is_file():
            found = True
            ids += [str(x.get("id")) for x in jsonio.read(p) or [] if isinstance(x, dict) and x.get("id")]
    return ids if found else None


# --------------------------------------------------------------------------------------------- reading the checkout
def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


_JS_DEF = (r"(?:^|\n)[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?\s+{n}\b|(?:const|let|var)\s+{n}"
           r"\s*[=:]|class\s+{n}\b|(?:interface|type)\s+{n}\b|{n}\s*=\s*function\b|[\w.$]+\.{n}\s*=\s*function\b|"
           r"{n}\s*:\s*function\b)")
_JS_EXT = frozenset({".ts", ".tsx", ".mjs", ".js", ".jsx", ".jsxinc"})


class Repo:
    """The checkout a ledger is checked against. Each file is read (and each Python file parsed) once per check."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._text: dict[str, str | None] = {}
        self._py: dict[str, ast.Module | None] = {}

    def path(self, rel: str) -> Path:
        return self.root / rel

    def exists(self, rel: str) -> bool:
        return bool(rel) and self.path(rel).exists()

    def text(self, rel: str) -> str | None:
        if rel not in self._text:
            p = self.path(rel)
            try:
                self._text[rel] = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
            except OSError:
                self._text[rel] = None
        return self._text[rel]

    def python(self, rel: str) -> ast.Module | None:
        if rel not in self._py:
            text = self.text(rel)
            try:
                self._py[rel] = ast.parse(text) if text is not None else None
            except SyntaxError:
                self._py[rel] = None
        return self._py[rel]

    # -- symbols
    def symbol(self, rel: str, symbol: str) -> tuple[int, int | None] | None:
        """Where `symbol` is defined in `rel`: (first line, last line or None). None when it isn't there."""
        suffix = Path(rel).suffix
        if suffix == ".py":
            return self._py_symbol(rel, symbol)
        text = self.text(rel)
        if text is None:
            return None
        if suffix in _JS_EXT:
            m = re.search(_JS_DEF.format(n=re.escape(symbol.split(".")[-1])), text)
            return (text.count("\n", 0, m.start() + (1 if text[m.start()] == "\n" else 0)) + 1, None) if m else None
        if suffix == ".json":
            return self._json_symbol(rel, symbol)
        if suffix == ".sh" or rel.endswith(("install.sh", "uninstall.sh")):
            m = re.search(r"(?m)^\s*(?:function\s+)?" + re.escape(symbol) + r"\s*(?:\(\)|=)", text)
            return (text.count("\n", 0, m.start()) + 1, None) if m else None
        i = text.lower().find(symbol.lower())
        return (text.count("\n", 0, i) + 1, None) if i >= 0 else None

    def _py_symbol(self, rel: str, symbol: str) -> tuple[int, int | None] | None:
        node: ast.AST | None = self.python(rel)
        if node is None:
            return None
        for part in symbol.split("."):
            found = None
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ch.name == part:
                    found = ch
                elif isinstance(ch, ast.Assign) and any(isinstance(t, ast.Name) and t.id == part for t in ch.targets):
                    found = ch
                elif isinstance(ch, ast.AnnAssign) and isinstance(ch.target, ast.Name) and ch.target.id == part:
                    found = ch
                if found is not None:
                    break
            if found is None:
                return None
            node = found
        return node.lineno, getattr(node, "end_lineno", None)

    def _json_symbol(self, rel: str, symbol: str) -> tuple[int, int | None] | None:
        text = self.text(rel)
        try:
            cur: Any = json.loads(text or "")
        except ValueError:
            return None
        for part in symbol.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        m = re.search(r'"' + re.escape(symbol.split(".")[-1]) + r'"\s*:', text or "")
        return ((text or "").count("\n", 0, m.start()) + 1 if m else 1), None

    def missing_terms(self, rel: str, terms: Iterable[str], span: tuple[int, int | None] | None = None) -> list[str]:
        """The terms that aren't in the file (or, for a Python symbol, inside that symbol's lines)."""
        text = self.text(rel) or ""
        if span and span[1]:
            text = "\n".join(text.splitlines()[span[0] - 1: span[1]])
        hay = _norm(text)
        return [t for t in terms if _norm(t) not in hay]

    # -- tests
    def has_test(self, node_id: str) -> bool:
        """Whether a pytest node id names a test function that's in the checkout (parameters aren't checked)."""
        rel, _, rest = node_id.partition("::")
        if not rest or not rel.endswith(".py"):
            return False
        tree = self.python(rel)
        if tree is None:
            return False
        parts = [re.sub(r"\[.*$", "", p) for p in rest.split("::")]
        node: ast.AST = tree
        for part in parts:
            found = next((ch for ch in ast.iter_child_nodes(node)
                          if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ch.name == part),
                         None)
            if found is None:
                return False
            node = found
        return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


# --------------------------------------------------------------------------------------------- one destination
@dataclass
class Resolution:
    """Whether a destination holds in the checkout, and what's wrong when it doesn't."""

    problems: list[str] = field(default_factory=list)
    line: int | None = None  # where the symbol (or the skill rule's first term) is now
    moved_from: int | None = None  # the ledger's line, when the symbol has moved since

    @property
    def ok(self) -> bool:
        return not self.problems


def _qa_gates() -> tuple[set[str] | None, str | None]:
    """The names in slate.qa.GATES, or (None, why) when the qa module won't import."""
    try:
        from slate import qa  # noqa: PLC0415

        qa.load_external()
        return set(qa.GATES), None
    except Exception as exc:  # noqa: BLE001 - another module's import error is a finding, not a crash
        return None, f"{type(exc).__name__}: {exc}"


def resolve(dest: dict, repo: Repo, *, qa_names: set[str] | None = None, qa_error: str | None = None) -> Resolution:
    """Check one destination against the checkout."""
    r = Resolution()
    kind = dest.get("type")
    if kind not in TYPES:
        r.problems.append(f"unknown destination type {kind!r}")
        return r
    if kind == "dropped":
        if not str(dest.get("reason") or "").strip():
            r.problems.append("dropped without a reason")
        return r
    if kind == "gap":
        if not str(dest.get("module") or "").strip() or not str(dest.get("change") or "").strip():
            r.problems.append("a gap has to name its module and the change that would close it")
        return r
    if kind == "pending":
        rel = str(dest.get("file") or "")
        if not rel or not str(dest.get("module") or "").strip() or not str(dest.get("what") or "").strip():
            r.problems.append("a pending destination has to name its planned file, its module and what's being built")
        return r
    if kind == "skill_rule":
        target = str(dest.get("target") or dest.get("file") or "").split("#")[0].strip()
        terms = [t for t in dest.get("terms") or [] if str(t).strip()]
        if not target.startswith("skills/") or not target.endswith(".md"):
            r.problems.append(f"a skill rule's target must be a skills/<name>/… .md file, not {target!r}")
            return r
        if not repo.path(target).is_file():
            r.problems.append(f"{target} doesn't exist")
            return r
        if not terms:
            r.problems.append(f"no key terms to find in {target}")
            return r
        missing = repo.missing_terms(target, terms)
        if missing:
            r.problems.append(f"{target} doesn't state it (missing: {'; '.join(missing)})")
        else:
            first = _norm(terms[0])
            r.line = next((n for n, ln in enumerate((repo.text(target) or "").splitlines(), 1) if first in _norm(ln)),
                          None)
        return r
    # code_default and gate: a file and a symbol
    rel, symbol = str(dest.get("file") or ""), str(dest.get("symbol") or "")
    if not rel or not symbol:
        r.problems.append(f"a {kind} needs a file and a symbol")
        return r
    if not repo.path(rel).is_file():
        r.problems.append(f"{rel} doesn't exist")
        return r
    span = repo.symbol(rel, symbol)
    if span is None:
        r.problems.append(f"{rel} has no {symbol}")
        return r
    r.line = span[0]
    if dest.get("line") and int(dest["line"]) != span[0]:
        r.moved_from = int(dest["line"])
    terms = [t for t in dest.get("terms") or [] if str(t).strip()]
    missing = repo.missing_terms(rel, terms, span if rel.endswith(".py") else None)
    if missing:
        r.problems.append(f"{rel}::{symbol} doesn't hold {'; '.join(missing)}")
    tests = [str(t) for t in dest.get("tests") or []]
    for t in tests:
        if not repo.has_test(t):
            r.problems.append(f"no test {t}")
    if kind == "gate":
        name = str(dest.get("gate") or "")
        probe = str(dest.get("check") or name)
        construction = dest.get("enforced") == "construction"
        if dest.get("enforced") not in (None, "construction"):
            r.problems.append(f"unknown enforcement {dest.get('enforced')!r} (only 'construction')")
        if not name:
            r.problems.append("a gate destination needs the gate's name")
        elif not construction and probe not in (repo.text(rel) or "") \
                and probe.rsplit(".", 1)[-1] not in (repo.text(rel) or ""):
            r.problems.append(f"{rel} never names the {probe} gate")
        if dest.get("registry") == "slate.qa.GATES":
            if qa_names is None:
                r.problems.append(f"slate.qa didn't import, so {name} can't be confirmed ({qa_error})")
            elif name not in qa_names:
                r.problems.append(f"{name} isn't in slate.qa.GATES")
        if not tests:
            r.problems.append("a gate without tests")
    return r


# --------------------------------------------------------------------------------------------------- the gates
def gate_mapped(data: dict, repo: Repo) -> GateResult:
    """Every harvested item in the ledger exactly once, with a destination of a known type."""
    name = "knowledge.mapped"
    items = lessons(data)
    seen: Counter[str] = Counter()
    for x in items:
        seen[str(x.get("id"))] += 1
        for a in x.get("absorbed") or []:
            seen[str(a)] += 1
    twice = sorted(k for k, n in seen.items() if n > 1)
    untyped = sorted(str(x.get("id")) for x in items
                     if not isinstance(x.get("destination"), dict) or x["destination"].get("type") not in TYPES)
    harvest = harvested_ids(repo.root)
    details: dict[str, Any] = {"lessons": len(items), "ids": len(seen), "listed_twice": twice, "unmapped": untyped}
    evidence = [LEDGER]
    if harvest is None:
        problems = twice + untyped
        return GateResult.unchecked(
            name, "the checkout has no harvest files, so the ledger can't be proved complete"
            + (f" ({len(problems)} problems in the ledger itself)" if problems else ""),
            evidence=evidence, engine=gates.engine(**ENGINE), details=details)
    evidence += [h for h in HARVEST if repo.exists(h)]
    missing = sorted(set(harvest) - set(seen))
    unknown = sorted(set(seen) - set(harvest))
    details.update(harvested=len(harvest), missing=missing, not_in_harvest=unknown,
                   merged=sum(len(x.get("absorbed") or []) for x in items))
    bad = missing + untyped + twice + unknown
    if bad:
        parts = []
        if missing:
            parts.append(f"{len(missing)} harvested item(s) missing ({', '.join(missing[:5])}…)"
                         if len(missing) > 5 else f"{len(missing)} harvested item(s) missing ({', '.join(missing)})")
        if untyped:
            parts.append(f"{len(untyped)} lesson(s) without a known destination type")
        if twice:
            parts.append(f"{len(twice)} id(s) listed twice")
        if unknown:
            parts.append(f"{len(unknown)} id(s) that aren't in the harvest")
        return GateResult.fail(name, "; ".join(parts), evidence=evidence + bad[:20], engine=gates.engine(**ENGINE),
                               details=details)
    return GateResult.pass_(name, f"all {len(harvest)} harvested items are mapped: {len(items)} lessons, "
                                  f"{details['merged']} merged into them", evidence=evidence,
                            engine=gates.engine(**ENGINE), details=details)


def landed(dest: dict, repo: Repo) -> bool:
    """Whether a pending destination's planned file (and symbol, when it names one) is in the checkout now."""
    rel = str(dest.get("file") or "")
    if not rel or not repo.exists(rel):
        return False
    symbol = str(dest.get("symbol") or "")
    return not symbol or repo.symbol(rel, symbol) is not None


def gate_gaps(data: dict, repo: Repo) -> GateResult:
    """No lesson is a gap, and none is still pending."""
    name = "knowledge.gaps"
    items = lessons(data)
    gaps = [x for x in items if (x.get("destination") or {}).get("type") == "gap"]
    pending = [x for x in items if (x.get("destination") or {}).get("type") == "pending"]
    by_module = Counter(str((x["destination"]).get("module") or "?").split(" ")[0] for x in gaps + pending)
    ready = [str(x.get("id")) for x in pending if landed(x["destination"], repo)]
    details = {"gaps": len(gaps), "pending": len(pending), "by_module": dict(by_module.most_common()),
               "ids": [x.get("id") for x in gaps], "pending_ids": [x.get("id") for x in pending],
               "pending_landed": ready}
    evidence = [LEDGER] + ([GAPS] if repo.exists(GAPS) else [])
    if gaps or pending:
        top = ", ".join(f"{m} {n}" for m, n in by_module.most_common(6))
        parts = []
        if gaps:
            parts.append(f"{len(gaps)} lesson(s) aren't reflected anywhere yet")
        if pending:
            parts.append(f"{len(pending)} still pending" + (f" ({len(ready)} of whose planned files have landed: "
                                                             "re-map them)" if ready else ""))
        return GateResult.fail(name, f"{'; '.join(parts)} ({top}); {GAPS} has the change that closes each",
                               evidence=evidence + [f"landed, re-map: {i}" for i in ready][:20],
                               engine=gates.engine(**ENGINE), details=details)
    return GateResult.pass_(name, "no gaps and nothing pending: every lesson lands somewhere real", evidence=evidence,
                            engine=gates.engine(**ENGINE), details=details)


def gate_destinations(data: dict, repo: Repo) -> GateResult:
    """Every destination resolves in the checkout, and every gate-type lesson has a gate (or is a declared gap)."""
    name = "knowledge.destinations"
    qa_names, qa_error = (None, None)
    if any(d.get("registry") == "slate.qa.GATES" for x in lessons(data) for d in destinations(x)):
        qa_names, qa_error = _qa_gates()
    problems: list[str] = []
    moved: dict[str, dict] = {}
    counts: Counter[str] = Counter()
    tests: set[str] = set()
    for x in lessons(data):
        lid = str(x.get("id"))
        dests = destinations(x)
        main = (x.get("destination") or {}).get("type")
        if x.get("gate_type") and main not in ("gate", "gap", "pending", "dropped"):
            problems.append(f"{lid}: a gate-type lesson whose destination is a {main}, not a gate")
        for i, d in enumerate(dests):
            res = resolve(d, repo, qa_names=qa_names, qa_error=qa_error)
            counts[str(d.get("type"))] += 1
            tests.update(str(t) for t in d.get("tests") or [])
            where = lid if i == 0 else f"{lid} (also #{i})"
            problems += [f"{where}: {p}" for p in res.problems]
            if res.moved_from:
                moved[where] = {"file": d.get("file"), "symbol": d.get("symbol"), "was": res.moved_from,
                                "now": res.line}
    details = {"checked": dict(counts), "tests_named": len(tests), "problems": problems, "moved": moved}
    if problems:
        return GateResult.fail(name, f"{len(problems)} destination problem(s): {problems[0]}",
                               evidence=[LEDGER] + problems[:40], engine=gates.engine(**ENGINE), details=details)
    note = f"; {len(moved)} symbol(s) moved since they were recorded (details.moved)" if moved else ""
    summary = (f"every destination resolves: {counts['code_default']} code defaults, {counts['gate']} gates "
               f"({len(tests)} tests named), {counts['skill_rule']} skill rules, {counts['dropped']} drops{note}")
    if counts["gap"] or counts["pending"]:
        summary += f" (and {counts['gap']} gaps, {counts['pending']} pending: see knowledge.gaps)"
    return GateResult.pass_(name, summary, evidence=[LEDGER], engine=gates.engine(**ENGINE), details=details)


def gate_type_tests(data: dict) -> dict[str, list[str]]:
    """{lesson id: its gate tests} for every gate-type lesson (its gate destinations' tests)."""
    out: dict[str, list[str]] = {}
    for x in lessons(data):
        if not x.get("gate_type"):
            continue
        ts = [str(t) for d in destinations(x) if d.get("type") == "gate" for t in d.get("tests") or []]
        if ts:
            out[str(x.get("id"))] = sorted(dict.fromkeys(ts))
    return out


def _module_dotted(rel: str) -> str:
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else rel


def parse_junit(xml_text: str) -> dict[str, str]:
    """{node id: passed|failed|error|skipped} from a pytest junit report written with junit_family=xunit1."""
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for case in root.iter("testcase"):
        rel = case.get("file") or ""
        classname, name = case.get("classname") or "", case.get("name") or ""
        mod = _module_dotted(rel)
        cls = classname[len(mod) + 1:] if rel and classname.startswith(mod + ".") else ""
        node = "::".join(p for p in (rel, cls, name) if p)
        outcome = "passed"
        for child in case:
            if child.tag in ("failure", "error", "skipped"):
                outcome = {"failure": "failed"}.get(child.tag, child.tag)
                break
        out[node] = outcome
    return out


def _outcome_for(node_id: str, results: dict[str, str]) -> str:
    """One requested id's outcome: a parametrized base id takes the worst of its runs."""
    hits = [v for k, v in results.items() if k == node_id or k.startswith(node_id + "[")]
    if not hits:
        return "missing"
    for worst in ("failed", "error", "skipped"):
        if worst in hits:
            return worst
    return "passed"


def run_pytest(node_ids: list[str], repo: Path, *, timeout: float = 3600.0) -> tuple[dict[str, str], dict]:
    """Run the given tests in one pytest batch and return ({node id: outcome}, run info)."""
    from slate import procs  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="slate-knowledge-") as tmp:
        report = Path(tmp) / "junit.xml"
        argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
                "-o", "junit_family=xunit1", f"--junitxml={report}", *node_ids]
        env = {"SLATE_FAKE_VENDORS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        proc = procs.run(argv, cwd=repo, env=env, timeout=timeout)
        text = report.read_text(encoding="utf-8") if report.is_file() else ""
    info = {"exit_code": proc.returncode, "tail": (proc.stdout or "").strip()[-600:], "requested": len(node_ids)}
    return parse_junit(text), info


def gate_tests(data: dict, repo: Repo, *, timeout: float = 3600.0) -> GateResult:
    """Every gate-type lesson's tests, run in one pytest batch: PASS only when every one of them passes."""
    name = "knowledge.tests"
    wanted = gate_type_tests(data)
    ids = sorted({t for ts in wanted.values() for t in ts})
    if not ids:
        return GateResult.unchecked(name, "no gate-type lesson names a test", evidence=[LEDGER],
                                    engine=gates.engine(**ENGINE))
    results, info = run_pytest(ids, repo.root, timeout=timeout)
    outcome = {t: _outcome_for(t, results) for t in ids}
    failing = {lid: [t for t in ts if outcome[t] in ("failed", "error")] for lid, ts in wanted.items()}
    failing = {k: v for k, v in failing.items() if v}
    unrun = {lid: [t for t in ts if outcome[t] in ("skipped", "missing")] for lid, ts in wanted.items()}
    unrun = {k: v for k, v in unrun.items() if v and k not in failing}
    details = {"lessons": len(wanted), "tests": len(ids), "outcomes": dict(Counter(outcome.values())),
               "failing": failing, "not_run": unrun, "pytest": info}
    eng = gates.engine("pytest (via slate knowledge)", __version__)
    if failing:
        first = next(iter(failing.items()))
        return GateResult.fail(name, f"{len(failing)} gate-type lesson(s) have failing tests (first: {first[0]}: "
                                     f"{first[1][0]})", evidence=[f"{k}: {', '.join(v)}" for k, v in failing.items()][:30],
                               engine=eng, details=details)
    if unrun:
        return GateResult.unchecked(name, f"{len(unrun)} gate-type lesson(s) have tests that were skipped or didn't "
                                          "run, so they aren't proved", engine=eng, details=details,
                                    evidence=[f"{k}: {', '.join(v)}" for k, v in unrun.items()][:30])
    return GateResult.pass_(name, f"all {len(ids)} tests behind {len(wanted)} gate-type lessons pass (one pytest "
                                  "batch)", evidence=[LEDGER], engine=eng, details=details)


def gate_components(repo: Repo) -> GateResult:
    """Every component the inventory found lands on a real module path, or on one of the two places outside the core."""
    name = "knowledge.components"
    p = repo.path(COMPONENTS)
    if not p.is_file():
        return GateResult.unchecked(name, f"no {COMPONENTS} in this checkout", engine=gates.engine(**ENGINE))
    doc = jsonio.read(p) or {}
    outside = set((doc.get("outside_the_core") or {}).keys())
    statuses = set((doc.get("statuses") or {}).keys()) or {"ported", "generalized", "dropped"}
    categories = set((doc.get("categories") or {}).keys())
    comps = [c for c in doc.get("components") or [] if isinstance(c, dict)]
    problems: list[str] = []
    ids = Counter(str(c.get("id")) for c in comps)
    problems += [f"{i}: listed {n} times" for i, n in ids.items() if n > 1]
    by_module: Counter[str] = Counter()
    for c in comps:
        dest = c.get("destination") or {}
        module = str(dest.get("module") or "")
        by_module[module] += 1
        if not module:
            problems.append(f"{c.get('id')}: no destination module")
        elif module not in outside and not repo.exists(module):
            problems.append(f"{c.get('id')}: {module} isn't in the repo")
        if dest.get("status") not in statuses:
            problems.append(f"{c.get('id')}: status {dest.get('status')!r}")
        if categories and c.get("category") not in categories:
            problems.append(f"{c.get('id')}: category {c.get('category')!r}")
        if not str(dest.get("reason") or "").strip():
            problems.append(f"{c.get('id')}: no reason")
    declared = (doc.get("counts") or {}).get("total")
    if declared is not None and declared != len(comps):
        problems.append(f"counts.total says {declared}, the file lists {len(comps)}")
    details = {"components": len(comps), "modules": len(by_module), "problems": problems}
    if problems:
        return GateResult.fail(name, f"{len(problems)} component destination problem(s): {problems[0]}",
                               evidence=[COMPONENTS] + problems[:30], engine=gates.engine(**ENGINE), details=details)
    return GateResult.pass_(name, f"all {len(comps)} components land on real module paths ({len(by_module)} "
                                  "modules, including the two outside the core)", evidence=[COMPONENTS],
                            engine=gates.engine(**ENGINE), details=details)


def _provenance(rel: str) -> str | None:
    for prefix, why in PROVENANCE.items():
        if rel == prefix or rel.startswith(prefix):
            return why
    return None


def _guarded(words: list[str]) -> list[int]:
    """Indexes of guarded words (or of the first word of a guarded pair) in a list of lowercase words."""
    hits = []
    for i, w in enumerate(words):
        if len(w) in _GUARDED_LENGTHS and _h(w) in GUARDED_WORDS:
            hits.append(i)
        elif len(w) == 4 and i + 1 < len(words) and len(words[i + 1]) == 5 \
                and _h(f"{w} {words[i + 1]}") in GUARDED_PAIRS:
            hits.append(i)
    return hits


def _any_guarded(words: list[str]) -> bool:
    """A fast first pass over a whole file: each distinct word is hashed once."""
    if any(_h(w) in GUARDED_WORDS for w in set(words) if len(w) in _GUARDED_LENGTHS):
        return True
    pairs = {f"{a} {b}" for a, b in zip(words, words[1:]) if len(a) == 4 and len(b) == 5}
    return any(_h(p) in GUARDED_PAIRS for p in pairs)


def _guard_line(line: str) -> bool:
    """A test's guard pattern: a line that names two or more guarded words to keep them out, one that only builds the
    two-word name from common words, or the path constant golden-data tests read the film project through."""
    words = _WORD.findall(line.lower())
    hits = _guarded(words)
    singles = {words[i] for i in hits if len(words[i]) in _GUARDED_LENGTHS and _h(words[i]) in GUARDED_WORDS}
    return len(singles) >= 2 or (bool(hits) and not singles) or bool(re.match(r"\s*FILMROOT\s*=", line))


def _walk(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            yield Path(dirpath) / fn


def ledger_text_hits(repo: Repo) -> list[str]:
    """Guarded names in the ledger's own words (texts, notes, reasons, changes), which its provenance doesn't excuse."""
    text = repo.text(LEDGER)
    if not text or not _any_guarded(_WORD.findall(text.lower())):
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for x in lessons(data if isinstance(data, dict) else {}):
        fields = [("text", x.get("text"))]
        fields += [(k, d.get(k)) for d in destinations(x) for k in LEDGER_TEXT_FIELDS if k != "text"]
        for key, value in fields:
            if value and _guarded(_WORD.findall(str(value).lower())):
                out.append(f"{LEDGER}: {x.get('id')}'s {key}: a guarded name")
                break
    return out


def film_project(repo: Repo) -> Path | None:
    """The film project Slate was harvested from, as knowledge/components.json records it, when it's on this Mac."""
    doc = jsonio.read(repo.path(COMPONENTS), default=None) if repo.exists(COMPONENTS) else None
    root = ((doc or {}).get("source") or {}).get("filmroot") if isinstance(doc, dict) else None
    p = Path(root) if root else None
    return p if p and p.is_dir() else None


def gate_left_behind(repo: Repo, *, film_root: Path | None | bool = True) -> GateResult:
    """Nothing the plan left behind is in the repo: the names, the files, or copies of them under other names."""
    name = "knowledge.left_behind"
    hits: list[str] = []
    allowed: Counter[str] = Counter()
    hashes: dict[str, str] = {}
    scanned = 0
    for p in _walk(repo.root):
        rel = p.relative_to(repo.root).as_posix()
        why = _provenance(rel)
        base = p.name
        if base in LEFT_BEHIND_NAMES:
            hits.append(f"{rel}: {LEFT_BEHIND_NAMES[base]}")
        for frag, what in LEFT_BEHIND_PATHS.items():
            if frag in rel + ("/" if p.is_dir() else ""):
                hits.append(f"{rel}: {what}")
        path_words = _WORD.findall(rel.lower())
        if why is None and _guarded(path_words):
            hits.append(f"{rel}: a guarded name in the path")
        if p.suffix.lower() not in TEXT_EXT:
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) >= 200 and len(data) <= 2_000_000:
            hashes.setdefault(hashlib.sha256(data).hexdigest(), rel)
        if why is not None:
            allowed[why] += 1
            continue
        scanned += 1
        text = data.decode("utf-8", errors="replace")
        if not _any_guarded(_WORD.findall(text.lower())):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            words = _WORD.findall(line.lower())
            if _guarded(words):
                if rel.startswith("tests/") and _guard_line(line):
                    allowed["a test's guard pattern"] += 1
                    continue
                hits.append(f"{rel}:{n}: a guarded name")
    hits += ledger_text_hits(repo)
    copies: list[str] = []
    fr = film_project(repo) if film_root is True else (film_root or None)
    compared = 0
    if fr:
        for sub in LEFT_BEHIND_ROOTS:
            base = fr / sub
            files = [base] if base.is_file() else (list(_walk(base)) if base.is_dir() else [])
            for f in files:
                try:
                    if f.suffix.lower() not in TEXT_EXT or not 200 <= f.stat().st_size <= 2_000_000:
                        continue
                    digest = hashlib.sha256(f.read_bytes()).hexdigest()
                except OSError:
                    continue
                compared += 1
                if digest in hashes:
                    copies.append(f"{hashes[digest]}: a copy of the film project's {f.relative_to(fr).as_posix()}")
    hits += copies
    details = {"files_scanned": scanned, "provenance_files": dict(allowed), "film_project_files_compared": compared,
               "film_project": "on this Mac" if fr else "not here: the content comparison was skipped",
               "hits": hits}
    evidence = [f"{scanned} repo files scanned for guarded names and left-behind paths"]
    evidence.append(f"{compared} left-behind files in the film project compared by content" if fr else
                    "the film project isn't on this Mac, so copies under other names couldn't be looked for")
    if hits:
        return GateResult.fail(name, f"{len(hits)} thing(s) the plan left behind are in the repo: {hits[0]}",
                               evidence=evidence + hits[:30], engine=gates.engine(**ENGINE), details=details)
    summary = ("nothing the plan left behind is here: no guarded names, none of the films' scenes, the render-farm "
               "script, the retired v2 sound design, the rejected 3D pass or the website tooling")
    if fr:
        summary += f", and no copies of {compared} left-behind files"
    return GateResult.pass_(name, summary, evidence=evidence, engine=gates.engine(**ENGINE), details=details)


# ------------------------------------------------------------------------------------------------------ the check
def check(*, repo: Path | None = None, ledger: dict | None = None, run_tests: bool = False, scan: bool = True,
          test_timeout: float = 3600.0) -> list[GateResult]:
    """Prove the ledger against the checkout. Returns one GateResult per part (PASS only when all of them pass)."""
    root = Path(repo or paths.REPO)
    r = Repo(root)
    data = ledger if ledger is not None else load(root)
    results = [gate_mapped(data, r), gate_gaps(data, r), gate_destinations(data, r)]
    if run_tests:
        results.append(gate_tests(data, r, timeout=test_timeout))
    if r.exists(COMPONENTS):
        results.append(gate_components(r))
    if scan:
        results.append(gate_left_behind(r))
    return results


def stats(data: dict) -> dict:
    """Counts: lessons, merges, destinations by type, areas, kinds, gate-type lessons and gaps by module."""
    items = lessons(data)
    by_type = Counter(str((x.get("destination") or {}).get("type")) for x in items)
    gaps = Counter(str((x.get("destination") or {}).get("module") or "?").split(" ")[0] for x in items
                   if (x.get("destination") or {}).get("type") in OPEN_TYPES)
    return {
        "lessons": len(items),
        "harvested": len(items) + sum(len(x.get("absorbed") or []) for x in items),
        "merged": sum(len(x.get("absorbed") or []) for x in items),
        "lessons_with_merges": sum(1 for x in items if x.get("absorbed")),
        "by_type": {t: by_type.get(t, 0) for t in TYPES},
        "by_area": dict(Counter(str(x.get("area")) for x in items).most_common()),
        "by_kind": dict(Counter(str(x.get("kind")) for x in items).most_common()),
        "gate_type": sum(1 for x in items if x.get("gate_type")),
        "gate_tests": len({t for ts in gate_type_tests(data).values() for t in ts}),
        "also": sum(len(x.get("also") or []) for x in items),
        "gaps_by_module": dict(gaps.most_common()),
        "harvests": (data.get("meta") or {}).get("harvests"),
        "next_harvest": (data.get("meta") or {}).get("next_harvest"),
    }


# ---------------------------------------------------------------------------------------------------------- CLI
def _dest_line(d: dict) -> str:
    kind = d.get("type")
    if kind == "code_default":
        extra = f" (holds: {'; '.join(d['terms'])})" if d.get("terms") else ""
        return f"code default: {d.get('file')}:{d.get('line')} {d.get('symbol')}{extra}"
    if kind == "gate":
        check_name = f":{d['check']}" if d.get("check") else ""
        how = " (by construction)" if d.get("enforced") == "construction" else ""
        return (f"gate {d.get('gate')}{check_name}{how}: {d.get('file')}:{d.get('line')} {d.get('symbol')}; tests: "
                + ", ".join(d.get("tests") or []))
    if kind == "skill_rule":
        return f"skill rule: {d.get('target')}" + (f":{d['line']}" if d.get("line") else "") + \
            f" (states: {'; '.join(d.get('terms') or [])})"
    if kind == "dropped":
        return f"dropped: {d.get('reason')}"
    if kind == "gap":
        return f"gap in {d.get('module')}: {d.get('change')}"
    if kind == "pending":
        where = f"{d.get('file')}" + (f"::{d['symbol']}" if d.get("symbol") else "")
        return f"pending in {d.get('module')} ({where}): {d.get('what')}"
    return f"{kind}: {d}"


def _cmd_check(args: argparse.Namespace) -> int:
    from slate import cli  # noqa: PLC0415

    data = load()
    if cli.flag(args, "run_tests", False):
        log.info("knowledge: running every gate-type lesson's tests in one pytest batch (this takes a while)")
    results = check(ledger=data, run_tests=bool(cli.flag(args, "run_tests", False)))
    st = stats(data)
    summary = gates.summarize(results)
    counts = st["by_type"]
    head = (f"knowledge ledger: {st['lessons']} lessons ({st['harvested']} harvested, {st['merged']} merged) · "
            f"{counts['code_default']} code defaults, {counts['gate']} gates, {counts['skill_rule']} skill rules, "
            f"{counts['dropped']} dropped, {counts['gap']} gaps, {counts['pending']} pending")
    human = head + "\n" + gates.format_table(results)
    if not cli.flag(args, "run_tests", False):
        human += "\n(the gate-type lessons' tests weren't run: add --run-tests to run them in one pytest batch)"
    log.result({"status": summary["status"], "counts": st, "gates": [r.to_dict() for r in results],
                "tests_run": bool(cli.flag(args, "run_tests", False))}, human)
    return gates.exit_code(results)


def _cmd_stats(args: argparse.Namespace) -> int:
    data = load()
    st = stats(data)
    lines = [f"{st['lessons']} lessons from {st['harvested']} harvested items ({st['merged']} merged into "
             f"{st['lessons_with_merges']} lessons)"]
    lines.append("by destination: " + ", ".join(f"{k} {v}" for k, v in st["by_type"].items()))
    lines.append(f"gate-type lessons: {st['gate_type']} ({st['gate_tests']} tests named)")
    lines.append("by area: " + ", ".join(f"{k} {v}" for k, v in st["by_area"].items()))
    if st["gaps_by_module"]:
        lines.append("gaps by module: " + ", ".join(f"{k} {v}" for k, v in st["gaps_by_module"].items()))
    for h in st.get("harvests") or []:
        lines.append(f"harvest {h.get('date')}: {h.get('items')} items from {len(h.get('sources') or [])} sources")
    if st.get("next_harvest"):
        lines.append(f"next harvest: {st['next_harvest']}")
    log.result(st, "\n".join(lines))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    data = load()
    lesson = find(data, args.id)
    if lesson is None:
        raise UsageError(f"No lesson {args.id!r} in {LEDGER}.", hint="`slate knowledge stats` shows what's there.")
    repo = Repo(paths.REPO)
    qa_names, qa_error = (None, None)
    if any(d.get("registry") == "slate.qa.GATES" for d in destinations(lesson)):
        qa_names, qa_error = _qa_gates()
    checked = []
    for d in destinations(lesson):
        res = resolve(d, repo, qa_names=qa_names, qa_error=qa_error)
        checked.append({"destination": d, "resolves": res.ok, "problems": res.problems, "line_now": res.line})
    lines = [f"{lesson.get('id')} [{lesson.get('kind')}, {lesson.get('area')}"
             f"{', gate-type' if lesson.get('gate_type') else ''}]", lesson.get("text", "")]
    if args.id != lesson.get("id"):
        lines.append(f"({args.id} was merged into this lesson)")
    if lesson.get("absorbed"):
        lines.append("absorbed: " + ", ".join(lesson["absorbed"]))
    lines.append("sources: " + "; ".join(lesson.get("sources") or []))
    for i, c in enumerate(checked):
        label = "→ " if i == 0 else "  also "
        state = "resolves" if c["resolves"] else "DOESN'T RESOLVE: " + "; ".join(c["problems"])
        lines.append(f"{label}{_dest_line(c['destination'])}  [{state}]")
        if c["destination"].get("note"):
            lines.append(f"    {c['destination']['note']}")
    log.result({"lesson": lesson, "checked": checked}, "\n".join(lines))
    return 0 if all(c["resolves"] for c in checked) else 1


def register(sub) -> None:
    from slate import cli  # noqa: PLC0415

    group = cli.add_group(sub, "knowledge", help="the knowledge ledger: every lesson from the films, mapped and checked",
                          description="The knowledge ledger (knowledge/lessons.json): every lesson and rule harvested "
                                      "from the films, each with one destination (a code default, a gate with its "
                                      "tests, a skill rule, dropped with a reason, a gap with the change that "
                                      "closes it, or pending while it's being built).")
    p = cli.add_command(group, "check", _cmd_check,
                        help="prove every lesson's destination against this checkout; PASS only when nothing is "
                             "unmapped, a gap or pending",
                        epilog="Exit 0 when every part passes, 1 otherwise, 3 when there's no ledger. Parts: "
                               "knowledge.mapped, .gaps, .destinations, .tests (--run-tests), .components, "
                               ".left_behind.")
    p.add_argument("--run-tests", dest="run_tests", action="store_true",
                   help="also run every gate-type lesson's tests, in one pytest batch")
    cli.add_command(group, "stats", _cmd_stats, help="counts by destination, area and kind; gaps by module")
    p = cli.add_command(group, "show", _cmd_show, help="one lesson (by its id or an id it absorbed), its sources and "
                                                        "destination, checked against the checkout now")
    p.add_argument("id", help="a lesson id such as L-motion-D001")
