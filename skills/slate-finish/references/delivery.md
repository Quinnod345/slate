# Delivery

`slate deliver` assembles the delivery set in the film's `deliver/` folder and writes the manifest **last**, so a
manifest only ever describes a complete set.

## The delivery set

| Item | Notes |
|---|---|
| Masters | ProRes 422 HQ (archival) and H.264 (everyday), per derived film too |
| The mix and its stems | 48 kHz, 24-bit; four stems (dialogue, effects, ambience, music) that sum to the mix |
| Captions | SRT and WebVTT sidecars; burned in as well where the format asks (reels) |
| Review cut, phone cut, contact sheets | a half-resolution review copy, a small phone copy, 0.5-second contact sheets |
| Thumbnails and covers | per platform, at the platform's size |
| Platform files | the format's per-platform encodes (size, clock, bitrate, length limits), in `deliver/vN/platform/`; `slate formats show <format>` lists them |
| The AE project | for supervised films finished in AE |
| Synthetic-media flags | which voices and shots are synthetic: internal only, never published |
| The manifest | every file with its bytes, SHA-256 and probe; anything missing listed; the approval status |

## Thumbnails and covers

- The strongest single frame, not a new design: a still from the film at a signature moment (`slate render stills`),
  with the title set in the brand's own type if the platform wants text.
- Truthful: the thumbnail shows what the film shows.
- Sized per platform, inside its safe areas.
- A vendor's thumbnail tool can make covers when its credits are funded; it's optional, and its output follows the same
  truth rules.

## What a delivery refuses

- A draft film (drafts are never delivered as final).
- Plates or captures made in replay (tests), and plates without an approved take.
- A film whose gates don't all pass: only PASS counts; REVIEW items need a person's decision first (slate-qa). That
  includes the sound gates `slate verify` records, the balance gates among them, whenever the master carries sound.
- A platform file over its platform's size or length limit.
- Overwriting a delivered version: the next delivery is a new version.

## Publishing

Every output is marked **"not approved for publishing"** in its manifest until the owner approves that exact file's
SHA-256. Then:
- the requester (or their agent) posts **that exact file**, and never another version;
- an autonomous agent posts through its own committing path, after the owner's yes;
- Slate itself never posts, uploads or publishes anything.

A request to post a film without that approval is refused with what's missing (slate, routing.md).

## Where copies go

The owner's own productions: copies in their Downloads (the full master, a smaller share copy, the phone cut). An
autonomous agent's held films: the phone cut by iMessage and a push notification, with the question of approval. Never a
public link, never a platform upload.

## AI labels

None unless the law requires one. The manifest's synthetic-media flags make it clear when a law applies; a platform's own
labelling rule is the owner's call per platform.
