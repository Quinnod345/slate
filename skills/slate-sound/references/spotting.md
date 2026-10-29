# Spotting: from the picture's beats to sounds

Spotting decides what each moment sounds like. Synchronized effects start from the picture's beats and follow its
movement. The current brief can also call for off-screen sound, ambience, deliberate repetition or silence; record
their intended timing and source instead of treating visible cause as a universal creative rule.

## Where the beats come from

- `timing/visual_events/act<N>.json`: every beat that lands, with its kind, size, time and a note (slate-build). An act
  that's silent says so, with a reason.
- Captures' taps, beat-mapped onto the voice's words.
- A character's gestures, one row per note, on their contact frames.
- The score's own landings, from `sound/score/directions.json`.

Every sound's measured anchor sits on its beat's frame, within one frame: the onset of a strike, the peak of air or a
swell, or the end of a draw. A sound can start before its anchor; its peak or endpoint still serves the exact beat.

`slate sfx spot` builds `sound/sound_cues.json` from those events. It validates every expected act before placing a cue
and refuses malformed or missing event files. Each event keeps one logical cue, with count clicks represented as
individually measured notes. `t` is the picture contact; sustained draws and counts give `start` and `dur` and end on
`t`. Counts also give numeric `from` and `to`, and may set `hold_frames`. Prose is never parsed to guess those values.
Each event's seed stays stable across unrelated insertions. See [effects engine](effects-engine.md) for execution,
authored beds and preserving approved spans.

**The starting recipe sounds a repeating state once.** A thinking loop or pulse was silent after its first cycle.
A requested recurring sound can repeat under the chosen palette's rules; inspect its rhythm and narration balance.

**Tell the sound exactly what each beat is.** Each kind of interaction has its own family of sounds with several
variations, and that only works when every beat names its kind (and its size). A beat with a loose or wrong kind gets the
wrong sound; an unknown kind is reported, never guessed.

**Land the film's impact at the end of a sentence**, so its sound and its ripple play in the voice's pause.

## Moment patterns that worked

These are warm product-film patterns. Their materials, one-moment structure and silence are contextual choices; the
current palette and authorized listening feedback can choose another sound language.

- **Lists and step sequences** land on small notes rising up the scale, one per item.
- **State chips** flip on wood clacks pitched by meaning: positive bright, neutral middle, negative low; a negative state
  never sounds like an error buzzer.
- **Privacy and locks** sound reassuring: soft wood clacks, never metallic.
- **Stepping back in time** re-counts a number as a descending run of clicks, then rising again for now.
- **Answers streaming on screen** sit over a very low, soft texture; documents writing get a soft paper texture.
- **A sonic logo mirrors the logo build**: each construction line clicks in on a light woodblock, the fill is a soft
  swell, the mark lands on a hollow knock with one small note; the ending reverses it and lands on the same knock.
- **A brand's signature motif can have a sound**: a warm filtered tone born when the motif draws, splitting when it
  splits, resolving when the key value lands, and returning at the end on the same note. It never glides.
- **The widest moment opens the whole mix**: ambience, the motif's tone, a full pad, and the brand line landing per word
  on small notes.
- **One big sound moment per film**: the pad under the build-up stops dead for a held breath, then a sub, a pad bloom and
  a hollow knock land together.
- **In a held moment** the score drops to one pad and only one small sound plays, heard alone; text writing on during it
  stays under the pad or silent.

## Following the picture

- Envelopes, filter sweeps and swells run on the film's own named eases, so a whoosh peaks where the move peaks.
- Pads are space: low, slowly breathing chords that hold the harmony under whole passages and open up at the big
  moments.
- Keep harmony out of the words: under the voice, pads are low-passed (at or below about 1.4 kHz) and tonal beds sit
  about 20 LU under it.

## Silence

Plan silence as deliberately as sound: a silent opening second, a held breath before a hero moment, half a second before
a reveal, and the ending on the last frame. Planned stops are written down (score.md), so the gap check knows they're on
purpose.

## Carrying an approved proof onto the real picture

When adapting a storyboard proof to the real picture is within the authorized work, move it cue by cue:
- move every cue to the beat the picture really has, and remove cues for beats it doesn't have;
- re-voice tonal roles on the sampled instruments;
- trim a file that opens on silence, so its hit lands with the others;
- tie every change to one of the owner's own words about the proof.

Keep a source-bound approved clip unchanged as the baseline. A moved or altered result needs its own applicable
reference or delivered-file approval; permission to adapt does not make its new bytes approved.

## A spotting sheet

For a proof or a film, write a per-shot table (the shot, its time, its voice line, and what the sound does, with its
sources) generated from the script's sound notes and the events, so it can't drift from the film. The builders' score
cues in their NOTES.md (where the score should drop or thin) go into it.
