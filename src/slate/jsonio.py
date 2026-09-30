"""JSON and hashing helpers shared by every module.

Files are written atomically (temp file, fsync, rename) so a crash or a killed agent never leaves half a film.json.
Hashes are SHA-256 of file contents; they are what locks approved work and turns gates STALE.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from slate.errors import UsageError
from slate.paths import SCHEMAS_DIR

GENERATED_KEY = "generated"


def read(path: str | os.PathLike, default: Any = ...) -> Any:
    """Load a JSON file. Without a default, a missing file is a UsageError that names the path."""
    p = Path(path)
    if not p.exists():
        if default is ...:
            raise UsageError(f"Missing file: {p}")
        return default
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise UsageError(f"{p} isn't valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}") from exc


def dumps(obj: Any, indent: int | None = 1) -> str:
    return json.dumps(obj, indent=indent, ensure_ascii=False, default=str) + "\n"


def write_atomic(path: str | os.PathLike, obj: Any, indent: int | None = 1) -> Path:
    """Write JSON so readers only ever see the old file or the complete new one."""
    return write_text_atomic(path, dumps(obj, indent))


def write_text_atomic(path: str | os.PathLike, text: str | bytes) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8") if isinstance(text, str) else text
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if p.exists():
            os.chmod(tmp, p.stat().st_mode & 0o777)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


def mark_generated(obj: dict, command: str) -> dict:
    """Stamp a generated file so nobody edits it by hand: {"generated": "do not edit by hand: <command>"}."""
    return {GENERATED_KEY: f"do not edit by hand: {command}", **{k: v for k, v in obj.items() if k != GENERATED_KEY}}


def sha256(path: str | os.PathLike, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def sha256_obj(obj: Any) -> str:
    """A stable hash of a JSON-able value (sorted keys), for request keys and content ids."""
    return sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str))


def hash_inputs(paths: list[str | os.PathLike], root: str | os.PathLike | None = None) -> dict[str, str | None]:
    """{relative path: sha256} for gate evidence and lock records. A missing file maps to None."""
    base = Path(root) if root else None
    out: dict[str, str | None] = {}
    for p in paths:
        path = Path(p)
        full = base / path if base and not path.is_absolute() else path
        key = str(path) if not base else (str(full.relative_to(base)) if full.is_relative_to(base) else str(full))
        out[key] = sha256(full) if full.is_file() else None
    return out


@lru_cache(maxsize=None)
def schema(name: str) -> dict:
    """Load src/slate/schemas/<name>.schema.json."""
    path = SCHEMAS_DIR / f"{name}.schema.json"
    if not path.exists():
        raise UsageError(f"No schema called {name!r} (looked for {path}).")
    return json.loads(path.read_text(encoding="utf-8"))


def validate(obj: Any, schema_name: str) -> list[str]:
    """Validate against a named schema. Returns a list of readable problems (empty when valid)."""
    try:
        import jsonschema  # noqa: PLC0415 (optional import keeps `slate --help` fast)
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise UsageError("jsonschema isn't installed in Slate's environment; run install.sh again.") from exc
    validator_cls = jsonschema.validators.validator_for(schema(schema_name))
    validator = validator_cls(schema(schema_name))
    problems = []
    for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(x) for x in err.absolute_path) or "(top level)"
        problems.append(f"{where}: {err.message}")
    return problems


def require_valid(obj: Any, schema_name: str, what: str = "") -> None:
    problems = validate(obj, schema_name)
    if problems:
        label = what or schema_name
        listed = "\n  ".join(problems[:20])
        more = f"\n  …and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise UsageError(f"{label} doesn't match schema {schema_name}:\n  {listed}{more}")
