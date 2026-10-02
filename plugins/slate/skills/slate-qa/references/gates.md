# The gates

Every gate returns a result with a state (PASS, FAIL, REVIEW, UNCHECKED, STALE), a one-line summary, its evidence
(paths and measurements), the engine and version it used, and the hashes of its inputs. Only PASS counts.

## Script (stage 3): `slate script build`

`script.schema`, `script.structure`, `script.pace` (2.6 words a second or less per shot), `script.read_time`,
`script.on_screen` (12 words or fewer, and at most 40% of the words the voice says in the shot), `script.hero_lines`
(6 words or fewer, at most two per act), `script.runtime` (within 10% of the target), `script.banned_words` (and, when
the brand's `truth.self_commentary` is false, "Illustration" and "Example" captions on screen), `script.claims`,
`script.numbers`, `script.quotes` (a quoted product fact is re-read at its pinned commit), `script.sources`,
`script.first_line`, `script.vo_dashes`, `script.shot_ids`. A failing build writes nothing (slate-story).

## Voice (stage 4): the voice checks

Policy (the voice may be used), direction (no banned tag; tagged lines strip back to the script), takes (every line has
one), words (Whisper hears every word and every brand word in full, no repeats, no tag spoken), tails (nothing leaking
from the next line), fit (each read fits its shot). Replayed takes are UNCHECKED (slate-voice).

## Timing (stage 5): `slate timing`

`timing.frames` (frame-exact and contiguous: nothing is written unless it passes), `timing.reads`, `timing.words`,
`timing.events`. `slate timing misses` must be empty at hand-in.

## Hand-in (stage 7): `slate check <act>`

Type check, module load, word misses, kit problems, banned words, never-claims, frame literals, timing margins and
the rig lint, the character's acceptance checks, events, contracts, fonts, and each web shot of a Remotion act; with
`--markup` the act's static markup at every frame (against a git ref or a kit folder with `--against`), and with
`--determinism` frames rendered alone against the same frames in sequence (slate-build).

## Generation and captures (stages 7 and 8)

A generated shot's takes are REVIEW until a person picks one; a capture's taps must land on their words.

## Sound (stage 9): `slate verify`

`sound.loudness`, `sound.stems_sum`, `sound.gap`, `sound.voice_sync`, `sound.cue_sync`, `sound.repeats` (a sonic logo
closing the film as it opened is a bookend, not a repeat), `sound.glides`, `sound.drone`, `sound.levels`,
`sound.reference` (with the effects' brightness against the reference), `sound.clarity`, `sound.music_licence`,
`sound.voice_limiter` (the voice's limiter touches under 5% of the speech), `sound.key` (notes on the brand key's
pentatonic), `sound.timbre` (no chime tails, nothing metallic), `sound.mix_words` (Whisper on the mix, word for word),
and the balance gates, `sound.balance.approved_spans` and `sound.planned_stops` among them (slate-sound).

## The film (stages 10 to 12): `slate qa`

| Gate | Checks |
|---|---|
| `schema` | every film data file validates against its schema (a file with no schema yet is UNCHECKED, not passed) |
| `frames` | shots contiguous on the film's clock; every act render and master exactly as long as it should be, at the film's size and rate; the mix as long as the picture |
| `events_complete` | every act has a valid events file, or an explicit silence with a reason; every beat agrees with its span (a draw or count ends on its beat) |
| `sync` | cues within a frame of their beats, takes exactly on their cues, word-anchored beats on their words, a character's gestures on or before their stressed syllable (never late), every act covered |
| `loudness` | integrated loudness on target, true peak under the ceiling (ffmpeg's EBU R128 meter) |
| `bit_depth` | the mix and every stem are integer PCM at the delivery depth and rate (a 16-bit or float mix fails) |
| `silent_stems` | no stem is silent |
| `stems_sum` | the stems add up to the mix, sample for sample |
| `captions` | one caption rule (the law's 2 lines of 42 characters, 17 characters a second, at least 1 s), tightened by the format at the film's aspect and by film.json, never loosened; ordered, never overlapping, word for word |
| `claims` | every claim approved or sourced; autonomous films use approved claims only |
| `banned_words` | the brand's one list (every form of each entry, naming its substitute) against the script, the captions, the OCR of the picture and what Whisper heard in every take in use; self-commentary captions on screen when the brand forbids them |
| `ocr` | Apple Vision reads the whole picture every 0.25 s; UNCHECKED (never another engine) when it can't |
| `readtime` | every on-screen statement readable for max(1.5 s, 0.4 s + 0.3 s per word) once fully built, counting only frames where it's at least 90% opaque and moving under 8 px a frame; a shot's declared `reading` (below) is honoured |
| `glitch` | one- and two-frame glitches in every act render, triaged against planned beats; flags that block matching explains as motion or a fade are listed, not sent to a person; runs of blank frames between pictures are flagged |
| `boundary` | every join between consecutive act renders: contiguous, no bigger a jump than the picture's own motion either side |
| `cuts` | every scene change sits on a shot start whose declared transition allows a cut (a carry, portal, mask, whip, UI cut, at most three music cuts, or a seam of 4 frames or less); no hard cut or crossfade at a join the transition doesn't declare |
| `psnr` | the master against the act renders: at least 40 dB on every frame, every frame there |
| `end_hold` | the master's last seconds (3 s unless the film or its format says otherwise; none for a seamless loop) are pixel-identical frames |
| `motion` | the brand's eases, durations and stagger caps; entrances from 85% scale; a hero line also moves 16 px or unmasks; type never arrives while a carry moves faster than about 2 px a frame; speed and acceleration limits; no fast loops; symmetric mark rigs |
| `access` | at most 3 flashes a second; the brand's text colour pairs at 4.5:1 (body) and 3:1 (large); caption rate; 9:16 safe zones by OCR; text inside title-safe (90% of the frame); body copy at least 28 px at 1080 lines, by its measured cap height |
| `stability` | every act's render free of pops, whole-pixel snaps, shakes, reversals, wobbles, flicker and shimmer, triaged against planned beats, a reference render and the act's accepted baseline (a reversal always fails) |

Sound's own gates (`sound.*`, the balance gates among them) are `slate verify`'s; they're recorded in the same
`qa/gates.json`, `slate qa triage` shows them, and a delivery with sound needs them to pass.

## Declared reading

Some text isn't meant to be read the usual way, and a shot says so in script/script.json, with its reason:

```json
{"id": "S07", "on_screen": "…", "reading": {"voice_matched": true, "reason": "the voice reads the definition as it lands"}}
{"id": "S19", "on_screen": "…", "reading": {"not_for_reading": ["Q3 detail table"], "reason": "the report sits small as a closing object"}}
{"id": "L02", "on_screen": "…", "reading": {"labels": true, "reason": "the app's own screen title, shown in the Simulator"}}
```

- **voice_matched**: a definition the voice reads along as it lands. It may run past the on-screen word budget and
  needs only the 1.5 s floor.
- **not_for_reading**: a surface shown too small to read on purpose. Its listed statements (or the whole shot, with
  `true`) aren't timed; its large elements still must read.
- **labels**: the on-screen words name what the picture shows (a product's UI, a Simulator label, an end card), not a
  second script. It means the same to the script gates and to `slate qa readtime`: it answers the 40% voice-share rule
  only, so the words still count toward the 12-word cap and are timed in full. It's `true` for the whole shot.

A declaration without a reason, or naming text the shot doesn't show, is reported as a problem, never a quiet pass.

## One OCR engine

OCR gates use Apple Vision only. When it's unavailable the gate is UNCHECKED: a second engine would silently change the
results. Logo lockups and stylized marks go to the contact-sheet review (OCR misreads them).

## Validators read the film they validate

Every gate takes the film folder and reads that film's own files: on the first films a validator once read another
film's captions and cue sheets. A read-time checker needs the text as actually drawn: most of one film's read-time
failures were the checker, not the film.

## Trusting a new metric

Before a new measurement gates anything, check it against the owner's ear or eye on takes or shots they've already
judged. The voice's breathiness measures were validated that way before they rejected anything.

## Stale gates

A result records the hashes of what it read. When any input changes, the result turns STALE, and `slate status` places
the stale gate on its stage. Re-run it; never carry a PASS across a change.
