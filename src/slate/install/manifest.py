"""The install manifest: a record of everything the installer created, so uninstall reverses exactly that.

It lives in Slate's data folder (`install/manifest.json`), never in the checkout, and holds paths and commands only:
no keys, no environment. Uninstall trusts it only as far as the file system agrees: a link is removed only while it
still points where the manifest says, so something a person put there since is left alone.
"""

from __future__ import annotations

from pathlib import Path

from slate import jsonio
from slate.gates import now_iso
from slate.install.layout import Layout

VERSION = 1


def empty(layout: Layout) -> dict:
    return {"version": VERSION, "checkout": str(layout.repo), "home": str(layout.home), "agents": [], "links": [],
            "backups": [], "mcp": [], "updated_at": None}


def load(layout: Layout) -> dict:
    data = jsonio.read(layout.manifest_file, default=None)
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return empty(layout)
    for key in ("links", "backups", "mcp", "agents"):
        data.setdefault(key, [])
    return data


def save(layout: Layout, data: dict) -> Path:
    data["updated_at"] = now_iso()
    data["checkout"] = str(layout.repo)
    data["home"] = str(layout.home)
    return jsonio.write_atomic(layout.manifest_file, data)


def link_entry(data: dict, target: Path | str) -> dict | None:
    target = str(target)
    for entry in data.get("links", []):
        if entry.get("target") == target:
            return entry
    return None


def put_link(data: dict, *, target: Path, source: Path | None, kind: str) -> None:
    entry = link_entry(data, target)
    record = {"target": str(target), "source": str(source) if source else None, "kind": kind, "at": now_iso()}
    if entry is None:
        data.setdefault("links", []).append(record)
    else:
        entry.update(record)


def drop_link(data: dict, target: Path | str) -> None:
    target = str(target)
    data["links"] = [e for e in data.get("links", []) if e.get("target") != target]


def add_backup(data: dict, *, original: Path, backup: Path, kind: str) -> None:
    data.setdefault("backups", []).append({"original": str(original), "backup": str(backup), "kind": kind,
                                           "at": now_iso(), "restored": False})


def put_mcp(data: dict, *, client: str, command: list[str]) -> None:
    data["mcp"] = [m for m in data.get("mcp", []) if m.get("client") != client]
    data["mcp"].append({"client": client, "command": list(command), "at": now_iso()})


def drop_mcp(data: dict, client: str) -> None:
    data["mcp"] = [m for m in data.get("mcp", []) if m.get("client") != client]
