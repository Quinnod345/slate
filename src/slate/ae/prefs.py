"""Read or change one After Effects preference without damaging the rest.

AE keeps its preferences in a text file with classic Mac line endings (CR only, no LF). Rewriting that file in
Python's text mode silently turns every CR into LF; AE then can't read the file, throws it away and starts again from
factory defaults. That's how the person's "Allow Scripts to Write Files and Access Network" setting kept switching
itself off on 2026-09-30: the disk cache was toggled around master renders with text-mode rewrites, and AE reset 316
settings.

This module edits the file's bytes, changes exactly one value, and proves nothing else moved. Every AE preference
edit in Slate goes through it; nothing edits that file any other way. It is a faithful port of the film pipeline's
`ae_pref.py`, with Slate's exit codes and paths.

Rules it enforces:

- **AE must be closed for a live edit** (exit 5 otherwise). AE rewrites the whole file from memory when it quits, and
  quitting it for the person could lose unsaved work. aerender must be closed too.
- **One existing key in one section.** The value keeps its quoting style ("1" stays quoted, 01 stays bare). If the
  value is already what was asked for, nothing is written.
- **Nothing else moves.** After the edit it proves the line endings are unchanged and every other byte is identical.
  The write is atomic (a temp file, then a rename) and read back; the previous file is kept in
  `DATA/ae-prefs-backups/` with a timestamp.
- **The scripting security setting is never changed.** Any edit to `Pref_SCRIPTING_FILE_NETWORK_SECURITY` is refused,
  and every edit proves its value is untouched.
- **A damaged file is refused.** If the file already holds LF line endings, it stops and says so rather than editing.

Which file: `SLATE_AE_PREFS_FILE` (one file) or `SLATE_AE_PREFS_ROOT` (a folder shaped like
`~/Library/Preferences/Adobe/After Effects`) point Slate at a stand-in; tests and hand runs use them so the real file
is never touched. Passing `file=` works on a copy (no AE check, no backup), like the old `--file`.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import time
from pathlib import Path
from typing import Iterator

from slate import log, paths
from slate.ae import guard
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError

PROTECTED = frozenset({"Pref_SCRIPTING_FILE_NETWORK_SECURITY"})
SCRIPTING = ("Main Pref Section v2", "Pref_SCRIPTING_FILE_NETWORK_SECURITY")
DISK_CACHE = ("Disk Cache Controls", "Enabled 2")
DISK_CACHE_FOLDER = ("Disk Cache Controls", "Folder 7")
DISK_CACHE_MAX = ("Disk Cache Controls", "Max Size 3")

# Slate's own report channel (app.settings section "Slate") is data, not the person's preferences.
REPORT_SECTION = "Slate"
NOT_SETTINGS = (b'["Settings_' + REPORT_SECTION.encode() + b'"]',)
# Values that must never come back from an old copy: the security setting, a timestamp, a machine/GPU id.
NEVER_RESTORE = (b'"Pref_SCRIPTING_FILE_NETWORK_SECURITY"', b'"Pref_LAST_DATE_TIME"', b'"Mercury GPU Accelerator GUID"')
# The disk cache reflects the current machine state; restore never touches these sections.
NEVER_TOUCH_SECTIONS = frozenset({b'["Disk Cache Controls"]', b'["Additional Disk Cache Controls"]'})
# Settings a reset turns back to AE's defaults that are a person's own choices: restored when they differ.
RESTORE_CHANGED = frozenset({
    (b'["Coachmarks Settings"]', b'\t"Enabled"'),
    (b'["Coachmarks Settings"]', b'\t"Simple.Coachmarks.Seen"'),
    (b'["Coachmarks Seen By User"]', b'\t"Simple.Coachmarks.Enabled"'),
    (b'["Concurrent Frame Rendering"]', b'\t"Reserved CPU Power"'),
    (b'["Expression Editor Settings (v9)"]', b'\t"Indent Guides - Enabled"'),
    (b'["Main Pref Section v2"]', b'\t"Pref_COMP_PATH_DISPLAY_POST"'),
    (b'["Main Pref Section v2"]', b'\t"Pref_COMP_PATH_DISPLAY_PRE"'),
    (b'["Project Pref Section"]', b'\t"Project Settings Time Display Format"'),
    (b'["Item Window Settings"]', b'\t"Comp Axis State 0"'),
    (b'["Pref_New_SORT_PANELS v2"]', b'\t"Pref_New_SORT_PANELS_RQ"'),
})
# What a value may contain when Slate writes it: printable ASCII without quotes (it lands inside the file verbatim).
_SAFE_VALUE = re.compile(r"[ !#-~]*")


# ---------------------------------------------------------------------------------------------------------------
# Finding the file


def prefs_root() -> Path:
    """The folder that holds one sub-folder per AE version (`SLATE_AE_PREFS_ROOT` overrides it)."""
    env = os.environ.get("SLATE_AE_PREFS_ROOT")
    return Path(env).expanduser() if env else Path.home() / "Library" / "Preferences" / "Adobe" / "After Effects"


def version_key(name: str) -> tuple[int, ...]:
    """Sort key for version folders: numbers compared as numbers, so 26.10 comes after 26.5."""
    return tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)


def version_dirs(root: Path | None = None) -> list[Path]:
    """AE version folders, newest first (numeric sort; the old reader's lexicographic sort put 26.10 before 26.5)."""
    base = root or prefs_root()
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: version_key(p.name), reverse=True)


