# Spotting: from the picture's beats to sounds

Spotting decides what each moment of the film sounds like. It starts from the picture's own beats, so every sound has a
visible cause, and it follows the picture's own eases, so sound moves the way the picture moves.

## Where the beats come from

- `timing/visual_events/act<N>.json`: every beat that lands, with its kind, size, time and a note (slate-build). An act
  that's silent says so, with a reason.
- Captures' taps, beat-mapped onto the voice's words.
- A character's gestures, one row per note, on their contact frames.
- The score's own landings, from `sound/score/directions.json`.

Every sound's attack sits on its beat's frame (for air and swells, the peak), within one frame. Sound never comes before
the picture.

**A repeating state sounds once.** A thinking loop or a pulse sounds on its first cycle only; later cycles are silent.

**Tell the sound exactly what each beat is.** Each kind of interaction has its own family of sounds with several
variations, and that only works when every beat names its kind (and its size). A beat with a loose or wrong kind gets the
wrong sound; an unknown kind is reported, never guessed.

**Land the film's impact at the end of a sentence**, so its sound and its ripple play in the voice's pause.

## Moment patterns that worked

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

A proof spotted on the storyboard moves onto the real picture cue by cue:
- move every cue to the beat the picture really has, and remove cues for beats it doesn't have;
- re-voice tonal roles on the sampled instruments;
- trim a file that opens on silence, so its hit lands with the others;
- tie every change to one of the owner's own words about the proof.

## A spotting sheet

For a proof or a film, write a per-shot table (the shot, its time, its voice line, and what the sound does, with its
sources) generated from the script's sound notes and the events, so it can't drift from the film. The builders' score
cues in their NOTES.md (where the score should drop or thin) go into it.
