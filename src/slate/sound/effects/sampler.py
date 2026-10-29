"""Fixed-pitch playback of installed sampled instruments; no samples are copied or synthesized.

Zone selection, measured tuning and loop crossfades follow the approved sound proof. The EXS parser and library
discovery belong to slate.sound.instruments. Decoded samples and tuning measurements are cached only in memory.
The source sampler's Instrument.takes/cents_off/play and _xfade_loop are generalized here; its fixed source-project
cache and named instrument table become content-sensitive memory entries and palette roles respectively.
"""
from __future__ import annotations

import math
import re
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy import signal

from slate.errors import MissingPrerequisite, UsageError
from slate.sound import audio, instruments


def midi_of(note: str | float) -> float:
    if isinstance(note, (int, float)) and not isinstance(note, bool):
        value = float(note)
    else:
        match = re.fullmatch(r"([A-G])([#b]?)(-?\d+)", str(note))
        if not match:
            raise UsageError(f"Invalid musical pitch {note!r}; use a note such as C4 or a MIDI number.")
        letter, accidental, octave = match.groups()
        value = float(12 * (int(octave) + 1) + {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter]
                      + {"": 0, "#": 1, "b": -1}[accidental])
    if not math.isfinite(value) or not 0 <= value <= 127:
        raise UsageError(f"Musical pitch {note!r} is outside the supported MIDI range 0–127.")
    return value


def hz_of_midi(value: float) -> float:
    return 440.0 * 2 ** ((value - 69) / 12)


