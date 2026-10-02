"""The eval suite on disk: `evals/configs.json`, `evals/cases/*.json` and `evals/fixtures/`.

Loading validates everything twice: against the JSON schemas in `slate/evals/schemas/`, then semantically (every check
is a known grader with its parameters, every regex compiles, every configuration a case names exists, a pending case
carries a precondition, a strategist case runs on a harness). A broken case file is a usage error that names the file
and the problem, never a silent skip.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from slate import paths
from slate.errors import UsageError

PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = PACKAGE_DIR / "schemas"
PROBE_DIR = PACKAGE_DIR / "probe"
CONFIG_IDS = ("claude-code", "codex", "pursuit-slice", "openclaw")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def evals_dir() -> Path:
    """The suite's folder: `evals/` in the repo, or `SLATE_EVALS_DIR` (tests point it at a temporary copy)."""
    env = os.environ.get("SLATE_EVALS_DIR")
    return Path(env).expanduser() if env else paths.REPO / "evals"


@dataclass
class Case:
    id: str
    title: str
    group: str
    status: str
    prompt: str
    configs: list[str]
    expect: list[dict]
    vars: dict = field(default_factory=dict)
    runner: str = "agent"
    needs_eval_env: bool = False
    timeout_s: int | None = None
    cut_off: bool = False
    block: list[str] = field(default_factory=list)
    setup: dict = field(default_factory=dict)
    precondition: dict | None = None
    harness: dict = field(default_factory=dict)
    plan_points: list[str] = field(default_factory=list)
    why: str = ""
    notes: str = ""
    path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, path: Path | None = None) -> "Case":
        return cls(
            id=data["id"], title=data["title"], group=data["group"], status=data["status"], prompt=data["prompt"],
            configs=list(data["configs"]), expect=[dict(e) for e in data["expect"]], vars=dict(data.get("vars") or {}),
            runner=data.get("runner", "agent"), needs_eval_env=bool(data.get("needs_eval_env", False)),
            timeout_s=data.get("timeout_s"), cut_off=bool(data.get("cut_off", False)),
            block=list(data.get("block") or []), setup=dict(data.get("setup") or {}),
            precondition=data.get("precondition"), harness=dict(data.get("harness") or {}),
            plan_points=list(data.get("plan_points") or []), why=data.get("why", ""), notes=data.get("notes", ""),
            path=path,
        )

    @property
    def pending(self) -> bool:
        return self.status == "pending"


@lru_cache(maxsize=None)
def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def schema_problems(obj: Any, name: str) -> list[str]:
    """Readable problems from validating `obj` against one of the eval schemas."""
    import jsonschema  # noqa: PLC0415 (keeps `slate --help` fast)

    schema = _schema(name)
    validator = jsonschema.validators.validator_for(schema)(schema)
    out = []
    for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(x) for x in err.absolute_path) or "(top level)"
        out.append(f"{where}: {err.message}")
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UsageError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UsageError(f"{path} isn't valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}") from exc


# ---- configurations ------------------------------------------------------------------------------------------------------

def validate_configs(obj: Any) -> list[str]:
    from slate.evals.checks import CHECKS  # noqa: PLC0415

    problems = schema_problems(obj, "configs")
    if problems:
        return problems
    for name in obj.get("global_checks", []):
        if name not in CHECKS:
            problems.append(f"global_checks: unknown check {name!r}")
    for section in ("configurations", "harnesses"):
        for cid, cfg in (obj.get(section) or {}).items():
            for name in cfg.get("extra_checks", []) + list((cfg.get("unavailable_checks") or {})):
                if name not in CHECKS:
                    problems.append(f"{section}/{cid}: unknown check {name!r}")
            if cfg["transcript"]["source"] == "collect" and not cfg.get("collect"):
                problems.append(f"{section}/{cid}: transcript comes from collect steps, but there are none")
            if cfg["stdin"] == "prompt" and cfg["argv"][-1] != "-":
                problems.append(f"{section}/{cid}: a prompt on stdin needs '-' as the last argument")
            if cfg["stdin"] != "prompt" and not any("{prompt}" in a for a in cfg["argv"]):
                problems.append(f"{section}/{cid}: the prompt has to go somewhere: argv has no {{prompt}}")
    missing = [c for c in CONFIG_IDS if c not in (obj.get("configurations") or {})]
    if missing:
        problems.append(f"configurations: missing {', '.join(missing)}")
    return problems


def load_configs(root: Path | None = None) -> dict:
    """evals/configs.json, validated."""
    path = Path(root or evals_dir()) / "configs.json"
    obj = _read_json(path)
    problems = validate_configs(obj)
    if problems:
        listed = "\n  ".join(problems[:20])
        raise UsageError(f"{path} has problems:\n  {listed}")
    return obj


def config_entry(configs: dict, config_id: str) -> dict:
    """A configuration or a harness, by id."""
    for section in ("configurations", "harnesses"):
        entry = (configs.get(section) or {}).get(config_id)
        if entry is not None:
            return entry
    raise UsageError(f"No eval configuration called {config_id!r}.",
                     hint=f"Known: {', '.join(all_config_ids(configs))}.")


def all_config_ids(configs: dict) -> list[str]:
    return list(configs.get("configurations") or {}) + list(configs.get("harnesses") or {})


# ---- cases ---------------------------------------------------------------------------------------------------------------

