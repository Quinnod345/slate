---
name: slate-qa
description: Check a Slate film and decide what's truly done. Covers every gate (script, voice, timing, hand-in, sound and the film gates from schema and frame counts to glitch, act joins, motion, accessibility and master-against-render PSNR), the five gate states where only PASS counts and unchecked is never passed, triaging failures and glitch flags, a person accepting a REVIEW item, the review checklist and review packets, the separate reviewer agent that grades an autonomous film before it's held, and ponder as an optional QA loop over every shot. Use when running or reading Slate's gates, deciding whether a shot, act, stage or film is done, triaging a failure, reviewing a cut, or grading an autonomous film.
---

# slate-qa

Gates, not vibes. Every step with a measurable result is gated by a machine; the look, the hero moments and the final
cut are judged by people; and nothing unchecked is ever reported as passed. "Done" means verified, with the evidence
named.

## 1. The five states

| State | Meaning | Counts as passed? |
|---|---|---|
| **PASS** | checked, and it holds | yes, the only one |
| **FAIL** | checked, and it doesn't | no |
| **REVIEW** | a person has to judge it (a glitch flag, a generated take) | no, until a person accepts it |
| **UNCHECKED** | couldn't run (a tool missing, an input not made yet); its evidence says what to do | no, never |
| **STALE** | an input changed since it was checked | no, until it runs again |

Every result names the engine and version it used (the OCR engine and level, the Whisper model, the ffmpeg build) and
the hashes of what it read, so it turns STALE the moment an input changes. `qa/gates.json` holds the latest result per
gate. See [done means verified](references/done-means-verified.md).

## 2. Running the gates

```
slate qa                      # every film gate, in film order (writes qa/gates.json)
slate qa --quick              # skip the gates that read the whole picture (ocr, readtime, glitch, psnr, stability); it says
                              # which it skipped, and motion and access reuse what a full run read
slate qa loudness sync        # just these gates; a module's name (motion) selects all of its gates
slate qa motion --act 2       # the motion and access gates take their own flags (--act, --render, --only, ...)
slate qa stability baseline --act 3 --by <name> --reason "<why>"   # accept an act's open stability flags
slate qa --list               # the gates, where each comes from, and each one's latest status
slate qa --detach             # queue a full run as a job (it holds the render slot)
```

A narrowed run (`--act`, `--only`, one render) reports what it finds but can't pass the gate.

Exit 0 only when every result is PASS. Each stage has its own gates too, run by its own command (the script build,
the voice checks, the timing build, the hand-in check, `slate verify` for sound). Every gate, what it checks and how to
fix it: [gates](references/gates.md).

## 3. Triage

```
slate qa triage               # everything that isn't PASS, most severe first, with the next step for each
```

FAIL first, then STALE, UNCHECKED and REVIEW. Glitch flags within 3 frames of a planned beat are skipped (a smooth fast
move isn't a glitch); the rest are real until a person says otherwise. When a person accepts a REVIEW item:

```
slate qa triage --accept glitch:frame:1166 --by <their name> --reason "<why it's acceptable>"
```

The acceptance is bound to the hashes of the files it was judged on: re-render, and it no longer applies. An agent can't
accept a REVIEW item on an autonomous film. See [triage](references/triage.md).

The stability gate triages its own flags first (planned beats, the act's reference render, the baseline a person
accepted), and an act's accepted flags go into its baseline, which survives re-renders. Its order and tools (references,
strips, render-only compares, the scan's controls): [render stability](references/stability.md).

## 4. People review what machines can't

After the automated checks, look at the contact sheets and the strips yourself: pops, jumps, twisting, overshoot or
bounce; aliasing, clipping, legibility; hand-offs across shot and act joins; interpolation on generated plates; the
brand's look; sync with the voice. Record each shot's status as **pass**, **fixed → pass**, **fail** or **pending**, and
log every defect with measured evidence (a 17.8 px jump at frame 1166; a join jump of 1.36 against 0.13) and its fix.
See [human review](references/human-review.md) and the [review checklist](references/review-checklist.md).

Packets carry what the owner reviews: the phone cut, contact sheets, gesture sheets, what changed. See
[review packets](references/review-packets.md).

## 5. Autonomous films: park, don't pass

- A REVIEW verdict on an autonomous film **parks** it with its evidence; it never counts as a pass. Only a person releases
  it (`slate qa unpark --by <name>`).
- **Before a film parks for the owner, a separate reviewer grades it**: the contact sheet and the audio, against the
  brand's references, the owner's taste notes and the visionary bar. If you can run sub-agents, the reviewer is a
  sub-agent given only the packet and the references; otherwise it's a fresh agent session started for the review.
  Never the agent that built the film. See [reviewer agent](references/reviewer-agent.md).
- Claims off the brand's approved list park the film with a question.

## 6. Ponder: a QA loop over every shot (optional)

When a single review pass would miss things (a long film, many shots, a fidelity sweep), and the ponder skill is
available, run it as a converging loop: enumerate every shot as a unit, grade each against the review checklist, revise
the grades pass by pass until they stop changing, and never stop while a shot is ungraded. It sharpens review; it never
replaces the gates.

## 7. Before saying anything is done

- Every gate that covers it is PASS, fresh, with its evidence.
- A person has judged what only a person can judge (the look, the voice, the sound, hero moments, the final cut).
- Re-read the requester's original words in GOAL.md and check each ask against what was delivered.
- Never claim every frame is good unless every frame in the range was processed or inspected.

## References

- [gates](references/gates.md): every gate by stage, what it reads, how to fix it, trusting a metric.
- [triage](references/triage.md): ordering, glitch flags and their false positives, accepting REVIEW items.
- [stability](references/stability.md): render stability's triage order, baselines, references, strips and controls.
- [done-means-verified](references/done-means-verified.md): what counts as done, and what never does.
- [human-review](references/human-review.md): shot statuses, the defect log, contact sheets.
- [review-checklist](references/review-checklist.md): what to look for, shot by shot and film-wide.
- [review-packets](references/review-packets.md): what the owner sees, and how to read it.
- [reviewer-agent](references/reviewer-agent.md): grading an autonomous film before it's held.
