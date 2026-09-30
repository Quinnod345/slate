---
name: slate-story
description: Write and gate a Slate film's story. Covers the treatment (the idea, the acts, one signature moment per act, the visionary bar), the truth pack of pinned sources and approved claims, the script as data in script/script.json (the only hand-edited script file), voice-over lines paced at 2.6 words a second or less, on-screen words that stay readable, and rebuilding the film's clock from the voice's real reads. Use when a Slate film needs its idea, treatment, script, claims, on-screen text or VO copy written or revised, when a script gate fails, or when the timing needs building after voicing.
---

# slate-story

The story is data. One hand-edited file, `script/script.json`, holds every act and shot: what the voice says, what's on
screen, what the picture does, and where every claim comes from. `slate script build` checks it through the script
gates and writes the storyboard and a readable SCRIPT.md, and it refuses to write anything while a gate fails. Nothing
downstream is typed by hand: shot lengths, timecodes and frames all come from this file and the voice's real reads.

This skill covers stages 2 (truth), 3 (treatment and script) and 5 (timing) of the production. The front door (the
slate skill) has already made the film folder and the brief.

## 1. Truth first

Before a line is written, know what's true and who says so.
- The **brand's truth** (from onboarding): its claim authority, sources, approved claims, never-claim list, banned
  words and sample data.
- The **film's truth pack**, `brief/truth.json`: the sources this film relies on, each pinned (a commit, a retrieval
  date or a hash), the researched claims that cite them, what this film never claims, and its sample data.
- The product's own code, read in place at a pinned commit, is the only product truth: it wins over docs, demos,
  prototypes and older films. The owner's rulings win over the code for what's claimed.

Every claim and every number said or shown must be on the brand's approved list or cite a pinned source. Autonomous
films use approved claims only. Research method and templates: [truth](references/truth.md). The rules every frame and
line obeys: [truth packs](references/truth-packs.md).

## 2. The treatment

For anything longer than a short ad, write a treatment before the script: the idea as a reframe, why this runtime, act
by act, the emotional arc, one signature moment per act, the voice and the look and the sound in a line each, what the
film never shows, and the owner's calls so far. Keep it to a page or two. Template and craft:
[treatment](references/treatment.md); story shapes that work: [structure](references/structure.md).

## 3. The script as data

Write `script/script.json`: acts of shots, each with an id, the VO line, the on-screen words, the picture, the
transition out, and its sources and claims. Shape, fields and a worked example: [script as data](references/script-as-data.md).

```
slate script build --check      # the gates only; writes nothing
slate script build              # gates, then script/storyboard.json and script/SCRIPT.md
```

The gates: schema, structure, pace (2.6 words a second or less, per shot), read time, on-screen density, runtime
(within 10% of the target), banned words (VO, on screen and picture), claims, numbers, quotes (no invented testimonial),
sources, and the shot-id freeze. A failure lists what to fix; fix the script, never the generated files.
`storyboard.json` and `SCRIPT.md` say "generated: do not edit by hand".

Shot ids freeze when the build passes; once scenes use them, a renumber is refused. Add shots with new ids instead.

## 4. Writing the voice-over

- **Pace:** at most 2.6 words a second, per shot. Plan shot lengths from the words, then let the real reads re-time
  them (section 6).
- **Read time:** every on-screen statement holds, once fully built, at least max(1.5 s, 0.4 s + 0.3 s per word);
  numbers hold 2 s after they land.
- **Density:** at most 12 on-screen words per shot; on-screen text is keywords (about 40% of what the voice says or
  less), never a transcript of the VO.
- **Numbers and symbols** are written the way they're spoken or shown consistently; the clock knows how "664" or "&" is
  said, so word lookups find them.
- **Brand words** go in the brand's lexicon, so the voice checks can hear them said in full.
- **The words are final before voicing.** Direction (tone, pauses) is added later as audio tags that never change a
  word (slate-voice).

More craft, with the fixes for each gate: [VO writing](references/vo-writing.md).

## 5. The visionary bar

Make beautiful things that would make someone cry. Every act gets **one signature moment**: the beat people remember,
built inside the brand's own visual language (no glows, flares, particles or bounce). A correct but ordinary animation
doesn't meet the bar. If the visionary skill is available, use it before designing any hero moment: reframe, go wide
across lenses, kill the median, push one idea to a signature. Mark each act's moment in the treatment and in the script
(the shot's `notes`), so builders know where the film's weight is. See [signature moments](references/signature-moments.md).

## 6. After voicing: the clock

Once the takes are approved (slate-voice):
```
slate script build --from-takes     # every voiced shot re-timed from its approved take
slate timing                        # shots, voice cues, word timings, events, timeline on the film's clock
```
- `slate timing` writes `timing/shots.json`, `vo_cues.json`, `vo_words.json`, `events.json` and `timeline.json`. It
  refuses to write unless the frames are exact and contiguous.
- Word timings come from Whisper by default; a word lookup never throws, it falls back to an estimate and records a
  miss. The miss list must be empty before any act is handed in (slate-build).
- Once an act render is registered the timing locks; only the owner can decide to re-time, with a reason.

## 7. Handing over to the pre-production sitting

When the script builds clean (and the treatment, the truth pack and the voice shortlist are ready), stop. The
pre-production sitting approves the checklist, the claims, the script, the voice shortlist and, when the brand's look
isn't approved, the look test. Say what's ready, where it is (SCRIPT.md, the truth pack, the casting reel), and what
each decision is. Record the owner's calls on the draft (trims, keeps, confirmations) as numbered decisions in the
tracker, with their words.

## Rules that hold everywhere

- One hand-edited source. Never edit storyboard.json, SCRIPT.md or the timing files; change script.json and rebuild.
- Nothing is invented: every UI element, word and number maps to a citation (truth packs).
- No invented testimonials or customer quotes presented as real; quotes carry who said them and a pinned source.
- Runtime stays within 10% of the target unless the requester asks otherwise or you justify it in the tracker.
- The picture can be restaged freely by builders; the words, beats and timing are the script's.

## References

- [truth](references/truth.md): researching truth; pinned sources; product-truth and capability-brief templates.
- [truth-packs](references/truth-packs.md): the rules every line and frame obeys about claims, data and the product.
- [treatment](references/treatment.md): the treatment template and how to write each part.
- [structure](references/structure.md): story shapes that work: bookends, payoffs, journeys, the end card.
- [script-as-data](references/script-as-data.md): script.json field by field, a worked example, the gates and fixes.
- [vo-writing](references/vo-writing.md): pace, read time, density, numbers, brand words, a character's own words.
- [signature-moments](references/signature-moments.md): the visionary bar and one signature moment per act.
