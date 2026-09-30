"""The two modules film state leans on, behind one small door: formats and brand packs.

`slate.formats` (format templates) and `slate.brand.pack` (brand packs) are built by other modules. When they're
installed, film state calls exactly the functions docs/ARCHITECTURE.md §12 names. When one isn't there yet (or fails to
import), film state reads the same data files directly (`formats/<name>.json`, `brands/<name>/brand.json`, §7), so
`slate new` still works and says what it fell back to. Nothing here invents a format or a brand.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from slate import jsonio, log, paths
from slate.errors import UsageError

BRAND_REFERENCES = ("truth", "look_motion", "voice", "sound")
_WARNED: set[str] = set()


def _module(name: str):
    """Import an optional module; None when it isn't built (or is broken), with one warning per process."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and (name == exc.name or name.startswith(exc.name + ".")):
            return None
        _warn_once(name, f"{name} failed to import ({exc}); reading its data files directly.")
        return None
    except Exception as exc:  # noqa: BLE001 - a broken sibling module must not stop film state
        _warn_once(name, f"{name} failed to import ({type(exc).__name__}: {exc}); reading its data files directly.")
        return None


def _warn_once(key: str, message: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        log.warn(message)


# ---------------------------------------------------------------------------------------------------------------
# Formats


def format_load(name_or_path: str) -> tuple[dict, str]:
    """(format dict, where it came from). Accepts a format name or a path to a format JSON file."""
    raw = str(name_or_path).strip()
    if not raw:
        raise UsageError("A film needs a format (--format).", hint=f"Known formats: {', '.join(format_names()) or 'none yet'}.")
    candidate = Path(raw).expanduser()
    if candidate.suffix == ".json" and candidate.is_file():
        data = jsonio.read(candidate)
        if not isinstance(data, dict):
            raise UsageError(f"{candidate} isn't a format (expected a JSON object).")
        data.setdefault("name", candidate.stem)
        return data, str(candidate.resolve())
    mod = _module("slate.formats")
    if mod is not None and hasattr(mod, "load"):
        data = mod.load(raw)
        if not isinstance(data, dict):
            raise UsageError(f"slate.formats.load({raw!r}) didn't return a format.")
        data.setdefault("name", raw)
        return data, "slate.formats"
    path = paths.FORMATS_DIR / f"{raw}.json"
    if path.is_file():
        data = jsonio.read(path)
        data.setdefault("name", raw)
        return data, str(path)
    known = format_names()
    raise UsageError(f"No format called {raw!r}.",
                     hint=(f"Known formats: {', '.join(known)}." if known else
                           "No formats are installed yet; pass a format file with --format path/to/format.json."))


def format_names() -> list[str]:
    mod = _module("slate.formats")
    if mod is not None and hasattr(mod, "names"):
        try:
            return sorted(mod.names())
        except Exception as exc:  # noqa: BLE001
            _warn_once("formats.names", f"slate.formats.names() failed ({exc}); listing formats/*.json instead.")
    if not paths.FORMATS_DIR.is_dir():
        return []
    return sorted(p.stem for p in paths.FORMATS_DIR.glob("*.json") if not p.name.startswith(("_", ".")))


def safe_zones(fmt: dict, platform: str) -> dict | None:
    """Safe zones for a platform, from slate.formats when it's there, else from the format's own data."""
    mod = _module("slate.formats")
    if mod is not None and hasattr(mod, "safe_zones"):
        try:
            return mod.safe_zones(fmt, platform)
        except Exception as exc:  # noqa: BLE001 - an unknown platform is not our failure to report
            log.warn(f"slate.formats.safe_zones({fmt.get('name')!r}, {platform!r}) failed: {exc}")
            return None
    zones = fmt.get("safe_zones") or {}
    if isinstance(zones, dict):
        return zones.get(platform) or zones.get("default")
    return None


def format_platforms(fmt: dict) -> list[str]:
    zones = fmt.get("safe_zones")
    platforms = fmt.get("platforms")
    if isinstance(platforms, list):
        return [str(p) for p in platforms]
    if isinstance(zones, dict):
        return [k for k in zones if k != "default"]
    return []


# ---------------------------------------------------------------------------------------------------------------
# Brand packs


def _brand_file(name: str) -> Path | None:
    candidate = Path(name).expanduser()
    if candidate.name == "brand.json" and candidate.is_file():
        return candidate
    if (candidate / "brand.json").is_file() and (candidate.is_absolute() or str(name).startswith(".")):
        return candidate / "brand.json"
    for folder in paths.brand_dirs():
        path = Path(folder) / str(name) / "brand.json"
        if path.is_file():
            return path
    return None


def brand_find(name: str) -> Path:
    """The brand pack's folder. A brand Slate can't find is a usage error that says how to make one."""
    mod = _module("slate.brand.pack")
    if mod is not None and hasattr(mod, "find"):
        found = Path(mod.find(name))
        return found.parent if found.name == "brand.json" else found
    path = _brand_file(name)
    if path is None:
        searched = ", ".join(str(d) for d in paths.brand_dirs())
        raise UsageError(f"No brand pack called {name!r} (looked in {searched}).",
                         hint="Onboard the brand first: slate brand init <name> (the demo pack is 'demo').")
    return path.parent


def brand_load(name: str) -> dict:
    mod = _module("slate.brand.pack")
    if mod is not None and hasattr(mod, "load"):
        pack = mod.load(name)
        if isinstance(pack, dict):
            return pack
    path = _brand_file(name)
    if path is None:
        brand_find(name)  # raises the helpful error
        raise UsageError(f"No brand pack called {name!r}.")
    pack = jsonio.read(path)
    if not isinstance(pack, dict):
        raise UsageError(f"{path} isn't a brand pack (expected a JSON object).")
    pack.setdefault("name", path.parent.name)
    pack.setdefault("_dir", str(path.parent))
    return pack


def _reference_approved(ref: Any) -> bool:
    return isinstance(ref, dict) and bool(ref.get("approved_by")) and bool(ref.get("sha256") or ref.get("file"))


def brand_missing_references(pack: dict) -> list[str]:
    """Which of the four references (truth, look and motion, voice, sound) aren't approved yet."""
    mod = _module("slate.brand.pack")
    if mod is not None and hasattr(mod, "missing_references"):
        return list(mod.missing_references(pack))
    refs = pack.get("references") or {}
    return [key for key in BRAND_REFERENCES if not _reference_approved(refs.get(key))]


def brand_references_approved(pack: dict) -> bool:
    mod = _module("slate.brand.pack")
    if mod is not None and hasattr(mod, "references_approved"):
        return bool(mod.references_approved(pack))
    return not brand_missing_references(pack)


def brand_reference_approved(pack: dict, key: str) -> bool:
    """One reference (e.g. 'look_motion') approved in the pack."""
    return key not in brand_missing_references(pack)


def brand_approved_claims(pack: dict) -> list[dict]:
    mod = _module("slate.brand.pack")
    if mod is not None and hasattr(mod, "approved_claims"):
        return list(mod.approved_claims(pack))
    claims = (pack.get("truth") or {}).get("approved_claims") or []
    return [c if isinstance(c, dict) else {"text": str(c)} for c in claims]


def brand_voice(pack: dict) -> dict | None:
    voice = pack.get("voice")
    return voice if isinstance(voice, dict) and voice else None