def live_prefs(root: Path | None = None, variant: str = "") -> Path:
    """The prefs file of the newest AE version on this Mac (or the `variant` file, e.g. 'indep-output')."""
    env = os.environ.get("SLATE_AE_PREFS_FILE")
    if env and not variant:
        return Path(env).expanduser()
    suffix = f"-{variant}" if variant else ""
    for d in version_dirs(root):
        f = d / f"Adobe After Effects {d.name} Prefs{suffix}.txt"
        if f.exists():
            return f
    base = root or prefs_root()
    raise MissingPrerequisite(f"No After Effects prefs file under {base}.",
                              hint="Open After Effects once so it writes its preferences, then run this again.")


def resolve(file: str | os.PathLike | None = None, variant: str = "") -> tuple[Path, bool]:
    """(path, live). A given `file` is a copy (no AE check, no backup); otherwise it's the file AE itself uses."""
    if file is not None:
        p = Path(file).expanduser()
        if not p.is_file():
            raise UsageError(f"No prefs file at {p}.")
        return p, False
    return live_prefs(variant=variant), True


# ---------------------------------------------------------------------------------------------------------------
# Byte-level reading


def damaged(data: bytes) -> bool:
    """True when the file holds LF line endings: AE will throw it away and reset every preference on next launch."""
    return b"\n" in data


def find(data: bytes, section: str, name: str) -> tuple[int, int] | None:
    """(start, end) of the value bytes for `name` inside `[section]`, or None. Lines end in CR.

    Raises Refused when the key appears twice in the section (no guessing which to change) or when its value wraps
    onto continuation lines (AE's format for long values; this tool edits single-line values only).
    """
    head = b'\r["' + section.encode() + b'"]\r'
    i = data.find(head)
    if i < 0:
        if data.startswith(head[1:]):
            i = -1
        else:
            return None
    body_start = i + len(head)
    nxt = data.find(b"\r[", body_start - 1)
    body_end = len(data) if nxt < 0 else nxt
    line = b'\r\t"' + name.encode() + b'" = '
    j = data.find(line, body_start - 1, body_end)
    if j < 0:
        return None
    if data.find(line, j + 1, body_end) >= 0:
        raise Refused(f'["{section}"] "{name}" appears more than once; not guessing which to change.')
    vs = j + len(line)
    ve = data.find(b"\r", vs)
    if ve >= 0 and data[ve + 1:ve + 3] == b"\t\t":
        raise Refused(f'["{section}"] "{name}" has a value that wraps onto more lines; not editing it.')
    return vs, (len(data) if ve < 0 else ve)


def value_of(data: bytes, section: str, name: str) -> str | None:
    """The value as text (outer quotes removed), or None when the key isn't there."""
    at = find(data, section, name)
    if at is None:
        return None
    raw = data[at[0]:at[1]].decode("utf-8", "surrogateescape")
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == '"' else raw


def _running(live: bool) -> list[str]:
    return guard.ae_open() if live else []


def _scripting_state(raw: str | None) -> bool | None:
    """True/False for the scripting setting's stored value ("1", or a bare byte like 01), None when unset/unknown."""
    if raw is None:
        return None
    v = raw.strip().strip('"')
    if v in ("1", "01"):
        return True
    if v in ("0", "00"):
        return False
    return None


def _describe_scripting(raw: str | None) -> str:
    state = _scripting_state(raw)
    if state is True:
        return "on"
    if state is False:
        return "off"
    return "not set (off)" if raw is None else f"unreadable ({raw!r}), treated as off"


# ---------------------------------------------------------------------------------------------------------------
# check / get / set


