# Slate's proofs

A proof is a run on this Mac that shows Slate does what the films did, measured against what they delivered. Each
section says what was run (the exact commands), what came back, and the tolerances the comparison holds. The films'
project is read-only in every proof: Slate reads it, writes its own outputs to temporary folders, and never changes a
file there.

## Data parity (P192)

**The claim.** Slate's generalized timing, cue, caption and mix tools, rerun on films A, B and C's own inputs,
regenerate what those films delivered, within stated tolerances. The three films are the oracle (PLAN.md, "Golden
data"); this is the formal run of every golden suite that covers them, heavy tests included.

**When and where.** 2026-10-01, 10:30 to 10:49 EDT, on this Mac (Apple M4 Max, 14 cores, 36 GB), with Slate at commit
`060e85a` plus the L5 working tree, Python 3.13 in `.venv`, in a scratch sandbox (fresh `SLATE_DATA`, `SLATE_FILMS`
and `SLATE_BRANDS`) and replay mode, so nothing touched Slate's own data folder and nothing was spent. The Mac was busy
(the film session was rendering and other agents were testing), so the suites ran one at a time under `nice`.

**The commands.**

```sh
export SLATE_GOLDEN_HEAVY=1 SLATE_FAKE_VENDORS=1
export SLATE_DATA=$SANDBOX/data SLATE_FILMS=$SANDBOX/films SLATE_BRANDS=$SANDBOX/brands
nice -n 15 .venv/bin/python -m pytest tests/story/test_timing_golden.py -v -p no:cacheprovider        # timing and cue sheets
nice -n 15 .venv/bin/python -m pytest tests/voice/test_captions_golden.py -v -p no:cacheprovider      # captions
nice -n 15 .venv/bin/python -m pytest tests/sound/test_sound_golden.py -v -p no:cacheprovider         # mixes
nice -n 15 .venv/bin/python -m pytest tests/sound_balance/test_sound_balance_golden.py -v -p no:cacheprovider  # mix balance
```

**The results.** Every test passed; none skipped.

| Suite | What it reruns on A, B and C | Tests | Time | Peak memory |
|---|---|---|---|---|
| Timing and cue sheets | `shots.json`, `vo_cues.json`, `vo_words.json`, `timeline.json`, C's storyboard from its takes, the script gates against the films' own checks, and `slate timing` itself on each film's inputs | 23 passed | 42 s | 0.14 GB |
| Captions | each film's captions from its shots, words and cues, with its SRT and WebVTT | 4 passed | 0.8 s | 0.09 GB |
| Mixes | the meter, the stems summing to the mix, the music gap check, every take where its cue says, proofs 2 and 3, A's p3 mix, and the whole mix of C, B, A (its v1 layout) and A as delivered, rerun from their own inputs | 20 passed | 17 min 4 s | 4.7 GB |
| Mix balance | film B's p3 balance check, planned stops and score rests, from its family stems | 2 passed | 37 s | 0.36 GB |

**The tolerances.** Each is stated once, in the suite that holds it.

- **Shots** (`timing/shots.json`): every frame, second and rounding field exact on all three films; the text fields
  exact on B and C. A's delivered file predates its draft-2.5 edit, which re-voiced four lines (their text and words
  changed, their lengths didn't): those four shots are found from the data and are the only differences. C's
  delivered file predates its word timings, so it embeds none; the rerun embeds them.
- **Cue sheets** (`timing/vo_cues.json`): every cue's shot, start, type, stem, file, gain, fades and length exact, and
  the sheet's lead and voice; only the free-text note may differ.
- **Word timings** (`timing/vo_words.json`): rebuilt from each film's Whisper JSON and takes; every word's start, end,
  Whisper start and snap within 0.5 ms (they're stored to the millisecond). A number the script spells out is split into
  its spoken words, and C's two hand splits (C05, C19) come out exactly.
- **Timeline** (`timing/timeline.json`): exact on A's eleven shots with authored events (B and C have none).
- **Storyboard**: C's lengths re-derived from its takes exact, with its renumbered ids.
- **Captions** (`timing/captions.json`, SRT, WebVTT): text, line breaks and shots identical; times within 1 µs; SRT and
  WebVTT byte for byte. A predates merging short flashes; B and C use it.
- **The meter**: integrated loudness within 0.1 LU and true peak within 0.2 dB of each film's record, sample count
  exact.
- **Stems**: the four delivery stems sum to the mix within 1e-6 and agree with the films' own measure of it to one
  24-bit step.
- **Music gap check**: the same verdict and the same failures as each film's music check; the quietest level outside
  the planned stops within 0.1 dB.
- **Voice placement** (B and C): every delivered take found within 0.5 ms of where its cue puts it, correlating within
  0.002 of the delivered floor.
- **Whole mixes**, rerun from each film's own stems and score with its settings: the voice within 0.1 LU, the gain
  within 0.15 dB, the loudness within 0.1 LU, the true peak within 0.25 dB, each delivery stem within 0.15 LU, the
  limiter flags identical, and the stems summing to the mix to a 24-bit step. A runs through the films' own limiter
  (ffmpeg's alimiter), which `mix_arrays` keeps for exactly this; B and C land on Slate's.
- **Proofs 2 and 3**: they measure as they recorded; the starting palette is proof 2's measured balance; the sound
  reference is proof 3, never the retired v2 sound. A's p3 mix (`build/audio_p3`) is delivered now, so its test ran
  instead of skipping: Slate's tools measure it within 0.1 LU and 0.2 dB of its own record, with the same gap verdict.
- **Balance** (film B's p3 mix, measured from its family stems against its own `balance_check.json`): all 978 words'
  clarity within 0.05 dB, with the same minimum and median; each family alone within 0.05 dB; the same words traded
  under the owner's half-depth rule, inside approved spans, failing (none) and flagged; the never-over margin per
  family within 0.1 dB at the same word, within 11 ms; texture air within 0.1 dB; the voice and every family's level
  within 0.1 LU, and under speech within 0.15 LU. The planned stops widen by the music's own fades exactly as recorded,
  the composed score's rests are the same, and the mix opens on sound and ends in silence as it did.

**What it doesn't cover.** The effects cue sheet (`sound/sound_cues.json`, spotted from the visual events) comes from
the effects engine, which waits for proof 3, so there's no Slate cue sheet to compare yet. Render parity, a kit-built
act's frames against the delivered render, is its own proof (P193).

**Running it again.** The same four commands, from Slate's checkout, on a Mac with the films' project in place
(anywhere else the suites skip, saying why). The mixes need about 5 GB of memory and a quiet quarter of an hour.

## Render parity on film A's Act 4 (P193)

**The claim.** Slate's engine, at the pinned Remotion 4.0.521, renders a kit-built act of a delivered film to the same
pixels: every frame of film A's Act 4 at 40 dB PSNR or better against the render the film delivered.

**When and where.** 2026-10-01, 12:24 to 12:55 and 13:50 to 14:33 EDT, on this Mac, through Slate's installed engine
(`DATA/engine/4.0.521`, render_mb, 16 samples across a 180° shutter, GL `angle`, concurrency 4), inside Slate's job
queue on its one render slot (jobs `j-20261001-142502-1d93` and `j-20261001-165810-5e84`; the first stopped safely at
the 4 GB disk floor during S22, the second rendered S22 to S27 and proved all eight shots). Other sessions were
rendering, so throughput ranged from 5.6 to 19.9 samples a second.

**What was held against what.** The delivered file is the one the film's render registry names for Act 4 today:
`build/renders/r3f/A4_S20-S27.mov` (ProRes 422 HQ, 1920×1080 at 30000/1001, 2,712 frames, sha256
`3a707a61dd64f3e3d97f080937253b2a42283af8e1b604e46d1744186d921e13`). The film session rendered it at 04:06 to 04:34 in
its overnight re-render batch and installed it at 10:10 once its regression check passed; it was hashed again after
the last shot, unchanged. Slate read the act's scenes in place: a scratch film whose `scenes/act4/index.tsx` is one line
re-exporting the film's own Act 4 from `pipeline/r3f`, nothing copied. The act renders in tilt raster mode, its
default since the 2026-09-30 rework; five short preflight ranges confirmed that before the long render (hint mode
reproduces the older 2026-09-29 render instead). The whole-film master from 2026-09-29 still carries that older
Act 4; the parity is against the act render the project names today.

**The method.** One bundle, then one render per shot. Each shot was hashed, held against the same frames of the
delivered file (a stream-copy cut with an output-side seek, frame-exact for intra-only ProRes) by
`slate qa psnr --min 40` and by per-frame PSNR and SSIM on `yuv422p10le`, its frame MD5s kept, and then deleted, so the
job's own disk peak stayed near 1 GB.

**The results.** Every frame passes, by a wide margin: the worst is 62.91 dB, the median 82.32 dB, 193 frames are bit
for bit the delivered ones, no frame is under 60 dB, and SSIM is never below 0.99994. Rendering took 65.6 minutes.

| Shot | Act frames | Film frames | Frames | Render | Samples/s | Worst | Mean | Identical | SSIM min |
|---|---|---|---|---|---|---|---|---|---|
| S20 | 0–358 | 5350–5708 | 359 | 11.1 min | 8.7 | 76.90 dB | 101.69 dB | 99 | 0.999998 |
| S21 | 359–643 | 5709–5993 | 285 | 13.6 min | 5.6 | 72.10 dB | 81.06 dB | 0 | 0.999994 |
| S22 | 644–1212 | 5994–6562 | 569 | 7.6 min | 19.9 | 73.53 dB | 95.08 dB | 3 | 0.999988 |
| S23 | 1213–1482 | 6563–6832 | 270 | 8.2 min | 8.8 | 66.18 dB | 80.63 dB | 2 | 0.999979 |
| S24 | 1483–1842 | 6833–7192 | 360 | 6.8 min | 14.1 | 66.93 dB | 77.81 dB | 0 | 0.999985 |
| S25 | 1843–2127 | 7193–7477 | 285 | 6.9 min | 11.0 | 77.63 dB | 85.45 dB | 1 | 0.999995 |
| S26 | 2128–2456 | 7478–7806 | 329 | 7.7 min | 11.4 | 62.91 dB | 66.66 dB | 24 | 0.999945 |
| S27 | 2457–2711 | 7807–8061 | 255 | 3.7 min | 18.1 | 66.99 dB | 74.07 dB | 64 | 0.999991 |

The remaining difference is the GPU's run-to-run noise (about one 10-bit level), which is why the bar is a PSNR and
not bit identity; `swangle` renders are bit-exact run to run.

**Running it again.** Allow 36 to 80 minutes of render. What made this run work:

1. Find the delivered file in the film's own records: its render registry gives the path, its queue log gives when it
   was rendered and when it was installed (a regression hold put six hours between the two here).
2. Compare against an APFS clone of it (`cp -c`) and hash both: no disk, and nothing can swap the file mid-run.
3. Match the raster mode before the long render: five 8 to 12 frame ranges settle it in a minute or two.
4. Read the scenes in place through a one-line re-export, and record the act's source files (an esbuild import walk
   with a sha256 per file) before and after bundling.
5. Bundle once, render shot by shot, prove each shot and delete it, so a resumed job never redoes a proven shot and
   the disk peak is one shot.
6. Run it as one queued job; the queue gates only its start on load, and the engine waits up to three minutes for a
   page (`renderTimeoutMs()`), so a busy Mac slows it rather than failing it.

## The motion-blur comparison (P282)

**The question.** Is Remotion's `HtmlInCanvasMotionBlur` (4.0.529 and later) good enough to replace render_mb, Slate's
renderer that draws every frame N times across the shutter and averages the samples in 16-bit? The rule: switch only
if it wins on both quality and speed.

**How.** A scratch engine at Remotion 4.0.532 with `@remotion/motion-blur` 4.0.532, installed in a scratch folder (the
pinned engine and `engine/` never changed). The demo film's kit act (228 frames, 1080×1920 at 30 fps) rendered with
`HtmlInCanvasMotionBlur` and with render_mb, both at 16 samples on the same centred 180° shutter, through Slate's
bridge inside queued jobs, and held against render_mb at 64 samples on every frame that moves.

**The result: render_mb stays the default.** `HtmlInCanvasMotionBlur` rendered about 12 times faster (7.5 to 15.6 s
against 112 to 150 s on a calm Mac) and lost on quality:

| Render (16 samples) | Worst frame | Mean | Still frames | Streak error, median (max) |
|---|---|---|---|---|
| render_mb, 4.0.521 (Slate's engine) | 61.36 dB | 85.36 dB | bit-exact | 0.11 levels RMS (4.5) |
| render_mb, 4.0.532 | 62.42 dB | 85.28 dB | bit-exact | 0.11 levels RMS (4.7) |
| HtmlInCanvasMotionBlur, kit clock patched | 40.70 dB | 43.40 dB | 43.33 dB | 1.90 levels RMS (34.2) |
| HtmlInCanvasMotionBlur, kit as it is | 38.12 dB | 43.09 dB | 43.33 dB | 4.72 levels RMS (38.2) |

It sums its samples in an ordinary 8-bit canvas, so 16 samples keep 4 bits a channel, on `swangle` too: still frames
shift (the warm page turns grey, shadows band) and streaks stray. It also needs the kit's clock to keep fractional
frames at one sample per frame; as the kit is, its samples land on two whole frames and moving type doubles. Look
again when a release sums in float or 16-bit. `engine/README.md` records the decision.
