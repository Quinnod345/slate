# Licences: what Slate may use, and how

Facts as researched on 2026-09-30. Terms change, so re-check before a film is published commercially, and say which
terms you checked and when.

## Remotion

- Free for individuals, for for-profit organisations of up to three people, for non-profits and for evaluation,
  commercial use included. It's source-available, not open source, and it forbids selling a derivative of Remotion.
- Company tiers: Creators (per seat, per month), Automators (per render, with a monthly minimum) and Enterprise.
- Remotion's own FAQ puts a person writing Remotion code, directly or through agentic tools, under Creators, and
  automated pipelines, prompt-to-video tools and the Player under Automators. An unattended agent rendering on its own
  looks like Automators.
- For licensing, one render is one successful video, audio, GIF, still or PDF; Studio previews and the Player don't
  count.
- Remotion's coming v5 terms require a licence once the people operating it, across all involved parties (contractors
  included), reach four; receiving finished media isn't use.
- It allows a service that generates Remotion code with AI and renders it, but not one where end users upload their
  own Remotion code to be rendered.
- **How Slate uses it:** installed through npm, pinned exactly, never redistributed. Each organisation that runs it
  needs its own licence status, and Slate is never sold as a Remotion derivative. Companies of four or more should get
  their own licence or use the open web renderer or After Effects, which need none.

## After Effects (Adobe)

- Adobe's enterprise terms bar operations a person didn't start. Until the owner's own licence terms are checked, Slate
  treats that as the rule: every AE step starts on a person's go, and autonomous films never touch AE.
- Unlimited render engines are allowed on an intranet with one full AE install; Media Encoder can't serve as a hosted
  encoder.

## ElevenLabs and Eleven Music

- Output from paid plans is licensed for commercial use; free-plan output can't be used commercially at all, and output
  from beta services can't be used commercially on any plan.
- Eleven Music: online commercial use is fine on paid plans; film, TV and radio need an Enterprise plan. Slate flags a
  score on any broadcast format.
- Designed voice ids belong to the account that made them and don't transfer.

## Higgsfield

- Cloud API output is for the account holder's use under Higgsfield's terms. Record the model, request id and cost of
  every generation in the film's records.

## Sounds and fonts

- **Apple's sound content** (GarageBand, Logic, Final Cut Pro libraries) is licensed for productions made with Apple's
  apps. Slate builds from each machine's installed content and never redistributes a sample or a bank.
- **Brand fonts** are licensed per brand. Reference them by installed name, kit or Google Fonts family, with the licence
  noted; never commit font files, and never fetch a brand's licensed fonts from its web kit into a third-party sandbox.
  A web font kit may serve only allowed domains and load online during renders; in After Effects the same fonts must be
  activated or installed separately.

## Code Slate never copies

- **OpenMontage** is AGPL-3.0: copying its code would force the AGPL on Slate. Ideas only.
- Any other copyleft code the same way; permissive code (MIT, Apache-2.0) only with its notice kept.
- Company source code is never copied at all (protections.md).
- Vendor skills are depended on and pinned, never vendored into Slate.

## Recording what you checked

When a film's use of something depends on terms (a broadcast score, a company film on Remotion, a platform's AI-label
rule), write the terms you relied on and the date into the tracker, and flag it to the owner rather than deciding it.
