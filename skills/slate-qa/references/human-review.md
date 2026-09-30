# Human review

Machines catch what can be measured. People catch whether it's right: whether a move feels intentional, whether a frame
reads, whether the film is the brand's. Human review is built into the method, not left to the end.

## What each shot gets

- a half-resolution motion preview with sound;
- **cut strips**: every frame from 10 before to 10 after each cut, at full resolution;
- **detail frames** at each planned event: the landing frame and 20 frames after, at full resolution;
- a motion scan (the mean frame-to-frame difference), with spikes that no planned event explains (within 3 frames)
  flagged;
- its gates.

## Statuses per shot

| Status | Meaning |
|---|---|
| **pass** | every check holds, and a person has looked |
| **fixed → pass** | a defect was found, fixed, re-rendered and rechecked |
| **fail** | an open defect |
| **pending** | the final render isn't in yet |

Record them in a per-shot table with notes (the film's QA record), so anyone can see where every shot stands.

## The defect log

Log every defect with measured evidence and its fix:
- what it was, where (shot, frame, film time);
- the measurement (a 17.8 px jump at frame 1166; a join jump of 1.36 against 0.13 inside either side; a read held 2.75 s
  of 3.1 s);
- the cause;
- the fix, and the measurement after it.

"Looks better now" isn't evidence.

## Contact sheets

After the automated checks, look at the contact sheets yourself: 0.5-second sheets of the whole film, labelled with shot
and time. Look hard at hierarchy, emptiness, legibility, the brand's look, and sync with the voice. Review representative
frames the same way the reference films were studied. The review checklist says what to look for.

## A converging loop

For a long film or a wide sweep, one pass misses things. When the ponder skill is available it can run review as a
converging loop: every shot enumerated as a unit, each graded against the checklist, the grades revised pass by pass
until they stop changing, and no shot left ungraded.

## Who reviews what

| Reviewer | Reviews |
|---|---|
| The gates | everything measurable |
| The builder | their own act, before hand-in (stills, previews, strips) |
| The coordinator (or the lead agent) | every act's stills at the changed frames, each fix in its preview, the final renders' gates |
| The owner | the look, the voice, the sound, hero moments, generated takes, the final cut |
| A separate reviewer agent | an autonomous film, before it parks (reviewer-agent.md) |
