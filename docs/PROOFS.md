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

## Data parity recovery, October 4 (P192 follow-up)

The October 1 full-suite run later reported four failing golden nodes. Their fresh investigation and receipts are
in `build_reports/codex-golden-audit.json`; `GOLDEN_DATA.md` explains the reusable snapshot workflow. This addendum
preserves the historical full P192 run above and states the narrower evidence obtained today.

The four repaired nodes passed together with no skips against 57 frozen private files (2,452,936,000 bytes), with
SHA-256 checks before and after the test session: **4 passed in 101.65 seconds, exit 0**. Twenty neutral regression
tests also pass, covering snapshot portability, mutation and capture drift, unsafe paths, original voice provenance
and ambiguous revisions. The private media remains outside Slate's repository.

A's test now pairs its v2 inputs with that same delivery's v2 record and stems; the current delivery pointer had
changed to p3. C's test uses its preserved 6,553-frame clock and original takes, instead of the later 6,503-frame
re-voice. It requires matching delivery provenance before independently checking each take at the original 0.5 ms
alignment and correlation bars. B's adapter now honors its source checker's meaning of absent pinned metadata
(no pinned spans); its exact 0.1 LU boundary also allows binary floating-point rounding. Expected measurements were
not regenerated, and the declared tolerances were not expanded to accommodate different media.

```sh
SLATE_GOLDEN_ROOT="$PRIVATE_ORACLE" SLATE_GOLDEN_HEAVY=1 nice -n 10 .venv/bin/python -m pytest \
  -p no:cacheprovider -v \
  'tests/sound/test_sound_golden.py::test_every_delivered_take_is_found_exactly_where_its_cue_says[C]' \
  'tests/sound/test_sound_golden.py::test_a_mixes_again_from_its_own_inputs[A-delivered]' \
  tests/sound_balance/test_sound_balance_golden.py
```

This closes those four known failures. It does not claim a fresh run of every P192 timing, caption, mix and sound
proof, and it does not grant creative approval or satisfy the effects engine's existing proof-3 boundary.

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

## Local implementation verification after timing repair, October 4

The completion pass records 4,818 Python passes, 22 explicit skips and zero failures/errors; TypeScript and
435 engine passes; all 23 replay-demo stages and all 17 cached fresh-home checks. Python verifies final source `ad1248ced2166bd12f78628e98b74100581564a53031d707f2bdcdc2a77e9911`. Engine, demo and fresh-home verify `c98177668a51ff6045f5c90397cb44a580d78d0e4b6f810ed055c8e343a72b5b`. The only difference is the corrected `tests/render/render_fakes.py` helper; runtime, dependency metadata, skills, knowledge and every other verified file are byte-identical. The exact delta and 95 passing focused checks are retained. Each phase records its own unchanged before/after source hash.

The timing-source repair has 22 new regression cases. Polished playback remains authoritative for listening and
mixing; verified pre-polish sources drive picture alignment and onset snapping. This record does not refresh all
historical golden proofs, authorize paid comparison or establish live brand/AE/client/release acceptance. The demo
retains seven expected UNCHECKED gates and delivers a draft. Full phase logs, source maps and the preserved failed
fixture run are identified in the completion workspace receipts and packaged manifest.

## Three mapped golden checks, October 4

Three previously skipped mapped tests pass on final ad1248… source, unchanged: B’s digital-silent ambience gate,
the real snap/wobble positive-control clips and C’s original archived v2 mix rebuilt from its own inputs. The queued
run exits 0 in 33.48 seconds; three passes, no failures/errors/skips. Its sixteen-file private snapshot contains
1,054,136,968 bytes, verified before/after with explicit C archive-to-test-layout bindings. The C expected record
and dialogue equal the earlier frozen oracle; no expected values or tolerances were regenerated. This is retired
plumbing parity, not current redesigned sound or final C approval.

The remaining mapped A boundary golden is unproved: its available renders postdate the expected boundary report;
alternative render paths share their inodes. No coherent earlier render oracle was found. The timestamp guard and
original measurements remain intact. Execution reconciliation now proves 411/412 gate-type test references; full
gate execution and the final approved-source harvest remain open. The main-suite receipt retains 4,818 passes/22 skips.

## Benchmark evidence repair, October 4


The focused formats run passed 154 checks, including 75 benchmark cases. Five preserved old false winning
decisions now have passing regressions. The complete Python run passed 4,878 with 22 explicit skips,
zero failures/errors. TypeScript/435 engine checks, 23 replay-demo stages and 17 cached fresh-home checks pass.
Python, TypeScript/engine, demo and fresh-home now verify one source `8ffa6a3034da50383cb59839358613410d2d1bd21edf919a0e8c1ec414948e1c`. Each phase records the same unchanged before/after file map. Earlier timing and fixture-equivalence receipts remain preserved as historical evidence; this benchmark repair changes runtime and CLI files. Three mapped goldens pass separately against the preserved original oracle; the A boundary golden remains
unproved. This verifies the offline harness, not a funded Higgsedit comparison or owner audiovisual approval.
See `docs/guides/higgsedit-benchmark.md` for protocol-v2 migration and exact-cut review commands.

