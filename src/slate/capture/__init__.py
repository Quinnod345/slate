"""Captures: real app and web footage, conformed to the film's clock, with taps that land on the voice's words.

- `ios.capture(film_dir, spec) -> Path`: a pinned build on an isolated Simulator, a seeded demo account, Apple's
  status bar, a scripted recording (simctl), conformed and beat-mapped. Returns capture.json.
- `web.capture(film_dir, spec) -> Path`: a page through Aside at a fixed viewport, frame by frame on a virtual clock.
- `conform.to_sequence(src, film_dir, name) -> Path`: any recording → a JPEG sequence at the film's frame rate.
- `beatmap.run(film_dir, name)`: taps onto their words, visual events for the sound design, the beats gate.

Commands: `slate capture ios|web|conform|beatmap` (slate.capture.cli).
"""

from slate.capture import beatmap, conform, ios, spec, web  # noqa: F401  (cross-module interfaces)
