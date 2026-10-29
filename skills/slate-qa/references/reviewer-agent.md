# The reviewer agent: grading an autonomous film before it's held

An autonomous film is made without the owner watching. Before it parks for the owner's approval, a separate reviewer
grades it, so the owner sees films that already cleared a second pair of eyes, and a weak one comes back with its
problems named.

## Who reviews

A reviewer that didn't build the film. **If you can run sub-agents, the reviewer is a sub-agent** given only the packet
and the references, with no memory of how the film was made; **otherwise it's a fresh agent session** started just for
the review. The builder never grades its own film.

## What it's given

- the contact sheet (0.5-second, labelled) and the phone cut;
- the audio: the mix, and the stems if needed;
- the brand's four approved references (truth, look and motion, voice, sound) and the brand's taste notes;
- the owner's taste notes (sound, motion, the look) and the visionary bar;
- the film's gate results and its brief (GOAL.md).

## What it grades

| Area | Against |
|---|---|
| The look | the brand's approved look and motion reference: palette, surfaces, motion, composition |
| The sound | the brand's sound reference and levels against the voice; the owner's taste (no chimes, nothing buried, sound locked to picture) |
| The voice | the brand's approved voice: every word clear, brand words in full, delivery matching its brief and explicit policy |
| Truth | only approved claims; sample data labelled; nothing invented |
| The bar | one signature moment per act that meets the visionary bar; nothing generic |
| The brief | every ask in GOAL.md delivered |

For each, a grade with evidence: the frame, the time, the measurement, the rule.

## What it decides

- It never approves: only the owner approves a film.
- Anything below the bar is a REVIEW item with evidence; on an autonomous film that parks the film with the reviewer's
  notes, and nothing is published.
- A film that clears review still waits, held, for the owner's yes on its exact file. It reaches them by iMessage with
  the phone cut and a push notification.

## Protect the review

- The reviewer reads; it doesn't edit the film.
- It grades what's there, not what the builder intended; the brief and the references are its only context.
- Its notes go into the tracker verbatim, so the owner sees them with the film.
