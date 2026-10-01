# The truth reference

The brand's truth is the first of its four references, and nothing is scripted before it exists. Every film's own truth
pack (slate-story) builds on it.

## What it holds

| Part | What it is |
|---|---|
| **Claim authority** | the named person who rules on claims for this brand (and often on brand use). For a brand the owner doesn't own, someone at that company. |
| **Sources of truth** | where facts come from, in order of authority, each pinned (a commit, a date, a hash) |
| **Approved claims** | the claims the authority has approved, each with its id, text and any numbers it contains |
| **Never claim** | phrases and claims the brand never makes, even when true in the code |
| **Banned words** | one list, used by every check (script, act files, captions, OCR of the picture, the transcript of the mix). An entry can carry its inflections and the approved substitute, `{word, forms, instead}` (a banned "verified" is also "verifies" and "verification", and the failure says to use "trusted"), so a word is banned in every form. |
| **Self-commentary** | `truth.self_commentary`: set it to `false` when the brand's graphics never caption themselves ("Illustration", "Example"). The script and banned-words gates then ban those captions on screen; the voice can still say "for example". Ask, because one film tagged a frame "Illustration" against its own website's rule. |
| **Sample data** | the invented sample company, people and numbers films use, always labelled as sample data |

## Who decides what's true

- **The owner names the source of truth for product claims.** Retired prototypes, old demos and earlier films are layout
  references only.
- **The product's code is the only product truth**, read in place at a pinned commit; it wins over docs, demos and old
  films.
- **The claim authority's rulings win over the code for what's claimed.** A feature can exist in code and still be
  ruled out.

## Fidelity passes

Before a wide fidelity pass (making everything match the product), propose its scope and ask whether it's the pressing
stuff; record the ruling. The product's core surfaces must match exactly; secondary surfaces only need to feel right
unless something is plainly wrong.

## Autonomous films

Autonomous films use approved claims only. A sourced claim that isn't on the approved list parks the film with a
question for the owner; it isn't used until approved. So the approved-claims list is worth making generous and precise at
onboarding.

## Keeping it current

When the product changes, re-pin its source and re-check the claims that depend on it. When the authority rules on a new
claim, add it (with its id) and record the ruling. The truth reference is re-approved when its claims change.
