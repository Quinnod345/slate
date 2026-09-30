"""The composed score: Eleven Music on its best model, composed per act and assembled by measurement.

A score is part of most films. The playbook comes from the films (their v2 pass, kept as a record), and every rule in
it was learned from a take that went wrong:

- Eleven Music ignores edges: every take fades in (0.5-3 s) and out (2-4 s) whatever the plan says. So every part is
  composed with a 3-second pre-roll that assembly cuts away, and runs at least 3 seconds past its hand-over.
- Plans longer than about 90 seconds run out of material early, so every part is under 90 seconds; long acts split.
- Sections are at least 3 seconds (the API's minimum), and a moment that has to land exactly is a part boundary, never
  a section inside a take.
- Takes are placed by what they measurably do (their entry, their onsets, where their music ends), never by the plan's
  arithmetic alone.
- Joins between parts are equal power (sine and cosine amplitudes); squared gains dip 3 dB between different takes.
- A hole is closed in place by holding the previous part's own chord (a phase-vocoder freeze), never by moving parts,
  which cascades into the next seam.
- The gap check runs on the music as the mix will set it: no window under -60 dBFS outside the planned stops.
- The score never plays the effects' materials: the palette's materials go in every plan's negative styles.
- The model is always passed explicitly (the API's default is an older model), and it's the best one in registry.json.
- Broadcast use (film, TV, radio) needs Eleven Music's Enterprise licence, and a broadcast format says so loudly.

Stages: `slate score plan` → `slate score compose` (spends, through the ledger; replayed in tests) →
`slate score assemble` → `slate score qa`.
"""
