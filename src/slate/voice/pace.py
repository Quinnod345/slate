"""Pace a read a little quicker while keeping it natural (the films' `vo_pace.py`).

ElevenLabs' `speed` barely moves eleven_v4 (one line read 13.28 s at 1.0, 12.80 s at 1.06 and 13.52 s at 1.1:
take-to-take variation is larger than the setting), so pace is set after generation, in two gentle steps:

1. Pauses: every silence inside the read longer than `min_pause` is shortened to `pause_keep` of its length (never
   below `min_pause`), cut from its middle so decays and breath-ins stay.
2. Tempo: Rubber Band's finer engine (R3) with formants preserved, so pitch and timbre stay the voice's own.

Rubber Band should be the native arm64 build. An x86_64 build running under Rosetta works but is flagged, and the
binary's architecture is recorded with every paced take.
"""

from __future__ import annotations

import platform
import re
import struct
import tempfile
from pathlib import Path

import numpy as np

from slate import jsonio, log, procs
from slate.errors import MissingPrerequisite, UsageError
from slate.voice import audio as audio_mod
from slate.voice.film import VoiceFilm, now, record

CPU_TYPES = {0x01000007: "x86_64", 0x0100000C: "arm64", 7: "i386", 12: "arm", 18: "ppc"}
MH_MAGIC_64, MH_CIGAM_64, MH_MAGIC, MH_CIGAM = 0xFEEDFACF, 0xCFFAEDFE, 0xFEEDFACE, 0xCEFAEDFE
FAT_MAGIC, FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF


def macho_archs(data: bytes) -> list[str]:
    """Architectures in a Mach-O header (thin or universal). Empty for anything else (a script, say)."""
    if len(data) < 8:
        return []
    be = struct.unpack(">I", data[:4])[0]
    if be in (FAT_MAGIC, FAT_MAGIC_64):
        count = struct.unpack(">I", data[4:8])[0]
        step = 20 if be == FAT_MAGIC else 32
        out = []
        for i in range(min(count, 16)):
            off = 8 + i * step
            if off + 4 > len(data):
                break
            cpu = struct.unpack(">I", data[off:off + 4])[0]
            out.append(CPU_TYPES.get(cpu, hex(cpu)))
        return out
    le = struct.unpack("<I", data[:4])[0]
    if le in (MH_MAGIC_64, MH_MAGIC):
        cpu = struct.unpack("<I", data[4:8])[0]
        return [CPU_TYPES.get(cpu, hex(cpu))]
    if le in (MH_CIGAM_64, MH_CIGAM):
        cpu = struct.unpack(">I", data[4:8])[0]
        return [CPU_TYPES.get(cpu, hex(cpu))]
    return []


def machine() -> str:
    """This Mac's real architecture (arm64 on Apple silicon, even when Python itself runs under Rosetta)."""
    try:
        translated = procs.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001 - no sysctl (not macOS): trust platform.machine()
        translated = ""
    if translated == "1":
        return "arm64"
    return platform.machine()


def rubberband_info(path: str | None = None, host: str | None = None) -> dict:
    """Where Rubber Band is, its version and architecture, and whether it runs natively. Never raises."""
    exe = path or procs.which("rubberband")
    host = host or machine()
    if not exe:
        return {"path": None, "status": "missing", "machine": host,
                "note": "Rubber Band isn't installed; pacing needs its arm64 build (`slate doctor --install`)."}
    real = str(Path(exe).resolve())
    try:
        with open(real, "rb") as fh:
            archs = macho_archs(fh.read(4096))
    except OSError:
        archs = []
    version = None
    try:
        proc = procs.run([exe, "--version"], timeout=20)
        m = re.search(r"\d+\.\d+(?:\.\d+)?", (proc.stdout or "") + (proc.stderr or ""))
        version = m.group(0) if m else None
    except Exception:  # noqa: BLE001 - a version we can't read is recorded as unknown
        version = None
    native = host in archs
    rosetta = host == "arm64" and not native and "x86_64" in archs
    status = "ok" if native or not archs else ("rosetta" if rosetta else "foreign")
    note = {"ok": "native build", "rosetta": "x86_64 build running under Rosetta: it works, but arm64 is the target "
                                             "(brew reinstall rubberband from the arm64 Homebrew in /opt/homebrew)",
            "foreign": f"built for {', '.join(archs)}, not this {host} Mac"}[status]
    return {"path": exe, "resolved": real, "version": version, "arch": archs, "machine": host, "native": native,
            "rosetta": rosetta, "status": status, "note": note}


