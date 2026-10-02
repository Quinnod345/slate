"""Slate's desktop extension for the Claude app: `mcpb/` in the checkout, packed by `slate mcpb build`.

The extension carries no Slate code of its own. Its launcher (`mcpb/server/slate-mcp`) starts the `slate mcp` that's
installed on this Mac, so a chat in the desktop app and the command line always run the same Slate, with the same
films, queue, ledger and keys. That's what lets a regular chat plan, write and voice a film and start renders as
background jobs: every long step goes to the job queue on this Mac.

`mcpb/manifest.json` is written by hand except for two fields the build keeps honest: `version` (Slate's) and `tools`
(the server's own list). `slate mcpb build --check` fails when either has drifted; `--sync` rewrites them.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path

from slate import __version__, jsonio, paths
from slate.errors import MissingPrerequisite, Refused

SERVER_TYPES = ("node", "python", "binary", "uv")
REQUIRED = ("manifest_version", "name", "version", "description", "author", "server")
MANIFEST_VERSION = "0.3"
EXECUTABLE = ("server/slate-mcp",)
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SKIP = {".DS_Store"}


def source_dir() -> Path:
    return paths.REPO / "mcpb"


def manifest_path(src: Path | None = None) -> Path:
    return (src or source_dir()) / "manifest.json"


def load(src: Path | None = None) -> dict:
    path = manifest_path(src)
    if not path.is_file():
        raise MissingPrerequisite(f"There's no desktop extension manifest at {path}.",
                                  hint="It ships with Slate in mcpb/manifest.json; reinstall Slate if it's gone.")
    return jsonio.read(path)


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return match.group(1) if match else text


def server_tools() -> list[dict]:
    """The server's tools as the manifest lists them: name and a one-sentence description."""
    from slate.mcp.server import TOOLS  # noqa: PLC0415

    return [{"name": t.name, "description": first_sentence(t.description)} for t in TOOLS]


def validate(manifest: dict | None = None, src: Path | None = None) -> list[str]:
    """Everything wrong with the manifest, in plain sentences (empty when it's ready to pack)."""
    src = src or source_dir()
    data = manifest if manifest is not None else load(src)
    problems: list[str] = []
    for key in REQUIRED:
        if key not in data:
            problems.append(f"manifest.json has no {key}")
    if data.get("manifest_version") != MANIFEST_VERSION:
        problems.append(f"manifest_version is {data.get('manifest_version')!r}; Slate writes {MANIFEST_VERSION!r}")
    if not isinstance(data.get("author"), dict) or not data["author"].get("name"):
        problems.append("author needs a name")
    if data.get("version") != __version__:
        problems.append(f"version is {data.get('version')!r} but Slate is {__version__} (slate mcpb build --sync)")
    server = data.get("server") if isinstance(data.get("server"), dict) else {}
    if server.get("type") not in SERVER_TYPES:
        problems.append(f"server.type must be one of {', '.join(SERVER_TYPES)}")
    entry = server.get("entry_point")
    if not entry or not (src / entry).is_file():
        problems.append(f"server.entry_point {entry!r} isn't a file in {src}")
    config = server.get("mcp_config") if isinstance(server.get("mcp_config"), dict) else {}
    if not config.get("command"):
        problems.append("server.mcp_config needs a command")
    if "darwin" not in ((data.get("compatibility") or {}).get("platforms") or []):
        problems.append("compatibility.platforms must include darwin (Slate runs on macOS)")
    listed = data.get("tools")
    if listed != server_tools():
        problems.append("tools don't match the MCP server's own list (slate mcpb build --sync)")
    text = json.dumps(data)
    if re.search(r"sk_[A-Za-z0-9]{16,}|\bxi-api-key\b", text):
        problems.append("manifest.json looks like it holds a key; keys live in the Keychain, never in the extension")
    return problems


def sync(src: Path | None = None, *, write: bool = True) -> dict:
    """Rewrite the manifest's version and tools from Slate itself, keeping every hand-written field. With
    `write=False` (a dry run) it says whether it would change, and writes nothing."""
    src = src or source_dir()
    data = load(src)
    before = {"version": data.get("version"), "tools": data.get("tools")}
    data["version"] = __version__
    data["tools"] = server_tools()
    data["tools_generated"] = False
    changed = before != {"version": data["version"], "tools": data["tools"]}
    if changed and write:
        jsonio.write_atomic(manifest_path(src), data)
    return {"manifest": str(manifest_path(src)), "changed": changed, "version": __version__,
            "tools": [t["name"] for t in data["tools"]]}


def members(src: Path | None = None) -> dict[str, bytes]:
    """What goes in the .mcpb: every file under mcpb/, dotfiles left out."""
    src = src or source_dir()
    out: dict[str, bytes] = {}
    for root, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if name.startswith(".") or name in _SKIP:
                continue
            full = Path(root) / name
            out[full.relative_to(src).as_posix()] = full.read_bytes()
    return out


def pack(files: dict[str, bytes]) -> bytes:
    """A deterministic zip (sorted, fixed timestamps); the launcher keeps its executable bit."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.external_attr = (0o755 if name in EXECUTABLE else 0o644) << 16
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    return buf.getvalue()


def default_out() -> Path:
    return paths.REPO / "dist"


def build(out: str | os.PathLike | None = None, src: Path | None = None, *, write: bool = True) -> dict:
    """Check the manifest, then pack mcpb/ into dist/slate-<version>.mcpb. Refuses when the manifest has drifted."""
    src = src or source_dir()
    problems = validate(src=src)
    if problems:
        raise Refused("The desktop extension's manifest isn't ready:\n  " + "\n  ".join(problems),
                      hint="slate mcpb build --sync rewrites the version and tools; fix anything else by hand.",
                      details={"problems": problems})
    files = members(src)
    data = pack(files)
    target = Path(out).expanduser().resolve() if out else default_out()
    path = target / f"slate-{__version__}.mcpb"
    if write:
        jsonio.write_text_atomic(path, data)
    return {"path": str(path), "written": write, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "files": sorted(files), "version": __version__}


__all__ = ["load", "validate", "sync", "members", "pack", "build", "server_tools", "source_dir"]