def check(file: str | os.PathLike | None = None, *, variant: str = "") -> dict:
    """Line endings, the scripting setting, the disk cache and what's running. Reads only; never writes.

    `ok` is False when the file is damaged (LF line endings). `scripting` is how the bridge's pref reads today
    ("on", "off" or "not set (off)"); Slate reports it and never changes it.
    """
    path, live = resolve(file, variant)
    data = path.read_bytes()
    try:
        sc = value_of(data, *SCRIPTING)
    except Refused:
        sc = None
    cache = {}
    for label, key in (("enabled", DISK_CACHE), ("folder", DISK_CACHE_FOLDER), ("max_gb", DISK_CACHE_MAX)):
        try:
            cache[label] = value_of(data, *key)
        except Refused:
            cache[label] = None
    return {
        "file": str(path),
        "live": live,
        "cr": data.count(b"\r"),
        "lf": data.count(b"\n"),
        "damaged": damaged(data),
        "ok": not damaged(data),
        "scripting": _describe_scripting(sc),
        "scripting_raw": sc,
        "scripting_on": _scripting_state(sc) is True,
        "disk_cache": {**cache, "on": cache.get("enabled") == "1"},
        "running": _running(live),
    }


def get(section: str, key: str, *, file: str | os.PathLike | None = None, variant: str = "") -> str:
    """One value, or Refused (exit 1) when the key isn't in that section."""
    path, _live = resolve(file, variant)
    value = value_of(path.read_bytes(), section, key)
    if value is None:
        raise Refused(f'["{section}"] "{key}" isn\'t in {path.name}.')
    return value


def _backup(path: Path) -> Path:
    """Copy the file into DATA/ae-prefs-backups with a timestamp (never overwriting an earlier backup)."""
    folder = paths.ensure_dir(paths.data() / "ae-prefs-backups")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = folder / f"{stamp} {path.name}"
    n = 1
    while dest.exists():
        n += 1
        dest = folder / f"{stamp}-{n} {path.name}"
    shutil.copy2(path, dest)
    return dest


