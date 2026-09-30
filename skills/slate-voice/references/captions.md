# Captions

Captions come from the script's words and the voice's word timings, so they're word for word what's said and exactly
in sync. They're built once and used twice: as sidecar files for platforms, and burned in by the renderer where the
format asks for it.

## Building them

```
slate voice captions
```

writes `timing/captions.json` (the cues, for the renderer and the checks) and `voice/captions/<film>.srt` and `.vtt`.

## The rules

- At most **2 lines of 42 characters**.
- At most **17 characters a second**; a cue that would read faster is held longer.
- At least **1 second** on screen.
- **Never overlapping**, and in order.
- **Word for word the script** (the booth script, not what Whisper heard), including how numbers are written.
- Breaks at natural phrase boundaries; never split a name or a number across lines.

`slate qa captions` checks every rule against the delivered captions.

## Burned-in captions

Social reels (9:16) usually carry burned-in captions: the kit draws them from `timing/captions.json` in the brand's own
font, inside the platforms' safe zones (slate-motion, safe-areas.md), styled by the brand pack. They're part of the
picture, so they're checked by OCR with the rest of the frame.

## Sidecars

Deliver SRT and WebVTT alongside the masters for every platform that takes them (slate-finish). A derived film in
another language gets its own captions from its own script and takes.

## Language

v1 speaks English, but the caption code carries no English literals: line-break and punctuation rules live in language
tables, so another language arrives as data.
