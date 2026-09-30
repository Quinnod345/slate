---
name: slate-voice
description: Cast, design, direct and record the voice of a Slate film, then clean, pace, align and caption it. Covers casting shootouts and Voice Design with measured ranking, directing eleven_v4 with audio tags (never whisper, soft, quiet or gentle tags), voicing every line with its neighbours as context, cleanup, pacing, word timings, captions, the voice checks, and the cloning policy (designed or library voices by default, the owner's own verified professional clone, never anyone else's). Use when a Slate film needs a narrator chosen or designed, lines voiced or re-voiced, a take cleaned or re-timed, captions made, or a question about which voices may be used.
---

# slate-voice

The voice carries a film. Slate voices every line on ElevenLabs' best model, one take per line with the neighbouring
lines as context so the reads flow as one narration, then cleans, paces and times them, and checks every take before
anyone hears it. The owner picks voices, takes and direction by ear: measurements shortlist and catch faults, they
never decide.

This is stage 4 of the production (voice), and it feeds stage 5 (timing). Every paid call goes through the ledger and
replays offline in tests (`SLATE_FAKE_VENDORS=1`); `--live` spends.

## 1. The profile and the cloning policy

The brand's pack holds its voice profile (provider, voice id, model, settings, lexicon of brand words); a film pins
what it uses at first use, so a re-voice in week three matches week one.

- **Designed or library voices by default.**
- **The owner's own voice** may be an ElevenLabs professional clone, which the owner verified with ElevenLabs
  themselves. An agent never does that verification.
- **Anyone else's voice** only as a real recording, never a clone, and never a public figure's.
- Autonomous agents may narrate with the owner's own clone; their films are still held for the owner.

The policy is enforced before any request is sent: a refused voice exits 1 with the reason. See
[voice policy](references/voice-policy.md).

## 2. Cast or design the voice

- **Cast**: `slate voice cast --voices <id,id,…> --shots <S01,S02>` has several voices read the same lines, measures
  them (pace, pauses, pitch range, noise floor, every word heard, brand words said in full), ranks them, and makes a
  level-matched, labelled listening reel. See [casting](references/casting.md).
- **Design** a new voice from a brief: `slate voice design --description "<prompt>" --text "<preview text>"` makes
  Voice Design previews (always on the design model), measured and ranked; keep one with `--save`; then check the kept
  voice on the production model with `--check`, because designed voices can come out higher and faster there. See
  [voice design](references/voice-design.md).
- The **shortlist** goes to the pre-production sitting. The owner picks by ear; the pick is recorded with its model,
  settings and seed.

## 3. Direct it

Direction on eleven_v4 is **audio tags**: short instructions in square brackets where the delivery should change,
such as `[warmly, with a smile]` or `[confident]` before a line, or `[short pause]` for a beat.

- **Never use a tag that lowers vocal effort or adds breath**: whisper, soft, quiet, gentle, hushed, murmuring, breath,
  intimate, calm or a low voice, in any form. They make a read sound whispery and a little scary. Slate refuses them
  before any request is sent, for every brand.
- Emotion comes through a full, clear voice: warm, with a smile, bright, confident, reassuring, encouraging, sincere,
  matter-of-fact, hopeful.
- A tagged line strips back to the script exactly: tags never change a word. Tag sparingly (about half the lines or
  fewer), one emotion per phrase; a tag at the head of a line carries across it.
- If a line doesn't land, swap the tag rather than rewriting the words.
- Every tagged take is measured against the voice's own untagged takes of the same words, and one that measures
  breathier, softer, creaky, repeated or too long is rejected, however good its tag.

The tagged script lives in `voice/direction.json`; `slate voice --help` lists the direction tools (setting, checking,
stripping and A/B-testing tags). The full guide: [v4 tags](references/v4-tags.md); settings and context:
[directing](references/directing.md).

## 4. Voice every line

```
slate voice tts --estimate      # what it will cost, nothing spent
slate voice tts                 # one take per line, with the neighbouring lines as context
```

- Each line is voiced with its previous and next lines as plain-text context (tags stripped), so the narration flows.
- The model, voice, settings, seeds and request ids are pinned at first use; `--repin` needs the owner's name and a
  reason.
- Existing takes are kept; approved takes are locked by hash and never regenerated without `--force` and a recorded
  spend approval.
- Long batches can run as a queued job: `--detach` returns a job id.

Re-voicing, leaked tails and reusing approved takes: [takes and re-voicing](references/takes-and-revoicing.md).

## 5. Clean and pace

- `slate voice clean --diagnose` measures each take's noise floor; `slate voice clean` applies the approved chain (a
  denoiser that tracks the floor, with a gentle gate). `--method isolator` uses Voice Isolator, and `--remix` makes
  Voice Remix previews that keep the voice and change only the recording. Offer a few labelled cleanup variants and let
  the owner pick by ear. See [cleanup](references/cleanup.md).
- `slate voice pace` shortens long pauses and quickens the read gently with Rubber Band (formants kept), because
  eleven_v4's own speed setting barely moves it. See [pacing](references/pacing.md).

## 6. Align, caption, time

```
slate voice align                   # word timings (Whisper by default), recorded with the model and version
slate voice captions                # SRT, WebVTT and timing/captions.json, timed to the words
slate script build --from-takes     # every voiced shot re-timed from its take
slate timing                        # the film's clock
```

Word starts are snapped to each take's energy onsets. Forced alignment on ElevenLabs is for evaluation until it's
proven against Whisper. Captions are at most 2 lines of 42 characters, 17 characters a second, at least 1 s each,
never overlapping, and word for word the script. See [alignment](references/alignment.md) and
[captions](references/captions.md).

## 7. The voice checks

Before the owner hears a take, it passes the voice checks (listed in `slate voice --help`, recorded in
`qa/gates.json`):
- **policy**: the voice may be used;
- **direction**: no banned tag, and every tagged line strips back to the script exactly;
- **takes**: every line has a take;
- **words**: Whisper hears every word, and every brand word in full, with no repeats and no tag read aloud;
- **tails**: no sound from the next line leaking into the end of a take;
- **fit**: each read fits its shot, starting a short lead in and ending before the cut.

Replayed takes (in tests) are UNCHECKED, never passed.

## 8. Approval and locks

Takes are approved by ear, each against its exact bytes, and approved takes are locked by hash. The voice shortlist
belongs to the pre-production sitting; individual takes come in packets. Re-voicing an approved take needs `--force`
and a recorded spend approval, and re-voices only the lines that changed, with their neighbours as context.

## References

- [voice-policy](references/voice-policy.md): who may be voiced, clones, labels, character voices.
- [casting](references/casting.md): shootouts, reels, choosing by ear, long reads, dialogue.
- [voice-design](references/voice-design.md): the voice brief, prompts, previews, the production-model check.
- [directing](references/directing.md): settings, context, timing from the text, consistency.
- [v4-tags](references/v4-tags.md): the tag families, the rules, the banned tags, measuring every tagged take.
- [cleanup](references/cleanup.md): the floor, the chain, Voice Isolator, Voice Remix.
- [pacing](references/pacing.md): shortening pauses, Rubber Band, what the speed setting doesn't do.
- [alignment](references/alignment.md): Whisper, onset snapping, forced alignment, word misses.
- [captions](references/captions.md): the caption rules, sidecars and burned-in captions.
- [takes-and-revoicing](references/takes-and-revoicing.md): pins, locks, leaked tails, re-voicing with neighbours.
