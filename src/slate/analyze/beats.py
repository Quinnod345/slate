"""The music's tempo and beat grid, read with librosa's beat tracker (the films read about 117.5 BPM this way).

librosa is part of the optional analyze extra and runs through uvx, pinned, offline by default, like Demucs. It reads
the music stem when Demucs split one (a voice over the music confuses a beat tracker) and the whole mix otherwise,
and says which. UNCHECKED when uvx or a cached librosa isn't there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from slate import procs
from slate.errors import MissingPrerequisite, SlateError
from slate.gates import GateResult, engine as gate_engine

LIBROSA = "librosa==0.11.0"
PYTHON = "3.11"
WITH = ("soundfile",)

SCRIPT = """
import json, sys
import numpy as np
import librosa
y, sr = librosa.load(sys.argv[1], sr=22050, mono=True)
tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
b = [round(float(x), 4) for x in np.atleast_1d(beats)]
iv = np.diff(b) if len(b) > 1 else np.array([])
print(json.dumps({"tempo_bpm": round(float(np.atleast_1d(tempo)[0]), 2), "beats": b,
                  "interval_mean_s": round(float(iv.mean()), 4) if iv.size else None,
                  "interval_std_s": round(float(iv.std()), 4) if iv.size else None,
                  "librosa": librosa.__version__}))
"""


def command(wav: Path, *, offline: bool = True) -> list[str]:
    argv = ["uvx"]
    if offline:
        argv.append("--offline")
    argv += ["--python", PYTHON, "--with", LIBROSA]
    for pkg in WITH:
        argv += ["--with", pkg]
    return argv + ["python", "-c", SCRIPT, str(wav)]


def run(wav: Path, *, source: str, allow_download: bool = False,
        runner: Callable[..., object] = procs.run) -> tuple[GateResult, dict]:
    engine = gate_engine("librosa beat_track", LIBROSA.split("==")[1], via="uvx", python=PYTHON)
    if not procs.which("uvx"):
        return GateResult.unchecked("analyze.beats", "no beat read: uv (uvx) isn't installed",
                                    evidence=["next: slate doctor --install (installs uv, asking first)"],
                                    engine=engine), {}
    argv = command(wav, offline=not allow_download)
    try:
        proc = runner(argv, timeout=900)
    except (MissingPrerequisite, SlateError) as exc:
        return GateResult.unchecked("analyze.beats", f"no beat read: {exc.message}", engine=engine), {}
    out = (getattr(proc, "stdout", "") or "").strip().splitlines()
    try:
        data = json.loads(out[-1]) if out else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or data.get("tempo_bpm") is None:
        err = (getattr(proc, "stderr", "") or "").strip().splitlines()
        why = err[-1] if err else "no output"
        hint = "uv's cache doesn't hold librosa yet: add --allow-download once" if "offline" in why.lower() else why
        return GateResult.unchecked("analyze.beats", f"librosa didn't read a tempo ({hint})", engine=engine), {}
    data["source"] = source
    # librosa's global tempo is quantized to its tempo bins (a steady 120 BPM click reads 117.45); a steady beat grid
    # measures the tempo directly, so it leads when there is one.
    mean, std = data.get("interval_mean_s"), data.get("interval_std_s")
    steady = bool(mean and std is not None and len(data["beats"]) >= 4 and std <= 0.05 * mean)
    data["tempo_from_beats_bpm"] = round(60.0 / mean, 2) if mean else None
    data["steady"] = steady
    bpm = data["tempo_from_beats_bpm"] if steady else data["tempo_bpm"]
    how = f"from a steady beat grid; librosa's estimate {data['tempo_bpm']:g}" if steady else "librosa's estimate"
    return GateResult.pass_("analyze.beats", f"about {bpm:g} BPM ({how}; {len(data['beats'])} beats on the {source})",
                            evidence=[source], engine=engine), data
