# The After Effects finish

Supervised films are finished in After Effects: an editable AE project built from the film's own data, and masters
rendered by aerender. It's the owner's, so it starts on their go.

## The rules

- **Only on the owner's go.** Every AE step refuses without `--go`, refuses for autonomous films, and refuses while AE is
  busy (exit 5). Adobe's enterprise terms bar operations a person didn't start; Slate treats that as the rule.
- **Never quit AE for the owner**, and never "without saving": ask them to save and quit. Open projects are preserved
  before any rebuild.
- **AE's preferences are changed only by Slate's byte-safe tool**, with AE closed, one key at a time, line endings kept,
  and the scripting-security preference never touched. A text-mode rewrite once reset every preference AE had.
- **The disk cache is off while masters render**, and the owner's setting is restored afterwards, even on a failure or a
  kill. It once filled 60 GB. Emptying the cache is the owner's job, in AE's own settings.

## The steps

1. `slate master --ae --go` builds the project from data: one comp per shot showing its slice of the act render, the act
   comps in order, and the master comp with the final mix, a marker per shot and its words.
2. The project is audited (settings, comps against the shots, footage, fonts, render format) and the results recorded as
   gates.
3. The owner saves and quits AE.
4. The masters render with aerender in chunks: each chunk in a fresh process, with two outputs from one pass (H.264, and
   a high-quality intermediate conformed to ProRes 422 HQ), up to three tries, resuming by skipping chunks already at the
   right frame count, with a disk watchdog that stops under a floor of free space. Chunks are joined by stream copy and
   the mix is muxed in.
5. Every master frame is compared with the act renders (masters.md).

## Settings that matter

- **Working space Rec.709 Gamma 2.4**: the act renders are BT.709-tagged, and AE reads that tag as Rec.709 Gamma 2.4.
  With an sRGB working space AE converted them, and the darkest brand colour came out about 8 code values dark. With the
  matching space, the difference is under half a code value. Footage colour profiles and output-module colour settings
  can't be set by script in AE, so the working space is the lever.
- **16 bits per channel, linear blending off.**
- **A sampling bias on nested comps**: every nested layer starts a tenth of a frame early, so AE samples inside each frame.
  Placed at exact fractional-rate times, slices showed the previous frame and the picture ran a frame behind the sound;
  AE's own half-frame tolerance couldn't see it.
- **AE's stock "High Quality" output is ProRes 422, not 422 HQ**, and neither ExtendScript nor the AE bridge can pick
  the codec inside an output module's format options: render the stock lossless-with-alpha output and conform it to
  422 HQ.

## Rendering facts

- aerender renders without AE's interface (in its render-only mode it reportedly doesn't need an Adobe sign-in, which
  is unconfirmed); several aerender instances can't write one movie file, so parallel AE renders write image
  sequences, and plug-ins on extra render machines carry their own licences.
- Long aerender runs swap and abort under memory pressure: chunk them, in fresh processes, with a memory cap.
- ffprobe lists AE's movie streams twice; count frames from the first.
- Adobe Media Encoder comes with AE, but the pipeline doesn't need it.

## Proven end to end

Before the first film, a toolchain proof imported a generated clip into an AE comp, titled it on a brand ease, saved the
project and rendered it with aerender, end to end, and measured the ease in pixels. `slate connect aftereffects` repeats
that kind of proof as a one-second smoke test, on the owner's go.

## When AE isn't there

Finishing works without the AE bridge's file-write preference (the osascript route reports through AE's own settings).
If AE isn't installed or licensed, supervised films can still be mastered with ffmpeg on the owner's say-so, and the
manifest records it.