## 2026-10-04 — Local comparison preparation

All 104 frozen alignment inputs now have local-only raw Whisper transcripts. The original nine are preserved; the other 95 completed once in 1483.61 seconds. The two source histories remain distinct, with exact matching runner, implementation, weights, wrapper, audio and script bindings. All 326 source/provenance files revalidated. This is model output and integrity evidence, not human onset truth, a paid alignment result or current-take approval. The paid request remains the named nine.

A new private protocol-v2 passport cut proposal binds a delivered source master, native clock and 862-frame range. It revalidated 194 inputs and recorded zero renders, runs or reviews. The external source needs a verified private Slate adaptation; vertical framing and the common fidelity anchor remain unreviewed. Funded benchmark execution and human acceptance are still open.

The separate reference-preview jobs now make that framing gap concrete. Corrected job `j-20261004-115915-c966` passed with two 862-frame excerpts, 48 kHz stereo audio and a 1,380,579-sample decoded-source PCM excerpt; all 14 dependencies stayed unchanged. Separate agents inspected all 862 thumbnails per version with full-size samples. The centre crop clips stamps, score digits and captions and omits the sample-data label. The wide excerpt retains the paired layout, with temporary source zoom clipping and in-motion entry/exit. A different shared framing reference and private source adaptation are required before competitive measurements. The original contradictory-flag ffmpeg failure remains preserved; the corrected preparation changed no application source or frozen benchmark protocol. No benchmark arm, paid call, human acceptance or listening approval is claimed.


## 2026-10-04 — Alignment comparison correctness verified

The comparator previously called substituted words matched: “durable” versus “banana” reported one match and zero disagreement. It now measures equal normalized occurrences only, preserves edits and coverage, binds current audio/script evidence and rejects malformed clocks. The preserved red run has 28 failures and three passes; focused repair checks pass 46 with one live-Whisper skip. The four-phase source `20b39b270a7400bb739ded1badf06474bce2f2c04bfd23da04ceea8f2d340dc3` passes 4,916 Python tests with 22 explicit skips, TypeScript/435 engine tests, all 23 replay-demo stages and 17 cached fresh-home checks. The source delta consists of two comparison/CLI files and one synthetic-regression file. All 14 prior non-comparison function bodies and inference settings/imports remain unchanged. Historical local baseline executions retain their old whole-source hashes; no new Whisper run is claimed. P293 paid/listener acceptance remains open.


## B native passport source proof

The [bounded source proof](~/Documents/Codex/2026-10-04/claude-video-creator-plugin/outputs/slate-b-source-proof.md) reconstructs 20 native frames around master frame 11818.
All 320 original shutter samples passed exact-byte/order/CRC guards, and the output has 20 complete 1001-tick frames.
The failed19-frame attempt remains preserved. Model review covers all 20 native pairs; root independently checks
the overview and three join pairs. Font/glyph and paper-contrast differences remain explicit. This is private
source adaptation evidence, not a full 862-frame benchmark, paid run, audio review or owner acceptance.


## Current Sol compatibility, October 4

All 12 original cases pass under the current GPT-6.1 Sol High/no-MCP desktop profile, with 102 original
grader checks passing. Each pair used one shared queued render slot, one attempt and no automatic resume.
Root inspected each terminal result before submitting the next. The [compatibility report](~/Documents/Codex/2026-10-04/claude-video-creator-plugin/outputs/slate-current-sol-compatibility.md)
and [independent audit](~/Documents/Codex/2026-10-04/claude-video-creator-plugin/outputs/review/current-sol-compatibility-independent-review/review.md) bind the executed
argv, grades, transcripts, source maps and 349 protected-file maps. Application source stayed `20b39b…`
through all pairs; no config metadata exception was needed.

The social-reel model was intentionally stopped at a detected ffmpeg render (exit -15); its queue worker
completed with exit 0. The launch case produced an uncut local silent 45-second static storyboard MP4.
Twenty recovered nonzero child commands remain in the evidence. The watchdog observes recognized render
entrypoints and descendant ffmpeg/rsvg/browser processes after startup; polling can miss brief encodes.
The host-action audit is observational, with opaque/computed-target limits. Neither guard is a kernel deny.
The UI-recording case proves routing and missing-fixture refusal, not a real Simulator capture.

The 48 host/GUI and seven config preparation controls are synthetic evidence, separate from the 102 checks.
Historical outcomes remain 43 PASS, 1 FAIL, 4 UNCHECKED and 1 PENDING. These current-profile results supply
no historical configured-client, live Oneiro/MCP/GUI, full-film, owner or production acceptance. The plan
remains 320 done and 61 unfinished. The [execution-era archive](~/Documents/Codex/2026-10-04/claude-video-creator-plugin/outputs/review/current-sol-compatibility/execution-source/slate-source-2026-10-04.tar.gz)
and manifest retain their original bytes before this documentation-only delivery refresh.


