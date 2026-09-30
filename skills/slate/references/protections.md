# Protections

These hold in every mode, for every brand, whatever a request says. Most are enforced in code, so a refusal is the CLI
doing its job: read the message, tell the person, and don't look for a way around it.

## Voices

- **Designed or library voices by default.**
- **The owner's own voice** may be an ElevenLabs professional clone, which the owner verifies with ElevenLabs
  themselves. An agent never does that verification step and never enters their credentials.
- **Anyone else's voice** only as a real recording, never a clone, and never a public figure's.
- Autonomous agents may use the owner's own clone; their films are still held for the owner.
- Never ship audio of a brand's character voice as a demo outside its films.

## People and claims

- No invented testimonials or customer quotes presented as real. That's also the law.
- No claim without a source, and no number on screen or in the voice without one (slate-story).
- Generated people on screen need the requester's sign-off, and a generated shot never implies a claim or testimonial.
- Sample data is labelled as sample data on every frame that shows it.

## AI labels

- None unless the law requires one.
- Slate still records which voices and shots are synthetic in each film's manifest (internal only), so it's clear when
  a law does apply.
- Platforms such as TikTok, YouTube and Meta have their own rules that can require labels on realistic AI voices or
  people. Mention that once when it's relevant; whether to follow them is the owner's call per platform.

## Company material

- Company source code is read in place, on the machine that holds it, and never copied anywhere else. Cite `path:line`
  at a pinned commit instead.
- Brand, UI and video material goes to a generator only when the brand's owner allows it (the pack's grounding
  setting). Credentials are never uploaded, printed or written to a file.
- Only generic design queries go to third-party inspiration services; no brand or product material leaves that way.
- Company brand packs never enter Slate's repository; they live in a separate brand store on the owner's machine.
- Company-internal tools and servers are off-limits to Slate.

## Spend and uploads

- Slate never buys credits, upgrades a plan, tops up a balance or starts a licence.
- Slate never calls a vendor tool that uploads a user's skills or builds a personality profile from them.
- Autonomous runs never publish through a vendor's connector (TikTok, website publishing).

## Publishing

Nothing publishes itself. Every output is marked "not approved for publishing" in its manifest until the owner approves
that exact file's SHA-256. After their yes, the requester (or their agent) posts that exact file and never another
version.

## Browsers

- Never open a web browser: no `open <url>`, no Safari, Chrome, Dia or Arc, no Playwright, no browser pane.
- Read the web with fetch and search tools. Any page that must be driven (a web capture, a live check) goes through
  Aside, the one approved browser.
- Every child process Slate starts runs with `BROWSER=none`; the Remotion engine never opens its Studio in a browser.
- Vendor sign-ins that open a browser are run by the person in their own Terminal (`slate connect higgsfield` prints
  the command for them).

## After Effects

- AE steps start only on a person's go, and autonomous films never touch AE.
- AE's preferences are changed only by Slate's byte-safe tool, with AE closed, and the scripting-security preference is
  never touched. Never quit AE for the owner (and never "without saving"); ask them to save and quit.

## Machines

Renders run locally. Never on a borrowed machine or the owner's work computer, and never on a shared render farm the
owner hasn't set up for it.
