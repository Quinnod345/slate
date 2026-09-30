"""The film clock: one clock for picture, voice, sound and captions.

This reproduces the films' own frame maths exactly (FILMROOT/pipeline/tools/make_shots.py), because golden-data parity
depends on it: seconds -> frame is `int(round(seconds * fps))` with fps as a float, and frame -> seconds is
`frame * fps_den / fps_num`. Shot boundaries are rounded from *cumulative* seconds, so no error accumulates.

Python resolves every frame. The Node engine and the ExtendScript kit only read the resolved JSON; they never
re-derive boundaries, which is what keeps three engines on one clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Clock:
    fps_num: int = 30000
    fps_den: int = 1001

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den

    def frame_of(self, seconds: float) -> int:
        """The nearest frame to a time in seconds (same rounding as the delivered films)."""
        return int(round(seconds * self.fps))

    def secs_of(self, frame: int) -> float:
        """The exact start time of a frame."""
        return frame * self.fps_den / self.fps_num

    def boundaries(self, durations: Iterable[float], start: float = 0.0) -> list[tuple[int, int]]:
        """Drift-free (frame_in, frame_out) for consecutive segments of the given lengths in seconds."""
        out: list[tuple[int, int]] = []
        t = start
        for d in durations:
            f_in, f_out = self.frame_of(t), self.frame_of(t + float(d))
            out.append((f_in, f_out))
            t += float(d)
        return out

    def timecode(self, frame: int) -> str:
        """A display timecode (HH:MM:SS:FF, non-drop counting on the nominal rate) for burn-ins and notes."""
        nominal = round(self.fps)
        ff = frame % nominal
        total_s = frame // nominal
        return f"{total_s // 3600:02d}:{total_s // 60 % 60:02d}:{total_s % 60:02d}:{ff:02d}"

    def frame_of_timecode(self, tc: str) -> int:
        """Inverse of `timecode` (accepts HH:MM:SS:FF, MM:SS:FF, MM:SS or plain seconds)."""
        parts = tc.strip().split(":")
        nominal = round(self.fps)
        if len(parts) == 1:
            return self.frame_of(float(parts[0]))
        if len(parts) == 2:
            return self.frame_of(int(parts[0]) * 60 + float(parts[1]))
        if len(parts) == 3:
            h, m, rest = 0, int(parts[0]), parts[1:]
        else:
            h, m, rest = int(parts[0]), int(parts[1]), parts[2:]
        s, ff = int(rest[0]), int(rest[1])
        return ((h * 3600 + m * 60 + s) * nominal) + ff

    def to_dict(self) -> dict:
        return {"fps_num": self.fps_num, "fps_den": self.fps_den}


def from_film(film: dict) -> Clock:
    """The clock a film.json (or shots.json) declares."""
    return Clock(int(film.get("fps_num", 30000)), int(film.get("fps_den", 1001)))
