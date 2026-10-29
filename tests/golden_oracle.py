"""Capture and verify a private, portable copy of delivered golden inputs.

This freezes bytes, not a passing verdict. Capture from a coherent delivery, then run the unchanged parity tests.
Private film data stays outside this repository. A snapshot never shares writable hard links with its source.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

MANIFEST = ".slate-golden.json"
REPO = Path(__file__).resolve().parents[1]


class OracleError(ValueError):
    """An oracle is absent, unsafe, incomplete or changed."""


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise OracleError("Oracle file names must be nonempty relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..") for part in value.split("/")) or str(path) != value:
        raise OracleError(f"Unsafe oracle path: {value!r}")
    if path.parts[0] in (".git", MANIFEST):
        raise OracleError(f"Reserved oracle path: {value!r}")
    return value


def _file(root: Path, relative: str) -> Path:
    relative = _relative(relative)
    path = root / relative
    component = root
    for part in PurePosixPath(relative).parts:
        component /= part
        if component.is_symlink():
            raise OracleError(f"Oracle files cannot use symlinks: {relative}")
    if not path.is_file():
        raise OracleError(f"Oracle file is missing: {relative}")
    return path


def _sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _stamp(path: Path) -> tuple[int, int, int, int]:
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def validate(root: Path) -> dict:
    """Validate the entire snapshot, including unexpected files. A bad explicit oracle is an error, never a skip."""
    root = Path(root).resolve()
    manifest_path = root / MANIFEST
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise OracleError(f"No frozen oracle manifest in {root}; capture one with tests/golden_oracle.py")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (ValueError, OSError) as exc:
        raise OracleError(f"Cannot read oracle manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise OracleError("Unsupported oracle manifest schema")
    if not isinstance(manifest.get("label"), str) or not manifest["label"].strip():
        raise OracleError("Oracle manifest must name its delivered revision")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise OracleError("An oracle must list its inputs and expected results")
    for relative, record in files.items():
        path = _file(root, relative)
        if not isinstance(record, dict) or record.get("size") != path.stat().st_size or record.get("sha256") != _sha(path):
            raise OracleError(f"Frozen oracle changed: {relative}; restore its original bytes, never refresh an expectation to pass")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise OracleError(f"Oracle cannot contain a symlink: {path.relative_to(root)}")
        if path.is_file() and path != manifest_path:
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files):
        raise OracleError(f"Oracle has unlisted files: {', '.join(sorted(actual - set(files)))}")
    return manifest


@contextmanager
def verified(root: Path):
    """Hold a selected snapshot for a test session and detect mutation before declaring that session complete."""
    root = Path(root).expanduser().resolve()
    validate(root)
    try:
        yield root
    finally:
        validate(root)


def capture(source: Path, output: Path, files: list[str], *, label: str) -> dict:
    """Copy an explicit file set atomically. Refuse replacement, links and a source that changes during capture."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if not label.strip():
        raise OracleError("Name the delivery or source revision being captured")
    if output == source or output.is_relative_to(source) or output == REPO or output.is_relative_to(REPO):
        raise OracleError("Keep private oracle snapshots outside both the source film and the Slate repository")
    if output.exists():
        raise OracleError("Oracle destination exists; snapshots are never replaced")
    names = [_relative(name) for name in files]
    if not names or len(set(names)) != len(names):
        raise OracleError("List each oracle input or expected result exactly once")
    sources = {name: _file(source, name) for name in names}
    before = {name: _stamp(path) for name, path in sources.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        records = {}
        for name, path in sources.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            digest = _sha(target)
            if _stamp(path) != before[name] or _sha(path) != digest:
                raise OracleError(f"Source changed while capturing: {name}")
            records[name] = {"sha256": digest, "size": target.stat().st_size}
            target.chmod(target.stat().st_mode & ~0o222)
        # Catch a change to an earlier file while a later (possibly large) file was being copied.
        for name, path in sources.items():
            if _stamp(path) != before[name] or _sha(path) != records[name]["sha256"]:
                raise OracleError(f"Source changed while capturing: {name}")
        manifest = {"schema_version": 1, "label": label, "source": str(source), "files": records}
        (stage / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n")
        (stage / MANIFEST).chmod(0o444)
        validate(stage)
        # Reserve the destination so a concurrently created snapshot is never overwritten.
        output.mkdir()
        try:
            os.rename(stage, output)
        except BaseException:
            output.rmdir()
            raise
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("capture", help="freeze a coherent delivery's explicit file set")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--files", type=Path, required=True, help="JSON array of paths relative to source")
    create.add_argument("--label", required=True, help="delivery version or source revision")
    check = commands.add_parser("verify", help="verify every frozen byte")
    check.add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            files = json.loads(args.files.read_text())
            if not isinstance(files, list):
                raise OracleError("--files must contain a JSON array of relative file paths")
            result = capture(args.source, args.output, files, label=args.label)
        else:
            result = validate(args.root)
    except (OracleError, OSError, ValueError) as exc:
        parser.exit(1, f"Oracle refused: {exc}\n")
    print(json.dumps({"label": result["label"], "files": len(result["files"]), "verified": True,
                      "parity": "not measured; run the golden tests"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