def _write_verified(path: Path, out: bytes) -> None:
    """Atomic write next to the file (temp, fsync, keep its mode, rename), then read back and compare."""
    tmp = path.with_name(path.name + ".ae_pref.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(out)
            fh.flush()
            os.fsync(fh.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    if path.read_bytes() != out:
        raise SlateError(f"FAILED: {path} on disk doesn't match what was written. Check it with `slate ae prefs check`.")


def set(section: str, key: str, value: str, *, file: str | os.PathLike | None = None,  # noqa: A001 - §12 name
        variant: str = "") -> dict:
    """Change one existing value, proving nothing else moved. Returns {changed, old, new, file, backup}."""
    if key in PROTECTED:
        raise Refused(f'"{key}" is the person\'s security setting, and only they change it (in After Effects: '
                      "Settings > Scripting & Expressions). Slate never touches it.")
    value = str(value)
    if not _SAFE_VALUE.fullmatch(value):
        raise UsageError("A preference value can only hold printable ASCII without double quotes.")
    path, live = resolve(file, variant)
    data = path.read_bytes()
    if damaged(data):
        raise Refused("The prefs file already has LF line endings, so AE will reset it on its next launch. Don't "
                      "edit it; tell the person (and see `slate ae prefs restore`).")
    at = find(data, section, key)
    if at is None:
        raise Refused(f'["{section}"] "{key}" isn\'t in {path.name}. This tool changes existing settings only.')
    old = data[at[0]:at[1]]
    quoted = old[:1] == b'"' and old[-1:] == b'"'
    new = (b'"' + value.encode() + b'"') if quoted else value.encode()
    result = {"file": str(path), "section": section, "key": key, "old": old.decode("utf-8", "surrogateescape"),
              "new": new.decode(), "changed": False, "backup": None, "live": live}
    if new == old:
        return result
    if live:
        guard.require_ae_closed("changing an After Effects preference")
    out = data[:at[0]] + new + data[at[1]:]
    # Proofs: identical outside the value, same line endings, the security setting untouched.
    if not (out[:at[0]] == data[:at[0]] and out[at[0] + len(new):] == data[at[1]:]):
        raise SlateError("Refusing to write: bytes outside the value would change.")
    if out.count(b"\r") != data.count(b"\r") or out.count(b"\n") != 0 or data.count(b"\n") != 0:
        raise SlateError("Refusing to write: the line endings would change.")
    if value_of(out, *SCRIPTING) != value_of(data, *SCRIPTING):
        raise SlateError("Refusing to write: the scripting security setting would change.")
    if live:
        result["backup"] = str(_backup(path))
    _write_verified(path, out)
    result["changed"] = True
    return result


# ---------------------------------------------------------------------------------------------------------------
# Whole-entry model, used by restore
#
# A prefs file is a preamble (comment lines), then sections. A section is a "[name]" line, its entries and trailing
# blank lines. An entry is a line starting with TAB + quote, plus any continuation lines starting with two TABs (AE
# wraps long keys and values). Serializing the parse gives back the exact bytes.


def parse(data: bytes) -> tuple[list[bytes], list[dict]]:
    pre: list[bytes] = []
    secs: list[dict] = []
    cur: dict | None = None
    for ln in data.split(b"\r"):
        if ln.startswith(b"["):
            cur = {"name": ln, "entries": [], "tail": []}
            secs.append(cur)
        elif cur is None:
            pre.append(ln)
        elif ln.startswith(b'\t"') and not cur["tail"]:
            cur["entries"].append([ln])
        elif ln.startswith(b"\t\t") and cur["entries"] and not cur["tail"]:
            cur["entries"][-1].append(ln)
        else:
            cur["tail"].append(ln)
    return pre, secs


def serialize(pre: list[bytes], secs: list[dict]) -> bytes:
    out = list(pre)
    for s in secs:
        out.append(s["name"])
        for e in s["entries"]:
            out.extend(e)
        out.extend(s["tail"])
    return b"\r".join(out)


def entry_key(entry: list[bytes]) -> bytes:
    joined = b"\r".join(entry)
    i = joined.find(b'" = ')
    return joined[: i + 1] if i >= 0 else joined


def entry_value(entry: list[bytes]) -> bytes:
    joined = b"\r".join(entry)
    i = joined.find(b'" = ')
    return joined[i + 4:] if i >= 0 else b""


def _section_label(name: str) -> bytes:
    """Accept "Settings_X" or '["Settings_X"]' and return the exact header bytes."""
    name = name.strip()
    if name.startswith('["') and name.endswith('"]'):
        return name.encode()
    return b'["' + name.encode() + b'"]'


def plan_restore(cur: bytes, old: bytes, *, skip_sections: tuple[str, ...] = ()) -> tuple[bytes, list, list, list]:
    """What restoring settings from `old` into `cur` would do: (merged bytes, added, changed, skipped_mru).

    Never touches the security setting, the disk cache, timestamps, GPU ids or Slate's report data (nor any
    `skip_sections`). A key that exists now keeps its current value unless it's in RESTORE_CHANGED. Recent-files
    lists are restored only where the list is empty now, so an old list is never mixed into a new one.
    """
    pre, secs = parse(cur)
    if serialize(pre, secs) != cur or serialize(*parse(old)) != old:
        raise SlateError("The prefs parser didn't round-trip one of the files exactly; not restoring anything.")
    _, osecs = parse(old)
    skip = frozenset(NOT_SETTINGS) | {_section_label(s) for s in skip_sections}
    by_name = {s["name"]: s for s in secs}
    added: list = []
    changed: list = []
    skipped_mru: list = []
    for os_ in osecs:
        name = os_["name"]
        if name in skip or name in NEVER_TOUCH_SECTIONS:
            continue
        keep = [e for e in os_["entries"] if not any(k in entry_key(e) for k in NEVER_RESTORE)]
        if name not in by_name:
            if not keep:
                continue
            new = {"name": name, "entries": [list(e) for e in keep], "tail": [b""]}
            pos = next((i for i, s in enumerate(secs) if s["name"].lower() > name.lower()), len(secs))
            secs.insert(pos, new)
            by_name[name] = new
            added += [(name, entry_key(e)) for e in keep]
            continue
        cs = by_name[name]
        have = {entry_key(e): i for i, e in enumerate(cs["entries"])}
        is_mru = b"Most Recently Used" in name
        for e in keep:
            k = entry_key(e)
            if k in have:
                if (name, k) in RESTORE_CHANGED and cs["entries"][have[k]] != e:
                    changed.append((name, k, entry_value(cs["entries"][have[k]]), entry_value(e)))
                    cs["entries"][have[k]] = list(e)
                continue
            if is_mru and cs["entries"]:
                skipped_mru.append((name, k))
                continue
            pos = next((i for i, x in enumerate(cs["entries"]) if entry_key(x).lower() > k.lower()), len(cs["entries"]))
            cs["entries"].insert(pos, list(e))
            have = {entry_key(x): i for i, x in enumerate(cs["entries"])}
            added.append((name, k))
    merged = serialize(pre, secs)
    # Proofs: CR only, the security setting and the disk cache exactly as now, every current entry still present.
    if b"\n" in merged:
        raise SlateError("Restore would introduce LF line endings; not restoring anything.")
    if value_of(merged, *SCRIPTING) != value_of(cur, *SCRIPTING):
        raise SlateError("Restore would change the scripting security setting; not restoring anything.")
    _, msecs = parse(merged)
    mby = {s["name"]: s for s in msecs}
    for s in parse(cur)[1]:
        m = mby[s["name"]]
        if s["name"] in NEVER_TOUCH_SECTIONS and m["entries"] != s["entries"]:
            raise SlateError("Restore would change the disk cache settings; not restoring anything.")
        mkeys = {entry_key(e): e for e in m["entries"]}
        for e in s["entries"]:
            k = entry_key(e)
            if k not in mkeys:
                raise SlateError(f"Restore would drop {k!r}; not restoring anything.")
            if (s["name"], k) not in RESTORE_CHANGED and mkeys[k] != e:
                raise SlateError(f"Restore would change {k!r}; not restoring anything.")
    return merged, added, changed, skipped_mru


def _text(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def restore(src: str | os.PathLike, *, file: str | os.PathLike | None = None, apply: bool = False,
            variant: str = "", skip_sections: tuple[str, ...] = ()) -> dict:
    """Add back settings lost in a reset, from an older copy of the prefs file. A dry run unless `apply`.

    Returns the plan ({added, changed, skipped_mru, by_section}) and, with `apply`, where the backup went.
    """
    path, live = resolve(file, variant)
    src_path = Path(src).expanduser()
    if not src_path.is_file():
        raise UsageError(f"No older prefs file at {src_path}.")
    data = path.read_bytes()
    old = src_path.read_bytes()
    if damaged(data) or damaged(old):
        raise Refused("One of the files has LF line endings, so it isn't a prefs file AE can read; not restoring.")
    merged, added, changed, skipped = plan_restore(data, old, skip_sections=skip_sections)
    by_section: dict[str, int] = {}
    for n, _k in added:
        by_section[_text(n)] = by_section.get(_text(n), 0) + 1
    result = {
        "file": str(path), "from": str(src_path), "live": live, "applied": False, "backup": None,
        "added": [{"section": _text(n), "key": _text(k).strip()} for n, k in added],
        "changed": [{"section": _text(n), "key": _text(k).strip(), "now": _text(now), "was": _text(was)}
                    for n, k, now, was in changed],
        "skipped_mru": [{"section": _text(n), "key": _text(k).strip()} for n, k in skipped],
        "by_section": by_section,
        "unchanged": "the scripting setting, the disk cache, timestamps, GPU ids, and Slate's report data",
    }
    if not apply:
        return result
    if merged == data:
        result["applied"] = True
        return result
    if live:
        guard.require_ae_closed("restoring After Effects preferences")
        result["backup"] = str(_backup(path))
    _write_verified(path, merged)
    result["applied"] = True
    return result


# ---------------------------------------------------------------------------------------------------------------
# The disk-cache toggle around master renders


@contextlib.contextmanager
def disk_cache_off(*, file: str | os.PathLike | None = None) -> Iterator[dict]:
    """Turn AE's disk cache off for a render and always put the previous value back, even on failure.

    AE's disk cache filled 60 GB during one master render, and it buys nothing for one-pass renders. AE must be
    closed (exit 5 otherwise). When the key isn't in the file (a fresh install), nothing is edited and the render
    goes ahead. If the restore can't happen (AE was opened mid-render), the exact command to run is logged.
    """
    guard.require_ae_closed("switching After Effects' disk cache off for the render")
    info = check(file)
    if info["damaged"]:
        raise Refused("AE's prefs file has LF line endings (damaged); not touching it.",
                      hint="Run `slate ae prefs check` and tell the person; `slate ae prefs restore` can help.")
    state: dict = {"was": None, "toggled": False, "restored": None, "file": info["file"]}
    try:
        was = get(*DISK_CACHE, file=file)
    except Refused:
        log.warn("AE's disk cache setting isn't in its prefs file yet; leaving the cache alone for this render.")
        yield state
        return
    state["was"] = was
    set(*DISK_CACHE, "0", file=file)
    state["toggled"] = was != "0"
    try:
        yield state
    finally:
        try:
            set(*DISK_CACHE, was, file=file)
            state["restored"] = True
        except SlateError as exc:
            state["restored"] = False
            log.error(f"AE's disk cache was NOT restored ({exc.message}). Once After Effects is closed, run: "
                      f'slate ae prefs set "{DISK_CACHE[0]}" "{DISK_CACHE[1]}" {was}')
