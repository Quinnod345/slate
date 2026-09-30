"""Small helpers the film-state modules share: time stamps, film-relative paths, a per-film file lock, a hash cache,
managed Markdown blocks and the language tables.

Nothing here knows about a particular stage. It exists so film.json, film.lock.json, approvals.json and the briefs are
always written the same careful way: under one lock, atomically, with hashes that don't re-read a 10 GB master every
time someone asks `slate status`.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from slate import jsonio, log
from slate.errors import UsageError

STATE_DIR = ".slate"  # hidden, git-ignored: the state lock and the hash cache
HIDDEN_NAMES = {".gitkeep", ".DS_Store", "README.md"}
LANGUAGES_FILE = Path(__file__).with_name("languages.json")
DEFAULT_LANGUAGE = "en"


def now() -> str:
    """Local time with its UTC offset, to the second (the same stamp gate results carry)."""
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


# ---------------------------------------------------------------------------------------------------------------
# Paths


def film_rel(film_dir: Path, path: str | os.PathLike) -> str:
    """A film-relative POSIX path for `path` (relative paths are taken as film-relative already).

    Refuses anything that escapes the film folder: state files only ever describe the film's own files.
    """
    film_dir = Path(film_dir).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = film_dir / p
    try:
        rel = p.resolve().relative_to(film_dir)
    except ValueError as exc:
        raise UsageError(f"{path} is outside the film folder {film_dir}.",
                         hint="Pass a path inside the film, relative to its folder (e.g. script/storyboard.json).") from exc
    text = rel.as_posix()
    if text in ("", "."):
        raise UsageError("That's the film folder itself; name a file or a folder inside it.")
    return text


def input_key(film_dir: Path, path: str | os.PathLike) -> str:
    """How an input is keyed in a lock entry: film-relative when inside the film, absolute otherwise."""
    p = Path(path)
    full = p if p.is_absolute() else Path(film_dir) / p
    try:
        return full.resolve().relative_to(Path(film_dir).resolve()).as_posix()
    except ValueError:
        return str(full.resolve())


def is_hidden(name: str) -> bool:
    return name.startswith(".") or name in HIDDEN_NAMES


def walk_files(root: Path, *, limit: int | None = None) -> list[Path]:
    """Every visible file under `root` (sorted), skipping hidden files and folders. `limit` stops early."""
    out: list[Path] = []
    if not root.exists():
        return out
    if root.is_file():
        return [root]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if is_hidden(name):
                continue
            out.append(Path(dirpath) / name)
            if limit is not None and len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------------------------------------------------------
# Locking


@contextlib.contextmanager
def state_lock(film_dir: Path) -> Iterator[None]:
    """Hold the film's state lock: one writer at a time for film.json, film.lock.json, approvals.json and the brief.

    Parallel act builders record lock entries at the same moment; without this, one record would silently lose the
    other. The lock is re-entrant within a process (a nested call just passes through).
    """
    film_dir = Path(film_dir)
    key = str(film_dir.resolve())
    held = _held()
    depth = held.get(key, 0)
    if depth:
        held[key] = depth + 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    lock_path = film_dir / STATE_DIR / "state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


_LOCAL = threading.local()


def _held() -> dict[str, int]:
    """This thread's held locks (re-entrancy is per thread; other threads and processes wait on the file lock)."""
    held = getattr(_LOCAL, "held", None)
    if held is None:
        held = _LOCAL.held = {}
    return held


# ---------------------------------------------------------------------------------------------------------------
# Hashing with a cache


class Hasher:
    """SHA-256 of files and folders, cached by (size, mtime) in the film's .slate/hashes.json.

    Like git's index: a file whose size and modification time haven't moved isn't re-read. A file modified in the last
    two seconds is never cached (its time stamp could be shared with a later write), so a quick rewrite can't hide.
    """

    RACY_SECONDS = 2.0

    def __init__(self, film_dir: Path | None = None):
        self.film_dir = Path(film_dir) if film_dir else None
        self._cache: dict[str, list] = {}
        self._dirty = False
        if self.film_dir:
            raw = jsonio.read(self._path(), default={}) or {}
            if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
                self._cache = raw["files"]

    def _path(self) -> Path:
        assert self.film_dir is not None
        return self.film_dir / STATE_DIR / "hashes.json"

    def sha(self, path: Path) -> str | None:
        """The file's SHA-256, or None when it doesn't exist (or isn't a file)."""
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None
        key = str(path.resolve())
        hit = self._cache.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        digest = jsonio.sha256(path)
        if time.time() - st.st_mtime > self.RACY_SECONDS:
            self._cache[key] = [st.st_size, st.st_mtime_ns, digest]
            self._dirty = True
        return digest

    def tree(self, path: Path) -> str | None:
        """A folder's hash: SHA-256 over its visible files' relative paths and hashes, in order. None if empty/missing."""
        path = Path(path)
        if not path.is_dir():
            return None
        files = walk_files(path)
        if not files:
            return None
        lines = [f"{f.relative_to(path).as_posix()}\0{self.sha(f)}\n" for f in files]
        return jsonio.sha256_bytes("".join(lines))

    def any(self, path: Path) -> tuple[str | None, str]:
        """(hash, kind) for a file ("file") or a folder ("tree")."""
        path = Path(path)
        if path.is_dir():
            return self.tree(path), "tree"
        return self.sha(path), "file"

    def save(self) -> None:
        if not (self.film_dir and self._dirty):
            return
        try:
            jsonio.write_atomic(self._path(), {"note": "Slate's hash cache (size, mtime_ns, sha256). Safe to delete.",
                                               "files": self._cache}, indent=None)
            self._dirty = False
        except OSError:
            pass  # a cache that can't be written only costs time

    def __enter__(self) -> "Hasher":
        return self

    def __exit__(self, *exc) -> None:
        self.save()


