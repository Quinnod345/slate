# Visual events: telling the sound what each beat is

Sound is designed from the picture's own beats. Each kind of interaction gets its own family of sounds, with natural
variation between repeats, and that only works when every beat says exactly what it is. Loose kinds ("click" used for a
press, a check, a vertex and a row) made consistent sound impossible on the first film; a strict vocabulary fixed it.

## The shape

`timing/visual_events/act<N>.json`, written by `slate emit-events` from the act's scenes:

```json
[{"shot": "S12", "id": "seal_sets", "t": 41.28, "kind": "stamp", "size": "l", "note": "the seal presses into the page"},
 {"shot": "S13", "id": "trend_draws", "t": 44.10, "start": 43.30, "dur": 0.80, "kind": "draw", "size": "m",
  "note": "the trend line draws"}]
```

- `t` is always **the beat**: the film second where the motion lands (its contact), taken from the same beat constant
  the picture uses. Never the start.
- A sustained motion also gives its **span**: `start` (film seconds) and `dur` (seconds), always together.
  - A `draw` and a `count` need their span, and they **end on their beat**: `t = start + dur` (within half a frame).
    A count runs `from` → `to` and reaches `to` at `t`.
  - A `whoosh` and a `swell` **peak on their beat**, somewhere inside their span.
  - Any other kind may give a span too; its beat sits inside it.
  The films timed one sustained draw at its start while the sound handover read a draw as ending on `t`; spans make
  it explicit, and `slate.timing.beats.problems` checks the arithmetic the same way for every engine and reader.
- A capture's beats carry `"source": "capture:<name>"`; a captured draw or count needs its span like any other.
- `id` is unique in the act and says what it is.
- `note` is what the viewer sees, in plain words.
- An act with no beats says so, `{"silent": true, "reason": "…"}`, because "no file" and "no beats" look the same to a
  sound engine and only one of them is a decision.

## The kinds

| Kind | Use it for | Example |
|---|---|---|
| `tap` | a press or a choice: a button, a chip, a menu pick | "Continue" pressed |
| `type` | text writing on (a line or a word) | the question writes on |
| `land` | a card, panel, sheet or document settling into place | the report swings up and lands |
| `snap` | a state change: a chip flipping, a check, a toggle, a node lighting | node 2 lights |
| `count` | a number counting (give `from` and `to`, and the landing) | 2% counts to 23% |
| `draw` | a line or chart drawing | the trend line draws |
| `data` | a single data point arriving (dots, bars, points) | a dot lands on the trend |
| `page` | paper moving as paper: a stack fanning, a page turning, a sheet sliding | the research fans out |
| `stamp` | a seal or stamp pressing down; a group completing | the seal sets |
| `whoosh` | a camera move, a carry, a transition | the camera pushes in |
| `swell` | a chapter opening, a ground rising, the picture filling | the next chapter opens |
| `character` | the brand's character or mark gesturing (one row per note) | the mark nods |
| `impact` | the one big landing of a scene: rare, at most one per act | the key value lands |

`size`: `s` a small detail, `m` normal, `l` a moment.

## Rules

- **List every beat that lands**: checks, chips, cards, counts, whips. Sound locked to the picture is a signature the
  owner loves; a beat that isn't listed gets no sound.
- **One impact per act, at most.** Land it at the end of a sentence, so its sound plays in the voice's pause.
- **Character rows come from the rig** (`motifBeats()`): one row per note, each on its own whole contact frame. The
  sound engine adds no offsets of its own.
- **Captures' taps** become beats too: `slate capture ios` and `slate capture web` beat-map a capture's taps onto the
  voice's words and write them as events.
- **Score cues** (where the score should drop to one pad, or thin under a heavy line) go in the act's NOTES.md for the
  sound pass; they aren't events.

## Checking

- `slate emit-events --act <N>` writes the file from the act and validates it: kinds, sizes, the act's span, unique
  ids, at most one impact, and every beat's own span (t at a draw's end, inside a whoosh's span).
- `slate check <N>` fails the act when its events file is missing or no longer matches what the act says now.
- `slate qa events_complete` fails the film if any act has neither beats nor an explicit silence, or if a beat
  disagrees with its span.