def shorten_pauses(x: np.ndarray, sr: int, *, pause_keep: float = 0.8, min_pause: float = 0.25,
                   threshold_db: float = -45.0) -> tuple[np.ndarray, list[list[float]]]:
    """Shorten every interior pause over `min_pause` to `pause_keep` of its length, cut from the middle.
    Returns (samples, cuts) with cuts as [start, end] in the source's seconds."""
    hop = max(1, sr // 100)  # 10 ms windows
    n = len(x) // hop
    if n == 0:
        return x.copy(), []
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    quiet = 20 * np.log10(rms) < threshold_db
    voiced = np.nonzero(~quiet)[0]
    if len(voiced) == 0:
        raise UsageError("The take is silent: nothing to pace.")
    first, last = int(voiced[0]), int(voiced[-1])
    keep = np.ones(n, dtype=bool)
    cuts: list[list[float]] = []
    i = first
    while i <= last:
        if quiet[i]:
            j = i
            while j <= last and quiet[j]:
                j += 1
            length = (j - i) * hop / sr
            if length > min_pause:
                target = max(min_pause, length * pause_keep)
                cut = int(round((length - target) * sr / hop))
                if cut > 0:
                    mid = i + (j - i) // 2
                    a = mid - cut // 2
                    keep[a:a + cut] = False
                    cuts.append([round(a * hop / sr, 4), round((a + cut) * hop / sr, 4)])
            i = j
        else:
            i += 1
    segs = [x[k * hop:(k + 1) * hop] for k in range(n) if keep[k]] + [x[n * hop:]]
    return np.concatenate(segs), cuts


def map_time(t: float, cuts: list[list[float]], tempo: float) -> float:
    """Where a moment of the source lands after the pause cuts and the tempo change."""
    removed = sum(max(0.0, min(t, b) - a) for a, b in cuts if a < t)
    return max(0.0, (t - removed) / (tempo or 1.0))


def pace_file(src: str | Path, dst: str | Path, *, tempo: float = 1.06, pause_keep: float = 0.8,
              min_pause: float = 0.25, threshold_db: float = -45.0, rb: dict | None = None) -> dict:
    """Shorten pauses, then change tempo with Rubber Band (R3, formants kept); 48 kHz / 24-bit out.
    Returns {cuts, tempo, before_s, after_s, rubberband}. A tempo of 1.0 needs no Rubber Band."""
    x, src_sr = audio_mod.load(src)
    y, cuts = shorten_pauses(x, src_sr, pause_keep=pause_keep, min_pause=min_pause, threshold_db=threshold_db)
    sr = src_sr
    if tempo != 1.0:
        rb = rb or rubberband_info()
        if rb["status"] == "missing":
            raise MissingPrerequisite(rb["note"], hint="Or pace pauses only with --no-tempo.")
        with tempfile.TemporaryDirectory(prefix="slate-pace-") as tmp:
            mid, stretched = Path(tmp) / "mid.wav", Path(tmp) / "out.wav"
            audio_mod.write_wav(mid, y, sr, 24)
            procs.run([rb["path"], "-3", "-F", "--tempo", f"{tempo}", "-q", str(mid), str(stretched)],
                      check=True, timeout=600)
            z, zsr = audio_mod.load(stretched)
            y, sr = audio_mod.resample(z, zsr, 48000), 48000
    else:
        y, sr = audio_mod.resample(y, sr, 48000), 48000
    audio_mod.write_wav(dst, y, sr, 24)
    return {"cuts": cuts, "tempo": tempo, "before_s": round(len(x) / float(src_sr), 3),
            "after_s": round(len(y) / float(sr), 3), "rubberband": rb if tempo != 1.0 else None}


_WARNED: set[str] = set()


def used_tempo_is_one(tempo: float, no_tempo: bool) -> bool:
    return no_tempo or tempo == 1.0


def pace_take(vf: VoiceFilm, shot: str, *, tempo: float | None = None, pause_keep: float | None = None,
              min_pause: float | None = None, threshold_db: float | None = None, no_tempo: bool = False) -> dict:
    """Pace the shot's selected take (its clean stage when there is one) into <take>.paced.wav, 48 kHz / 24-bit."""
    sel = vf.selected(shot)
    if not sel:
        raise MissingPrerequisite(f"{shot} has no take yet.", hint=f"slate voice tts --only {shot}")
    take, info = sel
    src_rel = info.get("clean") or info["raw"]
    src = vf.dir / src_rel
    prof = vf.profile.get("pace") or {}
    tempo = float(tempo if tempo is not None else prof.get("tempo", 1.06))
    pause_keep = float(pause_keep if pause_keep is not None else prof.get("pause_keep", 0.8))
    min_pause = float(min_pause if min_pause is not None else prof.get("min_pause", 0.25))
    threshold_db = float(threshold_db if threshold_db is not None else prof.get("threshold_db", -45.0))
    if not 0.5 <= tempo <= 2.0:
        raise UsageError(f"A tempo of {tempo} isn't pacing; keep it between 0.5 and 2 (1.06 is 6% quicker).")
    rb = rubberband_info()
    if not no_tempo and tempo != 1.0 and rb["status"] == "missing":
        raise MissingPrerequisite(rb["note"], hint="Or pace pauses only with --no-tempo.")
    if rb.get("rosetta") and not used_tempo_is_one(tempo, no_tempo) and rb["path"] not in _WARNED:
        _WARNED.add(rb["path"])
        log.warn(f"Rubber Band at {rb['path']} is an x86_64 build under Rosetta; arm64 is the target")
    out = src.with_name(f"{take}.paced.wav")
    used_tempo = 1.0 if no_tempo else tempo
    res = pace_file(src, out, tempo=used_tempo, pause_keep=pause_keep, min_pause=min_pause,
                    threshold_db=threshold_db, rb=rb)
    cuts, before, after = res["cuts"], res["before_s"], res["after_s"]
    stage = {"source": src_rel, "file": vf.rel(out), "sha256": jsonio.sha256(out), "tempo": used_tempo,
             "pause_keep": pause_keep, "min_pause": min_pause, "threshold_db": threshold_db, "cuts": cuts,
             "before_s": before, "after_s": after, "rubberband": res["rubberband"], "at": now()}
    data = vf.take_json(shot, take)
    data.setdefault("stages", {})["pace"] = stage
    vf.save_take_json(shot, take, data)
    vf.update_take(shot, take, paced=vf.rel(out), align=None)
    record(vf.dir, vf.rel(out), producer="slate voice pace", inputs={src_rel: jsonio.sha256(src)},
           tool_version=f"rubberband {rb.get('version')}" if used_tempo != 1.0 else None)
    words = len(vf.line(shot).text.split())
    return {"shot": shot, "take": take, "file": vf.rel(out), "before_s": round(before, 3), "after_s": round(after, 3),
            "shorter_pct": round((1 - after / before) * 100, 1) if before else 0.0,
            "wps_before": round(words / before, 2) if before else None,
            "wps_after": round(words / after, 2) if after else None, "tempo": used_tempo, "cuts": len(cuts),
            "rubberband": {k: rb.get(k) for k in ("path", "version", "arch", "status", "note")}}