def validate_case(obj: Any, configs: dict) -> list[str]:
    """Schema and semantic problems with one case (empty when it's good)."""
    from slate.evals import checks, preconditions  # noqa: PLC0415

    problems = schema_problems(obj, "case")
    if problems:
        return problems
    runner = obj.get("runner", "agent")
    agent_ids = set(configs.get("configurations") or {})
    harness_ids = set(configs.get("harnesses") or {})
    for cid in obj["configs"]:
        if runner == "agent" and cid not in agent_ids:
            problems.append(f"configs: {cid!r} isn't one of the agent configurations ({', '.join(sorted(agent_ids))})")
        if runner == "strategist" and cid not in harness_ids:
            problems.append(f"configs: a strategist case runs on a harness, and {cid!r} isn't one")
    for i, spec in enumerate(obj["expect"]):
        problems += [f"expect/{i}: {p}" for p in checks.validate_spec(spec)]
    if obj["status"] == "pending" and not obj.get("precondition"):
        problems.append("a pending case needs a precondition that says when it becomes runnable")
    if obj.get("precondition"):
        problems += [f"precondition: {p}" for p in preconditions.validate(obj["precondition"])]
    names = set(_PLACEHOLDER_RE.findall(obj["prompt"]))
    unknown = names - set(obj.get("vars") or {}) - {"workdir", "home"}
    if unknown:
        problems.append(f"prompt: no value for {', '.join(sorted(unknown))} (add it to vars)")
    for block in obj.get("block") or []:
        for cid in obj["configs"]:
            entry = (configs.get("configurations") or {}).get(cid) or (configs.get("harnesses") or {}).get(cid) or {}
            if block not in (entry.get("blocks") or {}):
                problems.append(f"block: {cid} has no way to block {block!r} (add it under blocks in configs.json, "
                                "with a note if it can't be blocked there)")
    return problems


def load_cases(root: Path | None = None, configs: dict | None = None) -> list[Case]:
    """Every case in evals/cases/, validated, sorted by group then id. Duplicate ids are an error."""
    base = Path(root or evals_dir())
    configs = configs if configs is not None else load_configs(base)
    cases: list[Case] = []
    problems: list[str] = []
    for path in sorted((base / "cases").glob("*.json")):
        obj = _read_json(path)
        found = validate_case(obj, configs)
        if found:
            problems += [f"{path.name}: {p}" for p in found]
            continue
        if obj["id"] != path.stem:
            problems.append(f"{path.name}: the file name must match its id ({obj['id']!r})")
            continue
        cases.append(Case.from_dict(obj, path))
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate case ids: {', '.join(dupes)}")
    if problems:
        listed = "\n  ".join(problems[:30])
        raise UsageError(f"The eval cases have problems:\n  {listed}")
    order = {"triggering": 0, "routing": 1, "negative": 2, "dry-run": 3, "oneiro": 4}
    return sorted(cases, key=lambda c: (order.get(c.group, 9), c.id))


def select(cases: list[Case], *, ids: list[str] | None = None, groups: list[str] | None = None,
           configs: list[str] | None = None) -> list[tuple[Case, str]]:
    """The (case, configuration) pairs a run covers. Unknown ids are a usage error, so a typo never runs nothing."""
    known = {c.id for c in cases}
    for cid in ids or []:
        if cid not in known:
            raise UsageError(f"No eval case called {cid!r}.", hint="`slate evals list` shows them.")
    pairs = []
    for case in cases:
        if ids and case.id not in ids:
            continue
        if groups and case.group not in groups:
            continue
        for cfg in case.configs:
            if configs and cfg not in configs:
                continue
            pairs.append((case, cfg))
    return pairs


# ---- fixtures ------------------------------------------------------------------------------------------------------------

def fixtures_dir(kind: str, root: Path | None = None) -> Path:
    if kind not in ("recorded", "synthetic"):
        raise UsageError(f"Fixtures are 'recorded' or 'synthetic', not {kind!r}.")
    return Path(root or evals_dir()) / "fixtures" / kind


def recorded_fixture(case_id: str, config_id: str, root: Path | None = None) -> Path:
    return fixtures_dir("recorded", root) / case_id / config_id


def synthetic_fixtures(root: Path | None = None) -> list[tuple[Path, dict]]:
    """Every synthetic fixture folder with its meta.json (validated)."""
    out = []
    problems = []
    for folder in sorted(fixtures_dir("synthetic", root).iterdir() if fixtures_dir("synthetic", root).exists() else []):
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            problems.append(f"{folder.name}: no meta.json")
            continue
        meta = _read_json(meta_path)
        found = schema_problems(meta, "fixture_meta")
        if not meta.get("synthetic"):
            found.append("synthetic fixtures must say synthetic: true")
        if "expect" not in meta:
            found.append("synthetic fixtures must say what the graders should conclude (expect)")
        if found:
            problems += [f"{folder.name}: {p}" for p in found]
            continue
        out.append((folder, meta))
    if problems:
        listed = "\n  ".join(problems[:20])
        raise UsageError(f"The synthetic fixtures have problems:\n  {listed}")
    return out


def expand(template: str, values: dict[str, Any]) -> str:
    """Fill {name} placeholders. An unknown placeholder is an error, so a typo never reaches an agent."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise UsageError(f"No value for {{{key}}} in {template!r}.")
        value = values[key]
        return " ".join(value) if isinstance(value, list) else str(value)

    return _PLACEHOLDER_RE.sub(sub, template)