def loop_samples(x: np.ndarray, start: int, end: int, need: int, crossfade: int) -> np.ndarray:
    """Repeat the recorded loop using the proof's equal-power seams, retaining its original attack."""
    if not 0 <= start < end <= len(x) or end - start < 4:
        raise MissingPrerequisite("The sampled instrument has no usable sustain loop.", hint=instruments.DOWNLOAD)
    xf = max(1, min(int(crossfade), (end - start) // 4))
    out = x[:end].copy()
    loop = x[start:end]
    fade_in = np.sin(np.linspace(0, np.pi / 2, xf))[:, None]
    fade_out = np.cos(np.linspace(0, np.pi / 2, xf))[:, None]
    # Each join changes the preceding tail. Preallocation avoids repeatedly copying a long sustained note.
    result = np.empty((max(need, end), x.shape[1]), dtype=np.float64)
    result[:end] = out
    filled = end
    while filled < need:
        result[filled - xf:filled] = result[filled - xf:filled] * fade_out + loop[:xf] * fade_in
        count = min(len(loop) - xf, len(result) - filled)
        result[filled:filled + count] = loop[xf:xf + count]
        filled += count
    return result[:need]


class Instrument:
    """An installed tonal role, selected from a brand's palette. Missing content always fails closed."""

    def __init__(self, role: str, palette: dict | None = None):
        installed = instruments.require(role, palette)
        self.role = role
        self.path = Path(installed["path"])
        self.zones, self.groups, self.samples = instruments.parse_exs(self.path)
        self._audio: dict[tuple, tuple[np.ndarray, int]] = {}
        self._tuning: dict[tuple, float] = {}

    def group(self, zone: dict) -> dict:
        index = int(zone.get("group", -1))
        return self.groups[index] if 0 <= index < len(self.groups) else {}

    def takes(self, midi: float, velocity: int) -> list[dict]:
        key = int(round(midi))
        found = []
        for zone in self.zones:
            group = self.group(zone)
            low, high = group.get("vlo"), group.get("vhi")
            if low is not None and high is not None and high > 0 and not low <= velocity <= high:
                continue
            if (zone["klo"] <= key <= zone["khi"] and zone["vlo"] <= velocity <= zone["vhi"]
                    and "dummy" not in group.get("name", "").lower()):
                found.append(zone)
        return sorted(found, key=lambda z: (self.group(z).get("name", ""), z["root"]))

    def zone(self, midi: float, velocity: int, take: int = 0) -> dict:
        matches = self.takes(midi, velocity)
        if not matches:
            raise MissingPrerequisite(f"The {self.role} instrument has no sample for key {midi:g} at velocity {velocity}.",
                                      hint="Choose a pitch and velocity covered by the installed instrument's zones.")
        return matches[take % len(matches)]

    def sample_file(self, zone: dict) -> tuple[Path, int]:
        index = int(zone["sample"])
        if not 0 <= index < len(self.samples):
            raise MissingPrerequisite(f"The {self.role} instrument refers to a missing sample.", hint=instruments.DOWNLOAD)
        sample = self.samples[index]
        return Path(instruments._remap(sample["folder"])) / sample["file"], int(sample["rate"] or 44100)

    def audio(self, zone: dict) -> tuple[np.ndarray, int]:
        path, rate = self.sample_file(zone)
        try:
            stat = path.stat()
        except OSError as exc:
            raise MissingPrerequisite(f"The installed sample cannot be read: {path}", hint=instruments.DOWNLOAD) from exc
        key = (str(path), stat.st_size, stat.st_mtime_ns, rate)
        if key not in self._audio:
            self._audio[key] = (audio.load(path, rate), rate)
        return self._audio[key]

    def cents_off(self, zone: dict) -> float:
        """Measure the recording near its root note, then honor its EXS fine/coarse tuning."""
        path, rate = self.sample_file(zone)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, zone["start"], zone["root"], zone["lstart"], zone["lend"])
        if key not in self._tuning:
            x, _ = self.audio(zone)
            end = min(int(zone["end"] or len(x)), len(x))
            seg = x[int(zone["start"]):end].mean(axis=1)
            if zone["loop_on"] and zone["lend"] > zone["lstart"] + rate // 4:
                seg = x[int(zone["lstart"]):min(int(zone["lend"]), len(x))].mean(axis=1)
            elif len(seg):
                peak = int(np.argmax(np.abs(seg)))
                seg = seg[peak + int(0.03 * rate):peak + int(0.45 * rate)]
            seg = seg[:int(2.0 * rate)]
            if len(seg) < 8 or not np.any(seg):
                raise MissingPrerequisite(f"The {self.role} sample has no measurable sustained pitch: {path.name}.",
                                          hint="Use an installed, complete pitched instrument for this tonal role.")
            spectrum = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 19))
            frequencies = np.fft.rfftfreq(1 << 19, 1 / rate)
            nominal = hz_of_midi(zone["root"])
            indices = np.flatnonzero((frequencies > nominal * 2 ** (-70 / 1200))
                                     & (frequencies < nominal * 2 ** (70 / 1200)))
            if not len(indices):
                raise MissingPrerequisite("The sample's root pitch is outside its recorded bandwidth.")
            index = int(indices[np.argmax(spectrum[indices])])
            a, b, c = np.log(spectrum[index - 1:index + 2] + 1e-12)
            denominator = a - 2 * b + c
            correction = 0.0 if abs(denominator) < 1e-12 else 0.5 * (a - c) / denominator
            peak = frequencies[index] + np.clip(correction, -1, 1) * (frequencies[1] - frequencies[0])
            self._tuning[key] = round(float(1200 * np.log2(peak / nominal)), 2)
        return self._tuning[key] + zone["fine"] + 100 * zone["coarse"]

    def play(self, note: str | float, *, vel: int = 64, take: int = 0, dur: float | None = None,
             sustain: bool = False, xfade_s: float = 0.04, skip: str | float = 0.0,
             sample_rate: int = 48000) -> np.ndarray:
        midi = midi_of(note)
        if not 0 <= vel <= 127 or sample_rate <= 0 or (dur is not None and (not math.isfinite(dur) or dur <= 0)):
            raise UsageError("Sample playback needs a valid velocity, positive sample rate and positive duration.")
        zone = self.zone(midi, int(vel), int(take))
        x, rate = self.audio(zone)
        start, end = int(zone["start"]), min(int(zone["end"] or len(x)), len(x))
        if not 0 <= start < end:
            raise MissingPrerequisite(f"The {self.role} sample has an invalid playback range.")
        has_loop = bool(zone["loop_on"] and start <= zone["lstart"] < zone["lend"] <= end)
        if skip == "loop":
            if not has_loop:
                raise MissingPrerequisite(f"The {self.role} sampled pad has no steady sustain loop.", hint=instruments.DOWNLOAD)
            start = int(zone["lstart"])
        elif skip:
            start = min(start + int(float(skip) * rate), (int(zone["lstart"]) if has_loop and zone["lstart"] > start else end) - 1)
        speed = 2 ** ((midi - zone["root"]) / 12 - self.cents_off(zone) / 1200)
        segment = x[start:end]
        if dur is not None:
            need = int(dur * rate * speed) + rate // 10
            if sustain and has_loop:
                segment = loop_samples(x[start:end], int(zone["lstart"]) - start, int(zone["lend"]) - start,
                                       need, int(xfade_s * rate))
            elif sustain and need > len(segment):
                raise MissingPrerequisite(f"The {self.role} sample cannot sustain this duration without a loop.")
            else:
                segment = segment[:need]
        ratio = Fraction(sample_rate / (rate * speed)).limit_denominator(1000)
        y = signal.resample_poly(segment, ratio.numerator, ratio.denominator, axis=0)
        return y[:int(dur * sample_rate)] if dur is not None else y
