# Voice policy: who may be voiced, and how

These rules hold in every mode and for every brand. They're enforced in code before any request is sent (a refused
voice exits 1 with the reason) and checked again by the voice policy gate.

## Whose voice

| Voice | Allowed? |
|---|---|
| A library voice (from the vendor's voice library) | Yes: the default. |
| A shared Voice Library voice the vendor labels **professional** (a voice its owner shares in ElevenLabs' library) | Yes, once the owner approves that exact voice: `library_approval` in the profile. |
| A designed voice (made with Voice Design from a brief) | Yes. |
| The owner's own voice, as a **professional** clone the owner verified with the vendor themselves | Yes, in every mode. |
| The owner's own voice as an unverified or instant clone | No. |
| Anyone else's voice as a clone (a teammate, a client, a customer) | Never. |
| Anyone else's voice as a real recording, with their permission | Yes, as a recording: never synthesized from. |
| A public figure's voice, cloned or recorded | Never. |

- The profile records which it is (a clone names whose voice it is, what kind, and whether its owner verified it); the
  vendor's own voice category is checked live, so a voice the vendor marks as cloned or famous is caught.
- **Owner-approved Voice Library voices.** ElevenLabs labels some shared library voices "professional" because their owners made them as professional clones of their own voices and chose to share them. Quinn's rule is about people he works with, not those voices (2026-10-12: "I don't want to use anybody in the company's voice. I didn't mean that we couldn't use professional voice clones"). So a profile may name one with a `library_approval`: `voice_id` (the one voice it covers), `vendor_category: professional`, `approved_by`, `approved_at`, `ruling` (where the owner's words are recorded), and optionally `ruling_sha256`, `voice_name`, `note` and `autonomous`. The voice stays `source: library`. The approval never covers an instant clone ("cloned"), a famous voice or a clone of a named person, and an autonomous film can use it only when the approval says `autonomous: true`. The live vendor check (`vendor_problems`) accepts the voice only when the approval names its exact id.
- **Slate has no way to make a clone and never verifies one.** Verification is the owner's, done in the vendor's own
  interface. An agent never does that step and never enters the owner's credentials.
- **Autonomous agents** may narrate with the owner's own verified clone, and no other clone. Their films are still held
  for the owner before anything is published.

## Character voices

- A brand's character voice (a designed voice with a persona) is the brand's. Never ship its audio as a demo outside the
  brand's films.
- A voice brief that quotes company code or internal material stays with the company's material; it never travels into
  a personal or public package.
- Designed voice ids belong to the vendor account that made them and don't transfer to another account.

## AI labels

- None unless the law requires one. Slate records which voices in each film are synthetic, in the manifest (internal
  only), so it's clear when a law does apply.
- Platforms (TikTok, YouTube, Meta) have their own rules that can require labels on realistic AI voices even where the
  law doesn't. Mention it once when it's relevant; whether to follow them is the owner's call per platform.

## Commercial use

Only paid plans' output is licensed for commercial use: free-plan output can't be used commercially at all, and output
from beta services can't be used commercially on any plan. Lossless voice (PCM) needs a higher plan; Slate uses the
best format the account allows and records any fallback.

## Keys

The ElevenLabs key is set by a person in their own Terminal (`slate connect elevenlabs`), never in chat. Autonomous
films use a separate key with its own credit quota and only the scopes they need, so they can never spend the owner's
main balance.
