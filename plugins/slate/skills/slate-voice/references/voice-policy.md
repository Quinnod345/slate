# Voice policy: who may be voiced, and how

These rules hold in every mode and for every brand. They're enforced in code before any request is sent (a refused
voice exits 1 with the reason) and checked again by the voice policy gate.

## Whose voice

| Voice | Allowed? |
|---|---|
| A library voice (from the vendor's voice library) | Yes: the default. |
| A designed voice (made with Voice Design from a brief) | Yes. |
| The owner's own voice, as a **professional** clone the owner verified with the vendor themselves | Yes, in every mode. |
| The owner's own voice as an unverified or instant clone | No. |
| Anyone else's voice as a clone (a teammate, a client, a customer) | Never. |
| Anyone else's voice as a real recording, with their permission | Yes, as a recording: never synthesized from. |
| A public figure's voice, cloned or recorded | Never. |

- The profile records which it is (a clone names whose voice it is, what kind, and whether its owner verified it); the
  vendor's own voice category is checked live, so a voice the vendor marks as cloned or famous is caught.
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
