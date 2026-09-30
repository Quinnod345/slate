"""The sampled instruments behind the palette's tonal roles, found on this Mac, never shipped.

Proof 2's tonal roles (small felt notes, pads) play on real sampled instruments from Apple's own libraries, the ones
GarageBand and Logic install: Logic Sampler zone maps (.exs files) and the GarageBand Instrument Library samples they
point at. Apple licenses that content for use in your own productions made with its apps, not for redistribution, so
Slate never copies, bundles or uploads it: it's read in place, on the machine that renders, and a finished mix is the
only thing that leaves.

A missing instrument is a failure, never a fallback. There's no synthesized stand-in for a tonal role (the owner found
the synthesized pads and notes "the problem"), so `require()` raises and `slate sound instruments` names exactly what
to download, which a person does, free, in GarageBand: GarageBand > Sound Library > Download All Available Sounds.
Slate never downloads it, and never opens an app to do it.

`SLATE_APPLE_LIBRARY` points the lookup at another folder that stands in for /Library (tests use it).
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

from slate.errors import MissingPrerequisite

LIBRARY_ROOTS = {
    "logic-sampler": "Application Support/Logic/Sampler Instruments",
    "garageband-instruments": "Application Support/GarageBand/Instrument Library/Sampler/Sampler Files",
    "fcp-sound-effects": "Audio/Apple Loops/Apple/Final Cut Pro Sound Effects",
    "apple-loops": "Audio/Apple Loops/Apple",
    "alchemy-samples": "Application Support/Logic/Alchemy Samples",
}
LIBRARY_NAMES = {
    "logic-sampler": "Logic Sampler instruments (zone maps)",
    "garageband-instruments": "GarageBand Instrument Library (the samples)",
    "fcp-sound-effects": "Final Cut Pro Sound Effects",
    "apple-loops": "Apple Loops",
    "alchemy-samples": "Logic Alchemy samples",
}
DOWNLOAD = ("Open GarageBand and choose GarageBand > Sound Library > Download All Available Sounds (free). With Logic "
            "Pro, it's Logic Pro > Sound Library > Download All Available Sounds. Slate never downloads or copies Apple's "
            "sounds itself.")


def library_base() -> Path:
    return Path(os.environ.get("SLATE_APPLE_LIBRARY") or "/Library").expanduser()


def roots() -> dict[str, Path]:
    base = library_base()
    return {k: base / v for k, v in LIBRARY_ROOTS.items()}


def _remap(folder: str) -> str:
    """Sample paths inside a zone map are absolute /Library paths; follow SLATE_APPLE_LIBRARY when it's set."""
    base = os.environ.get("SLATE_APPLE_LIBRARY")
    if base and folder.startswith("/Library/"):
        return str(Path(base).expanduser() / folder[len("/Library/"):])
    return folder


# ---------------------------------------------------------------------------------------------------------------
# Logic Sampler (.exs) zone maps: a read-only parser (the chunk layout ConvertWithMoss and exs2sfz document)


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("latin-1", "replace")


def _s8(v: int) -> int:
    return v - 256 if v > 127 else v


def parse_exs(path: str | os.PathLike) -> tuple[list[dict], list[dict], list[dict]]:
    """(zones, groups, samples) of a .exs zone map. 84-byte chunk headers: u32 sig, u32 size, u32 id, u32 flags,
    4-byte magic, 64-byte name; chunk type = (sig & 0x0F000000) >> 24: 1 zone, 2 group, 3 sample."""
    data = Path(path).read_bytes()
    if len(data) < 84:
        return [], [], []
    magic = data[16:20]
    end = ">" if magic in (b"SOBT", b"SOBJ") else "<"
    zones, groups, samples = [], [], []
    expanded = struct.unpack(end + "I", data[4:8])[0] > 0x8000
    i = 0
    while i + 84 <= len(data):
        sig, size = struct.unpack(end + "II", data[i:i + 8])
        if expanded and size > 0x8000:
            size -= 0x8000
        ctype = (sig & 0x0F000000) >> 24
        name = _cstr(data[i + 20:i + 84])
        c = data[i + 84:i + 84 + size]
        if ctype == 1 and len(c) >= 96:
            u = lambda o, c=c: struct.unpack(end + "I", c[o:o + 4])[0]  # noqa: E731
            zones.append({"name": name, "root": c[1], "fine": _s8(c[2]), "klo": c[6], "khi": c[7], "vlo": c[9],
                          "vhi": c[10], "start": u(12), "end": u(16), "lstart": u(20), "lend": u(24),
                          "loop_on": bool(c[33] & 1), "coarse": _s8(c[80]), "group": u(88), "sample": u(92)})
        elif ctype == 2:
            groups.append({"name": name, "vlo": c[5] if len(c) > 6 else None, "vhi": c[6] if len(c) > 6 else None})
        elif ctype == 3 and len(c) >= 80:
            u = lambda o, c=c: struct.unpack(end + "I", c[o:o + 4])[0]  # noqa: E731
            samples.append({"name": name, "rate": u(8), "bits": u(12), "folder": _cstr(c[80:336]),
                            "file": _cstr(c[336:592]) if len(c) > 336 else name})
        i += 84 + size
    return zones, groups, samples


