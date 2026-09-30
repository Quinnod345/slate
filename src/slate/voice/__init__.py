"""Voice: cast or design a voice, direct it with audio tags, voice every line, clean, pace, align and caption it.

The stages are `slate voice …` commands (see slate.voice.cli). Other modules call these (docs/ARCHITECTURE.md §12):

- `slate.voice.tags.strip(text) -> str` and `slate.voice.tags.check(tagged, line) -> list[str]`
- `slate.voice.profile.load(brand_pack) -> dict`
- `slate.voice.captions.build(film_dir) -> list[Path]`
- `slate.voice.gates.run(film_dir, names=None) -> list[GateResult]` and `slate.voice.gates.GATES`

The one ElevenLabs client is `slate.vendors.elevenlabs.Client`. Submodules import on demand, so `slate --help`
stays fast.
"""
