# Higgsfield's After Effects bridge

Higgsfield publishes a free, local bridge that drives the owner's own After Effects through ExtendScript and serves a
set of motion-design skills (its "looks"). Slate depends on it, pinned, as a core lane for building scenes in AE; it
never vendors or copies it.

## What it is

- A local MCP server that talks to AE through files, so it needs AE's "Allow Scripts to Write Files and Access Network"
  preference on. Only the owner turns that on, in AE's own settings; Slate never changes it.
- It returns live project state and rendered frames, which Slate's own osascript route can't while that preference is
  off.
- Its recipes cost nothing until they generate media. Generating inside its recipes spends Higgsfield's app credits (the
  unfunded pool); for Slate films, media comes from the Cloud API through `slate gen` and is imported instead.
- Its project export and import use its own JSON schema, not Lottie.

## What it's good for

AI-driven motion design through After Effects is strongest for logo reveals, product promos, explainers and
presentation animation, and weakest for complex character animation, intricate rigging and tightly art-directed
performance.

## Its looks

The bridge serves thirteen skills that Slate's looks library lists as AE looks by name: the clean rig (its entry
point), native AE authoring, bridge realities, build orchestration, animation principles, design first, UI mastery,
depth and space, liquid glass, the transition kit, cleanup, matte painting, and Figma transfer. They're referenced, never
copied: there's no redistribution grant. The clean-rig references are worth reading during look development.

## Working with it safely

- **Its batches aren't transactional.** If a batch fails part-way, read the project's state before retrying; never
  blindly re-run a batch that may have half-applied.
- **Take Slate's AE lock** around bridge work, so the bridge and Slate's own scripts never drive AE at once. Slate takes
  the lock in every place the bridge might look for it.
- **Its frame render handles colour-managed projects** with a temporary display transform and warns when it falls back;
  its frames aren't colour evidence unless that warning is clear.
- **Acceptance tests on every version bump**: the bridge is pre-1.0 and moves fast. Where it fails, fall back to Slate's
  own ExtendScript library, which needs no preference change.
- Don't call a bridge tool that runs code inside AE without the owner's go: it launches After Effects.
- **Its arbitrary-code tool stays off, and its categories stay limited** to the ones Slate's scenes use. Slate registers
  and starts the bridge that way every time, and the environment can't loosen it.
- **Its mailbox is a trust boundary**: anything that can write there runs code in After Effects, so Slate creates it
  0700 and nothing else writes to it.
- Each call waits a little longer than the bridge's own limit for that tool (a minute by default, longer to render a
  frame or import), so a long import is never cut off on Slate's side and left in an unknown state.

## Alternatives, for the record

Community AE MCP servers (panel-based) and data-driven template renderers exist as untested fallbacks. Adobe's own AI
features can't drive AE from an agent; connecting to AE means a local bridge plus aerender.