# ---------------------------------------------------------------------------------------------------------------
# Managed Markdown blocks


def block_markers(name: str) -> tuple[str, str]:
    return f"<!-- slate:begin {name} -->", f"<!-- slate:end {name} -->"


def replace_block(text: str, name: str, body: str) -> str:
    """Put `body` between the named markers. Missing markers are added at the end, never over hand-written text."""
    begin, end = block_markers(name)
    body = body.rstrip("\n")
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    replacement = f"{begin}\n{body}\n{end}" if body else f"{begin}\n{end}"
    if pattern.search(text):
        return pattern.sub(lambda _m: replacement, text, count=1)
    return text.rstrip("\n") + f"\n\n{replacement}\n"


def read_block(text: str, name: str) -> str | None:
    begin, end = block_markers(name)
    m = re.search(re.escape(begin) + r"\n?(.*?)\n?" + re.escape(end), text, re.S)
    return m.group(1) if m else None


def md_cell(text: Any) -> str:
    """Text safe inside a Markdown table cell (pipes escaped, line breaks as <br>)."""
    s = "" if text is None else str(text)
    return s.replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def quote_block(text: str) -> str:
    """A verbatim quote in Markdown, every line kept (blank lines included)."""
    return "\n".join(("> " + line) if line.strip() else ">" for line in text.splitlines()) or ">"


# ---------------------------------------------------------------------------------------------------------------
# Language tables


@lru_cache(maxsize=None)
def _languages() -> dict:
    return json.loads(LANGUAGES_FILE.read_text(encoding="utf-8"))


def language_table(lang: str | None) -> dict:
    """The table for a language (film.json 'language'), falling back to the base language, then to English."""
    tables = _languages()
    code = (lang or DEFAULT_LANGUAGE).strip()
    for candidate in (code, code.split("-")[0], DEFAULT_LANGUAGE):
        if candidate in tables and isinstance(tables[candidate], dict):
            if candidate != code and candidate == DEFAULT_LANGUAGE and code.split("-")[0] != DEFAULT_LANGUAGE:
                log.warn(f"No language table for {code!r} yet; splitting and screening notes with the "
                         f"{DEFAULT_LANGUAGE!r} table.")
            return tables[candidate]
    raise UsageError(f"languages.json has no table for {code!r} or {DEFAULT_LANGUAGE!r}.")


def supported_language(lang: str) -> bool:
    tables = _languages()
    return lang in tables or lang.split("-")[0] in tables


def split_items(text: str, lang: str | None = None) -> list[str]:
    """Split a request or a set of notes into items: list items when it's a list, otherwise sentences.

    The split is a first pass for a person or agent to check, so it errs towards keeping words together: nothing is
    dropped, and every item is the requester's own wording.
    """
    table = language_table(lang)
    text = (text or "").strip()
    if not text:
        return []
    item_re = re.compile(table["list_item"])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    marked = [ln for ln in lines if item_re.match(ln)]
    if len(marked) >= 2:
        items: list[str] = []
        preamble: list[str] = []
        for ln in lines:
            if item_re.match(ln):
                items.append(item_re.sub("", ln, count=1).strip())
            elif items:
                items[-1] = f"{items[-1]} {ln.strip()}"
            else:
                preamble.append(ln.strip())
        if preamble:
            items.insert(0, " ".join(preamble))
        return [i for i in items if i]
    flat = " ".join(ln.strip() for ln in lines)
    # The table's pattern marks the gap between two sentences as group 1; the punctuation stays with its sentence.
    parts, start = [], 0
    for m in re.finditer(table["sentence_end"], flat):
        parts.append(flat[start:m.start(1)])
        start = m.end(1)
    parts.append(flat[start:])
    return [p.strip() for p in parts if p.strip()]


def needs_new_approach(item: str, lang: str | None = None) -> str | None:
    """The phrase that marks `item` as asking for a new approach (so it becomes a question), or None."""
    low = f" {item.lower()} "
    for phrase in language_table(lang).get("new_approach", []):
        if re.search(r"(?<![a-z])" + re.escape(phrase.lower()) + r"(?![a-z])", low):
            return phrase
    return None


# ---------------------------------------------------------------------------------------------------------------
# Values from the command line


def parse_value(raw: str) -> Any:
    """A command-line value: JSON when it parses (numbers, true/false/null, objects, lists), otherwise the text."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def short(sha: str | None, n: int = 12) -> str:
    return sha[:n] if sha else "none"