# ---------------------------------------------------------------------------------------------------------------
# Roles


def check_role(role: str, spec: dict) -> dict:
    """One tonal role: is its instrument installed, with every sample it needs? (ok | missing | incomplete)."""
    library = spec.get("library", "logic-sampler")
    base = roots().get(library)
    out = {"role": role, "library": library, "why": spec.get("why"), "instrument": spec.get("exs") or spec.get("path")}
    if base is None:
        return {**out, "status": "missing", "summary": f"unknown library {library!r}", "fix": DOWNLOAD}
    if library != "logic-sampler":
        p = base / str(spec.get("path", ""))
        ok = p.exists()
        return {**out, "path": str(p), "status": "ok" if ok else "missing",
                "summary": "installed" if ok else f"not installed ({p})", "fix": None if ok else DOWNLOAD}
    exs = base / str(spec["exs"])
    out["path"] = str(exs)
    if not exs.is_file():
        return {**out, "status": "missing", "summary": f"{Path(spec['exs']).stem} isn't installed ({exs})",
                "fix": DOWNLOAD}
    try:
        zones, groups, samples = parse_exs(exs)
    except (OSError, struct.error) as exc:
        return {**out, "status": "missing", "summary": f"{exs.name} can't be read ({exc})", "fix": DOWNLOAD}
    if not samples:
        return {**out, "status": "incomplete", "summary": f"{exs.name} lists no samples", "fix": DOWNLOAD}
    folders = sorted({_remap(s["folder"]) for s in samples})
    missing = [s for s in samples if not Path(_remap(s["folder"]), s["file"]).exists()]
    status = "ok" if not missing else "incomplete"
    summary = (f"{Path(spec['exs']).stem}: {len(samples)} samples in place" if not missing else
               f"{Path(spec['exs']).stem}: {len(missing)} of {len(samples)} samples missing (first: "
               f"{Path(_remap(missing[0]['folder']), missing[0]['file'])})")
    return {**out, "status": status, "summary": summary, "zones": len(zones), "samples": len(samples),
            "missing_samples": len(missing), "sample_folders": folders[:6], "fix": None if not missing else DOWNLOAD}


def report(palette: dict | None = None) -> dict:
    """Every tonal role in the palette and every Apple library root: what's installed, where, and what's missing."""
    if palette is None:
        from slate.sound.effects.palette import STARTING_PALETTE  # noqa: PLC0415

        palette = STARTING_PALETTE
    roles = [check_role(role, spec) for role, spec in (palette.get("instruments") or {}).items()]
    libs = {k: {"name": LIBRARY_NAMES[k], "path": str(p), "installed": p.is_dir()} for k, p in roots().items()}
    ok = all(r["status"] == "ok" for r in roles)
    return {"ok": ok, "roles": roles, "libraries": libs, "download": None if ok else DOWNLOAD,
            "redistribution": "Apple's sound content is used in place on this Mac and never copied, bundled or uploaded.",
            "library_base": str(library_base())}


def require(role: str, palette: dict | None = None) -> dict:
    """The installed instrument for a tonal role, or MissingPrerequisite (exit 3): there's no synthesized stand-in."""
    if palette is None:
        from slate.sound.effects.palette import STARTING_PALETTE  # noqa: PLC0415

        palette = STARTING_PALETTE
    spec = (palette.get("instruments") or {}).get(role)
    if spec is None:
        raise MissingPrerequisite(f"The palette has no instrument for the tonal role {role!r}.",
                                  hint="Add it under sound.palette.instruments in the brand pack.")
    res = check_role(role, spec)
    if res["status"] != "ok":
        raise MissingPrerequisite(f"The {role} role's instrument isn't fully installed: {res['summary']}. Slate won't "
                                  "synthesize a stand-in for a tonal role.", hint=DOWNLOAD, details=res)
    return res


def doctor_check(palette: dict | None = None) -> list[dict]:
    """Doctor rows ({id, status: green|red, summary, fix}) for `slate doctor`."""
    rep = report(palette)
    rows = []
    for r in rep["roles"]:
        rows.append({"id": f"sound.instrument.{r['role']}", "status": "green" if r["status"] == "ok" else "red",
                     "summary": r["summary"], "fix": r.get("fix")})
    return rows
