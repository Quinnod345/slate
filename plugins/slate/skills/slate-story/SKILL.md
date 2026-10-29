---
name: slate-story
description: Write and gate a Slate film's story. Covers the treatment and its contextual one signature moment per act recipe, the visionary bar, pinned sources and approved claims, script/script.json as the only hand-edited script source, configurable pace and reading policies, readable on-screen words, and rebuilding the clock from real voice reads. Use when a Slate film needs its idea, treatment, script, claims, on-screen text or VO copy written or revised, when a script gate fails, or when timing needs building after voicing.
---

# slate-story

The story is data. One hand-edited file, `script/script.json`, holds every act and shot: what the voice says, what's on
screen, what the picture does, and where every claim comes from. `slate script build` checks it through the script
gates and writes the storyboard and a readable SCRIPT.md, and it refuses to write anything while a gate fails. Nothing
downstream is typed by hand: shot lengths, timecodes and frames all come from this file and the voice's real reads.

This skill covers stages 2 (truth), 3 (treatment and script) and 5 (timing) of the production. The front door (the
slate skill) has already made the film folder and the brief.

Read `slate history context --topic story` when a private history is selected. Keep the original direction, the
feedback and the revision together. The latest brief and authorized feedback govern creative choices; the recipes
below do not overrule a requested change in structure, energy or visual language. Drafts can receive feedback before
approval, and an already requested revision does not need another approval of that same choice. Exact source claims,
approved words and the film's real clock remain binding.

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
by act, the emotional arc, the intended memorable moments, the voice and the look and the sound in a line each, what the
film never shows, and the owner's calls so far. Keep it to a page or two. Template and craft:
[treatment](references/treatment.md); story shapes that work: [structure](references/structure.md).

## 3. The script as data

Write `script/script.json`: acts of shots, each with an id, the VO line, the on-screen words, the picture, the
transition out, and its sources and claims. Shape, fields and a worked example: [script as data](references/script-as-data.md).

```
slate script build --check      # the gates only; writes nothing
slate script build              # gates, then script/storyboard.json and script/SCRIPT.md
```

The gates: schema, structure, pace (the starter uses 2.6 words a second or less, per shot), read time, on-screen density, runtime
(within 10% of the target), banned words (VO, on screen and picture), claims, numbers, quotes (no invented testimonial),
sources, and the shot-id freeze. A failure lists what to fix; fix the script, never the generated files.
`storyboard.json` and `SCRIPT.md` say "generated: do not edit by hand".

Pace comes from timing defaults, then the selected format, then `script.timing`. Reading defaults come from the format
and `script.rules.read_time`; `script.rules.on_screen_max_words` and `on_screen_max_vo_ratio` can set density limits.
These are supported choices, not reasons to bypass a failing gate. Keep the configured policy with the revision and
rebuild the script; the 2 s number hold and truthful timing still apply.

Shot ids freeze when the build passes; once scenes use them, a renumber is refused. Add shots with new ids instead.

## 4. Writing the voice-over

- **Pace:** start at 2.6 words a second or less per shot unless the format or `script.timing` sets another pace. Plan
  shot lengths from the words, then let the real reads re-time them (section 6).
- **Read time:** the starter holds statements for max(1.5 s, 0.4 s + 0.3 s per word), once fully built; use the chosen
  reading policy and its declared exemptions. Numbers hold 2 s after they land.
- **Density:** the starter uses at most 12 on-screen words per shot and about 40% of the VO word count. Use the
  configured policy for this format; a deliberate voice-matched line or small label has an explicit gate treatment.
  Choose keywords, a transcript or another text treatment for the brief, and verify its actual reading time.
- **Numbers and symbols** are written the way they're spoken or shown consistently; the clock knows how "664" or "&" is
  said, so word lookups find them.
- **Brand words** go in the brand's lexicon, so the voice checks can hear them said in full.
- **The words are final before voicing.** Direction (tone, pauses) is added later as audio tags that never change a
  word (slate-voice).

More craft, with the fixes for each gate: [VO writing](references/vo-writing.md).

## 5. The visionary bar

Make beautiful things that would make someone cry. The earlier product-film recipe used **one signature moment per
act**: the beat people remember. A different brief may need several peaks, one sustained transformation or deliberate
stillness. Set that structure in the treatment. Glows, flares, particles and bounce are creative materials whose use
depends on the current direction and the brand's language; they are not universally banned. A correct but ordinary
animation does not meet the bar. If the visionary skill is available, use it before designing a hero moment: reframe,
go wide across lenses, kill the median and push one idea to a signature. Mark the intended moments in the treatment
and the shot's `notes`, so builders know where the film's weight is. See [signature moments](references/signature-moments.md).

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
- The runtime gate requires the script to stay within 10% of its declared target. When the requester changes the
  runtime, update that target and rebuild; a tracker justification alone does not change the gate.
- The picture can be restaged freely by builders; the words, beats and timing are the script's.

## References

- [truth](references/truth.md): researching truth; pinned sources; product-truth and capability-brief templates.
- [truth-packs](references/truth-packs.md): the rules every line and frame obeys about claims, data and the product.
- [treatment](references/treatment.md): the treatment template and how to write each part.
- [structure](references/structure.md): story shapes that work: bookends, payoffs, journeys, the end card.
- [script-as-data](references/script-as-data.md): script.json field by field, a worked example, the gates and fixes.
- [vo-writing](references/vo-writing.md): pace, read time, density, numbers, brand words, a character's own words.
- [signature-moments](references/signature-moments.md): the visionary bar and the contextual one signature moment per act recipe.