## B full native source draft: preparation and first chunk

The [full-source progress packet](~/Documents/Codex/2026-10-04/claude-video-creator-plugin/outputs/slate-b-full-source-progress.md) prepares all 862 native B37–B38
frames from the private copied Remotion source. Its frozen plan has 22 manual chunks and a separate assembly
job, with 13,792 shutter samples in total. Independent preparation review checked 223 inputs, 109 frozen
files, 55 source/adapter copies and 46 original source/font bindings. Root separately rehashed all 1,516
verified application entries; runtime remains `20b39b…`. No application file or original film was changed.

Only the first 40-frame chunk has rendered: 640 samples, exact 1001-tick frame intervals at 30000/1001,
silent ProRes HQ and unchanged inputs. The old 20-frame overlap matches 318/320 sample SHA records and
18/20 decoded frame SHA records. Exact repeatability fails and its initial refusal remains preserved.
Bounded diagnostics measure 18 and 4,272 changed RGB pixels in the two differing frames, maximum channel
deltas 3 and 5 out of 255, confined to the passport's top edge. Native 10-bit maxima are 2 and 7. Root
viewed four native images and seven overview/context/difference assets; no label or layout change was
visible in these selected pairs. The cause is unknown. Raw original sample PNGs were not retained, so
their pixel differences cannot be reconstructed from hashes.

An explicit internal decision allows continuation of this exact private source-draft artifact after a free
resource window is verified. It changes no expected hash, golden or general tolerance, and grants no owner,
audio or benchmark acceptance. The first chunk finished while the independently owned source-film pipeline
was active, before root's stop-if-active check; no cancellation occurred. Manual preflight now checks live
ffmpeg/headless command names, rejects failed or empty process inventory, requires at least 10 GiB free
with 5 GiB left after its reservation, and rechecks immediately before submission. The preserved failing
resource checks catch later source-film phases after the original PIDs exit. These observations do not
provide a cross-process lock on an independently owned pipeline.

The remaining 21 chunks (822 frames) and assembly are unrun in this packet. Full continuity, historical
pixel/font parity, a common vertical framing reference, paid comparison, audio and exact-cut human review
remain open. The earlier failed 19-frame and corrected 20-frame proofs retain their original evidence.
The plan remains 320 done and 61 unfinished; no new full application suite run or production acceptance
is claimed.


## 2026-10-04 — B native progress and guarded documentation refresh

The latest [private progress snapshot](~/Documents/Codex/2026-10-04/claude-video-creator-plugin/outputs/slate-b-native-progress-2026-10-04-1900.md) records nine terminal native wide chunks, 000–007 and 011: 360 of 862 frames and 5,760 of 13,792 shutter samples. Thirteen chunks, 502 frames and assembly remain unrun. Root technical inspections passed, and independent record reviews passed for 000–005. The independent 006–007 live audit refused read-time stat instability; its first missing terminal-output receipt and the fresh refusal remain explicit. Chunk 007 took 265 seconds while another observed pipeline began during its job window. This does not prove isolated render timing, overlap duration or the cause of that delay. No independent process was changed. Chunk 008 refused before submission first on an active ffmpeg and later on insufficient free disk.

The private portrait source is prepared for nine isolated diagnostic frames and 144 shutter samples, with no media submitted or produced. The author records six passing static commands and 227 unchanged inputs; the 219-file freeze and additive shutter map remain intact. Eleven native files changed and four were added; the parent snapshot remains separately preserved. Seven faces are reflowed and the book moves one pixel to x=445 after a preserved ideal-margin refusal. Caption grouping still fails 17 CPS, with two cues at 17.391304 and 17.434211. Imported word times already include the original 0.3-second voice placement. Independent live source/clock binding remains pending after a timeout and a later disk-full terminal-receipt failure; the second child exit is unavailable. Static preparation does not establish readable pixels, motion, contrast, a full portrait reference or owner acceptance.

A later documentation refresh encountered 125 timestamp-only differences in the original 349 protected files. Independent stable reads showed all nonmanifest bytes, sizes, modes, devices and inodes unchanged. The original strict-stat predicate remains false, and its failure is preserved. An independently reviewed exact per-path disposition allows only the listed current nanoseconds while keeping every other predicate and the separately approved manifest exception. No tolerance or baseline rewrite was introduced. The helper's bounded warm read requires matching warm/actual SHA values and retains the original actual-read stat guard; its static and simulated review is separate from any fresh delivery pass. The first finalizer read-stability refusal remains preserved.

Application runtime remains 20b39b with the existing 4,916 Python passes and 22 skips, 435 engine checks, 23 replay-demo stages and 17 cached fresh-home checks. The tracker remains 320 done and 61 unfinished. Approved Proof 3 v2, A p3 and latest B sound decisions stay unchanged; this progress adds no audio, funded benchmark, live production or owner acceptance. The new report copied only its 14 small author/root packet files. Its conditional 116-entry external copy plan was not executed. Earlier packets and archive states remain preserved.
