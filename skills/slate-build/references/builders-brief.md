# A builder's brief (fill-in template)

Copy this into the film folder (for example `brief/BUILDERS.md`), fill in every bracket, and hand it to every act
builder. Keep it in sync with what's actually built. The rules behind it are in [builder briefs](builder-briefs.md).

---

## What the film is

[One paragraph: the film's idea, runtime, acts, narrator, and who it's for. Quote the owner's own words where they
shaped it.]

## The inputs (generated; don't edit them)

| What | Where |
|---|---|
| The approved script and shot list | `script/SCRIPT.md` (from `script/script.json`) |
| The product truth: screens, labels, numbers, flows, with sources | `brief/truth.json` and [the product-truth document] |
| The frame-exact shots and the voice's word timings | `timing/shots.json`, `timing/vo_words.json` |
| The voice's takes, placed | `timing/vo_cues.json` |
| Captions | `timing/captions.json` |
| The brand's look and motion law | the brand pack; [the brand's MOTION.md] |
| Shared surfaces and contracts | `scenes/contracts.tsx`, [any shared components] |

## The look

[The approved look in a few lines: the ground, the surfaces, depth, the accent's one meaning, what the camera does. Link
the look test that was approved.]

## Motion law (enforced)

Only the brand's named eases, by job; the duration scale; staggers capped; nothing scales from zero; no bounce outside
the named settle; no linear moves except time-based things; no hard cuts or crossfades except the reasoned ones; hero
text moves or unmasks; read times hold; sync within 2 frames of the word; motion blur is the renderer's, never faked.

## Truth (enforced)

[What the film never shows or says: ruled-out features, banned words, the sample data and its label, partner rules.]

## Files and ownership

You own only your act's folder: `scenes/act<N>/`. Import the kit, the contracts and other acts read-only. Kit requests
go in your NOTES.md. Your own scratch folder: [path].

Every act folder follows one pattern: `index.tsx` (the act inside its `ActRoot`, plus the scene), `beats.ts` (every beat
computed from the voice's words, at module level), scene files per shot or per world, the act's events (registered in
the beats, or `events.ts`), `NOTES.md`, and optionally a review-only entry.

Your NOTES.md keeps, from the first hour: a checklist re-read against this brief's words; what each shot shows;
decisions and restaging; the truth pass; what you reused; your contracts and their proof; the checks you ran; score and
sound cues; open items and kit requests; and a log.

## Who builds what

| Builder | Acts | Shots | Frames | Folder |
|---|---|---|---|---|
| [1] | [1–2] | [S01–S09] | [0–2223] | `scenes/act1/`, `scenes/act2/` |

## Contracts (both sides must match)

| Boundary | Rest state | The picture |
|---|---|---|
| [Act 2 → Act 3] | [`NameOpen`] | [exactly what's on screen, where, at what size; the character calm] |

Hold each contract still for at least 6 frames each side; settle everything decorative out over the last 0.6 s before
it.

## Signature moments

[Per act: the moment, the word it lands on, what the sound does.]

## Sound

Export your beats with `slate emit-events --act <N>` in the event vocabulary (tap, type, land, snap, count, draw, data,
page, stamp, whoosh, swell, character, impact; size s, m, l). List every beat that lands. Note score cues in NOTES.md.

## Checking and handing in

- Stills: `slate render stills --act <N> --frames <frames> --scale 0.5`, into your scratch folder.
- A draft preview: `slate render --act <N> --draft --out <scratch>/act<N>.mov`.
- No full-resolution finals. No other machine.
- Hand in with `slate check <N>` passing.

**Before you finish:**
- [ ] Every shot's on-screen text matches the script (restaging the picture is fine; say so in NOTES.md).
- [ ] No banned words; read times hold; body text at least 28 px at a 1080-line frame.
- [ ] Both contracts match.
- [ ] Any character is calm at your act's ends.
- [ ] The word-miss list is empty.
- [ ] Your events file lists every beat.
- [ ] NOTES.md says what you decided, what you restaged and what you couldn't do.
