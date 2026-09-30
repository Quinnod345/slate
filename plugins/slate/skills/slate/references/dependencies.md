# Dependencies: tools, vendor skills and helpers

What Slate depends on, what it keeps inside its own engine folder, what's optional, and how to check a new tool before
anyone installs it.

## What Slate needs on the machine

`slate doctor` checks all of it and names anything missing; `slate doctor --install` installs the free prerequisites,
asking first. The five connect steps are `slate connect claude`, `slate connect higgsfield`, `slate connect elevenlabs`
and `slate connect aftereffects` (the person runs the sign-ins in their own Terminal).

| Thing | Why | Who sets it up |
|---|---|---|
| ffmpeg and ffprobe, Rubber Band (arm64), uv, Node | encoding and QA, voice pacing, the Python and Node runtimes | the installer, asking first |
| The Whisper model, the Remotion engine and its headless Chrome | word timings, rendering | the installer, asking first |
| GarageBand's sound library | the sampled instruments the sound's tonal roles play | the person, from GarageBand |
| Xcode command-line tools | iOS app captures only | the person |
| After Effects | the AE finish and AE-built scenes | the person |
| ElevenLabs (Creator works; Pro or higher for lossless voice) | voice and score | the person |
| A Higgsfield Cloud API balance | generated shots, when a film allows them | the person |
| Brand fonts | licensed per brand, referenced by name, never shipped | the brand |

## Vendor skills

- **Remotion's and ElevenLabs' skills** are installed inside Slate's engine folder, not globally. Where they disagree
  with Slate, Slate's rules win: Remotion's voice-over skill uses an old multilingual TTS model and settings, its sound
  effects skill points at meme sounds, and its caption skill wants a GPU Whisper; Slate's voice, sound and caption
  stages replace all three. Remotion's Studio never opens a browser.
- **Higgsfield's skills** stay global, because the owner uses them directly, and are installed for Codex and OpenClaw
  too. Outside Slate they generate on app credits (routing.md).
- **The After Effects bridge's skills** are served by the bridge and used as AE looks by name; they're referenced, never
  copied.
- Slate never deletes, edits or copies a vendor skill.

## Optional helpers

None is required; each helps where it fits.

| Helper | Use it for |
|---|---|
| ponder | a converging QA loop over every shot, when a single review pass would miss things |
| visionary | pushing a hero moment past the correct-but-ordinary (the bar in slate-story) |
| goal-fidelity | keeping every ask of a big brief on the checklist |
| video-verifier | recording and checking an app's UI (not film-making); its web path goes through Aside |
| asset-creator | vector drawing: an SVG character, arrows, a diagram |
| canvas-design | static frames and posters |
| frontend-design | component looks for a brand without a design system |
| avoid-ai-writing | polishing a script so it doesn't read as machine-written |

## Checking a tool before anyone installs it

When a tool, MCP server or skill is suggested (by a person, a listing or a search result):
1. Confirm its repository exists and is what the listing says: one listing pointed at a repository that doesn't exist.
2. Check what it really does: does it write files or only play live, does it open a browser, does it need system
   permissions?
3. Check its licence (licences.md): anything copyleft stays out of Slate's code.
4. Record the verdict with reasons in the tracker. Installs that need system permissions go to the owner.

## Pinning

Slate pins what it depends on (Remotion exactly, the AE bridge's version, model ids per film) and upgrades only after a
parity check proves nothing changed. Current model picks live in `registry.json` with verified-on dates, overridable per
film or per user.
