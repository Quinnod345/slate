"""Sound: the composed score, the mix, the checks, the meters, the instruments, and the effects engine (gated).

The interfaces other modules call (docs/ARCHITECTURE.md §12):

    from slate.sound import measure, mix, verify, effects
    measure.loudness(path) -> dict          # ITU-R BS.1770-4: integrated LUFS, true peak (4x), LRA, and more
    mix.run(film_dir) -> Path               # sound/mix/final_mix.wav, its four stems and final_mix.json
    verify.run(film_dir) -> list[GateResult]
    effects.require_proof3()                # raises Refused until a person has approved proof 3

What lives where:

    audio.py        WAV in and out natively (24-bit delivery), anything else through ffmpeg
    measure.py      the loudness meter, true peak, LRA; ffmpeg ebur128 as a cross-check
    dsp.py          equal-power joins, the voice follower, the limiter, freeze repairs, measured onsets
    film.py         a film's clock, targets, brand, palette and format, read once
    vo.py           the voice placed from timing/vo_cues.json, and found again
    gap.py          the music gap check (predicted at assembly, measured after the mix)
    mix.py          the delivery mix (the films' final_mix.py, generalized)
    checks.py       glides, drones, repeats, clarity: the measurements behind the gates
    verify.py       the sound gates
    instruments.py  Apple's sampled instruments for tonal roles: found in place, never shipped, never faked
    score/          plan, compose, qa, assemble (Eleven Music on its best model, assembled by measurement)
    effects/        the palette (proof 2's as the starting point) and the gated effects engine

Commands: `slate score plan|compose|assemble|qa`, `slate mix`, `slate verify`, `slate sfx`, `slate sound
instruments|loudness`. Films in the folder layout of docs/ARCHITECTURE.md §5; every file written is generated
(marked so) except sound/score/directions.json and sound/stems/stems.json, which a person or agent writes.
"""

__all__ = ["audio", "measure", "dsp", "film", "vo", "gap", "mix", "checks", "verify", "instruments", "score", "effects"]
