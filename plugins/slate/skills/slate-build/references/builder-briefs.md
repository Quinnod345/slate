# Running act builders

A film's acts can be built by several builders at once, each owning one act, all working from one brief. On the first
films, five builders built one film's acts overnight, five built the next, and three built the third, with one sound
designer working in parallel from their exported beats.

**If you can run sub-agents, build acts in parallel, one builder per act; otherwise build them in order**, one act at a
time, with the same brief and the same hand-in check. The discipline below matters either way.

## What a builder's brief names

- **The approved script and shot list** (`script/SCRIPT.md`), and the treatment's signature moment for the act.
- **The product truth**: every screen, label, number and flow the act may show, cited from the product's code
  (slate-story's truth pack).
- **The motion law** (the brand's) and the look.
- **The locked timing**: shot lengths and word timings, generated; never edited by builders.
- **Files and ownership**: who owns which act folder, and what's read-only.
- **The contracts** at each boundary, specified exactly.
- **The event vocabulary** for sound.
- **How to check and hand in**, ending with the hand-in checklist.
- **The owner's words** where they shaped the brief, quoted.

Briefs are layered: a base brief for the film (or for Slate's defaults) plus a short delta for this film's differences.
A variant film's builders read the base brief first; its own brief lists only what's different. Keep briefs in sync with
what was built: one brief still said two shots were built in After Effects after they'd been rebuilt in code, and a
builder followed it.

## Ownership

- A builder owns only its act's folder (`scenes/act<N>/`). The kit, the shared contracts, other acts, generated files
  and docs are read-only.
- **Kit changes go through NOTES.md**: a builder who needs one writes it up and works around it locally; the coordinator
  changes the kit.
- **Each builder has its own scratch folder**: parallel builders overwrite each other's scratch otherwise.
- **Builders make stills and half-scale previews only**, never full-resolution finals, never inside the film folder.
- **When an act is handed in, the builder cleans its scratch folder** and keeps only the final preview and its contact
  sheet.
- **Facts several acts show** (a tally, a standing, a sample value) come from one shared source; an act that picks a
  value records it for the coordinator, and a change to a shared surface goes to every act that shows it.

## Judgement calls

- **Keep the script's beats, words, timing and on-screen text; restage the picture freely**, and say so in NOTES.md.
- When the script's picture notes and its on-screen column disagree on wording, the on-screen column wins.
- When a brief's ease note conflicts with the motion law's ease for that job, use the law's and record the deviation.
- Gate each element's entrance on the first word it shows, not a later one, or it appears already part-way through its
  move.
- When two rules conflict (a no-logos-over-footage rule against a brief that wants the corner mark throughout), make
  the smallest judgement that honours both, and record it.
- Record deviations on surfaces outside your act rather than fixing them.
- Work the owner approved is not rebuilt; build around it.

## Save early and often

A builder session can end without warning, and anything not on disk is lost; it happened on the first film. Get a first
working version of every shot onto disk quickly, then refine, and keep NOTES.md current as you go, so the next builder
(or the next session of you) can pick up where you stopped.

## Coordinating

- The coordinator builds the kit and the shared contracts before any builder starts, and marks the foundation done.
- Builders hand in with `slate check <N>` passing and their checklist done.
- Every act keeps a requirement checklist in its NOTES.md, each ask a checkbox with its evidence; before calling an act
  finished, the builder re-reads the brief's original words against it.
- Superseded cuts stay recorded in the notes as a record of what was delivered and why it changed; they're never
  silently erased.
- Intentionality degrades across parallel acts: judge each act against the best act's code and notes, tie every move to
  a word or an idea, and prefer fewer, better moves.

A fill-in brief: [builder's brief](builders-brief.md).
