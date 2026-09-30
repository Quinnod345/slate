"""Split a reference film's soundtrack into voice and music with Demucs (htdemucs, two stems), the way the films did.

Demucs is part of the optional analyze extra and runs through uvx, pinned (demucs 4.1.0 on Python 3.11, with numpy
below 2 and soundfile), so nothing is installed into Slate's own environment or globally. It runs offline by
default: uvx uses only its cache, and the htdemucs weights must already be in torch's cache. When either is missing
the step is UNCHECKED with the one command that fixes it; `--allow-download` lets uvx and Demucs fetch what they need
(about a gigabyte of Python packages the first time, and about 80 MB of weights).
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Callable

from slate import procs
from slate.errors import MissingPrerequisite, SlateError
from slate.gates import GateResult, engine as gate_engine

DEMUCS = "demucs==4.1.0"
PYTHON = "3.11"
WITH = ("numpy<2", "soundfile")
MODEL = "htdemucs"
# htdemucs's weights for demucs 4.1.0 (demucs/remote/files.txt), fetched by torch.hub into its checkpoints folder.
CHECKPOINT = "955717e8-8726e21a.th"
TIMEOUT_PER_AUDIO_SECOND = 6.0


def torch_checkpoints() -> Path:
    home = os.environ.get("TORCH_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".cache" / "torch"
    return base / "hub" / "checkpoints"


def weights_cached() -> bool:
    return (torch_checkpoints() / CHECKPOINT).is_file()


def command(wav: Path, out_dir: Path, *, offline: bool = True) -> list[str]:
    argv = ["uvx"]
    if offline:
        argv.append("--offline")
    argv += ["--python", PYTHON, "--from", DEMUCS]
    for pkg in WITH:
        argv += ["--with", pkg]
    return argv + ["demucs", "--two-stems=vocals", "-n", MODEL, "-d", "cpu", "-o", str(out_dir), str(wav)]


def _lock():
    try:
        from slate.jobs import locks  # noqa: PLC0415
    except ImportError:
        return contextlib.nullcontext()
    # Demucs is a heavy local model like Whisper; sharing Whisper's slot keeps two of them from running at once.
    return locks.hold("whisper", owner="slate analyze (demucs)")


def _loudness(path: Path) -> dict | None:
    try:
        from slate.sound import measure  # noqa: PLC0415

        m = measure.loudness(path)
    except Exception:  # noqa: BLE001 - the stems are still useful without their levels
        return None
    return {"integrated_lufs": m.get("integrated_lufs"), "true_peak_dbtp": m.get("true_peak_dbtp"),
            "lra_lu": m.get("lra_lu")}


def run(wav: Path, out_dir: Path, *, duration_s: float, allow_download: bool = False,
        runner: Callable[..., object] = procs.run) -> tuple[GateResult, dict]:
    """Split `wav` into vocals.wav and no_vocals.wav under out_dir/htdemucs/<name>/. UNCHECKED when Demucs can't run."""
    engine = gate_engine("demucs", DEMUCS.split("==")[1], model=MODEL, via="uvx", python=PYTHON)
    if not procs.which("uvx"):
        return GateResult.unchecked("analyze.stems", "no stem split: uv (uvx) isn't installed",
                                    evidence=["next: slate doctor --install (installs uv, asking first)"],
                                    engine=engine), {}
    if not allow_download and not weights_cached():
        return GateResult.unchecked(
            "analyze.stems", "no stem split: Demucs's htdemucs weights (about 80 MB) aren't in torch's cache yet",
            evidence=["next: run the same slate analyze with --stems --allow-download once; the weights stay cached",
                      f"looked for {torch_checkpoints() / CHECKPOINT}"], engine=engine), {}
    argv = command(wav, out_dir, offline=not allow_download)
    try:
        with _lock():
            proc = runner(argv, timeout=max(600.0, TIMEOUT_PER_AUDIO_SECOND * duration_s))
    except MissingPrerequisite as exc:
        return GateResult.unchecked("analyze.stems", f"no stem split: {exc.message}", engine=engine), {}
    except SlateError as exc:
        return GateResult.unchecked("analyze.stems", f"Demucs didn't finish: {exc.message}", engine=engine), {}
    folder = out_dir / MODEL / wav.stem
    vocals, music = folder / "vocals.wav", folder / "no_vocals.wav"
    if not (vocals.is_file() and music.is_file()):
        tail = ""
        if getattr(proc, "returncode", 0):
            err = getattr(proc, "stderr", "") or ""
            tail = err.strip().splitlines()[-1] if err.strip() else ""
        hint = ("uv's cache doesn't hold Demucs yet: add --allow-download once" if "offline" in tail.lower()
                or "no solution" in tail.lower() else tail)
        return GateResult.unchecked("analyze.stems", "Demucs ran but wrote no stems" + (f" ({hint})" if hint else ""),
                                    evidence=[" ".join(argv)], engine=engine), {}
    def rel(path: Path) -> str:  # stems are recorded relative to the analysis folder
        return str(path.relative_to(out_dir.parent))

    data = {"model": MODEL, "vocals": rel(vocals), "music": rel(music),
            "levels": {"vocals": _loudness(vocals), "music": _loudness(music)}, "command": argv}
    return GateResult.pass_("analyze.stems", "split into voice and music (htdemucs, two stems)",
                            evidence=[data["vocals"], data["music"]], engine=engine), data
