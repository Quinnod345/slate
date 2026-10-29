# The designed effects engine

The engine turns the picture's events into measured cues, then balanced effect stems. It carries the approved sound
design: wood, air, subs, sampled felt notes and sampled pads, with natural variation and the voice always understood.
Its runtime gate still requires a person's approval of the exact proof file; a changed or revoked proof closes it.

## Commands

```
slate sfx status                 # approval state; no film required
slate sfx spot --seed 7          # picture events to sound/sound_cues.json; no stem render
slate sfx render                 # existing cues to sound/stems/ and stems.json
slate sfx run                    # spot, then render (also the default for slate sfx)
slate sfx run --dry-run          # inspect the work; no decoding, locks, writes or queued job
slate sfx run --detach           # keep working through the job queue in the render slot
```

The seed controls variation. It is combined with each event's stable identity, so inserting an earlier tap does not
reroll the rest of the film. A cue records its voice parameters, sample take, gain and measured anchor. The sheet
records the film clock and hashes of its events, palette, brand sound/motion, timing and authored spans.

Every expected act must have valid `timing/visual_events/act<N>.json`. Missing, unreadable or malformed files stop with
a per-file report. An act without sound says `{"silent": true, "reason": "A held frame."}`. An empty array is not that
declaration. A derived film reads inherited events in place and writes its new cues and stems only in the child.

One interaction becomes one logical cue. A count carries individual measured clicks in `notes`; its clicks follow the
explicit start, duration, value change and frame hold. A stamp or impact can contain several material layers within
one cue. Sampled swishes vary their rate and are measured again after variation. Composite air layers align their
peaks before the combined cue is placed.

## Palette and instruments

Each `sound.palette.kinds.<kind>` supplies its material family, processing family, stem, anchor, rendering type, gain
and params. The defaults use installed sampled swishes, wood voices, air, subs and sampled tonal instruments. Musical
notes come from the brand's key or explicit pitches; the character's motif must be supplied. Named eases resolve
through the brand's motion law and are pinned as curve data in the cue. See [palette](palette.md).

Text write-ons use `start`: their first audible sound on a 2 ms RMS envelope, 40 dB below its peak, lands on the
picture beat. The louder middle of the air does not pull the sound ahead of the writing.

For an authored exception, store the choice in `film.json` `sound.event_overrides`, keyed by both shot and event:

```json
{"sound": {"event_overrides": [
  {"shot": "S01", "event": "panel-arrival", "params": {"kind": "temple", "pitch": "A4"},
   "gain": -30, "balance_family": "accents"}
]}}
```

Use the film's own identity and musical pitch. Each entry may set `type`, `sound`, `balance_family`, `stem`, `anchor`,
`params`, `gain` and `pan`. Those values merge into that event's palette kind; nested params merge as well. Rendering
types must be supported cue voices, families must be `accents`, `textures`, `swells` or `impacts`, and stems must be
`ui`, `fx`, `tonal` or `amb`. Anchors must be measurable. Timing, cue routing and approval metadata cannot be set
through an override. The picture still owns motion duration and easing; seeded variation remains reproducible.

These film choices survive re-spotting and record their hash on the generated cue. A missing or duplicate identity,
unknown field, unsupported family or nonfinite value refuses before rendering. An override inside a pinned approved
span also refuses: approve the revised sound before pinning its reference. Overrides keep the palette's verification
rules, including one sound per kind; a new sound name does not exempt the cue from those checks.

Samples stay in their installed libraries. Missing files or tonal instruments raise a prerequisite error with a next
step. The engine never downloads, copies or substitutes them with synthesis.

## Authored beds and composed scores

Most films use a composed score. In score mode, bed spans document permitted sound but do not generate continuous pad
beds. To author sampled beds, set `film.json` `sound.music.kind` to `bed` and provide `sound.beds` entries:

```json
{
  "id": "opening-pad",
  "from": 0.5,
  "to": 4.5,
  "why": "The opening holds this chord before the next passage.",
  "gain": -31,
  "params": {"notes": ["A3", "C4", "E4"], "vel": 35, "attack": 0.4, "release": 0.5}
}
```

Those notes are an example; use the brand's authored chord. Notes are required: a span alone cannot invent its harmony.
The voice is sampled `spad`. Same-chord overlaps use a measured 0.3-second handoff at equal energy, with gain knots on
the film clock. A bed between two handoffs keeps both envelopes. Planned silences and beds survive re-spotting.

## Preserve approved spans

`sound.music.match` entries name a film-relative approved reference bundle with `clip`, `from`, `to`, `core`,
`pin_cues` and `pin_rides`. Its folder must have a current hash-bound `sound_proof` approval and contain
`cue_sheet.json` and `balance_used.json`; matching voice and score files belong in `family_stems/` inside that bundle.

Pinned cues retain exact timing, gains, seeds, pitches and reverb. Approved legacy layers remain separate; regrouping
them would change the approved sound. A layer whose event differs from its picture interaction supplies an explicit
`visual_event` owner. Deleted or moved interactions refuse, and spans are never relocated automatically. Reapprove
the changed sound when the picture changes. The reference loader rechecks its approval before and after reading it.

Keep `render_log.json` in the approved bundle. Its dry waveform fingerprints bind sampled instruments and named
motion curves to the approved sound, at the same sample rate. A changed waveform refuses even when its cue fields
match. Legacy bundles without a log can preserve literal synthesis and built-in curves; unresolved sampled roles,
recordings and brand curves require approved render evidence. Slate never guesses the missing source context.

After rendering, run `slate mix` and `slate verify`. Rendering produces provisional balanced stems; the final mix
recalculates its guards against the current delivered voice and score. Only passing gates establish delivery quality.
