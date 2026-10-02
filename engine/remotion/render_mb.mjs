// render_mb: real motion blur, no intermediates on disk. Generalized from the films' pipeline/r3f/render_mb.mjs.
//
// Each master frame is drawn `sub` times across a 180° shutter centred on the frame (phase −90°); the act does the
// sub-frame timing itself through its `sub` prop. The PNG frames stream straight from headless Chrome into ffmpeg,
// which averages each group in 16-bit (tmix on rgb48) and encodes ProRes 422 HQ, 10-bit 4:2:2, BT.709, at the film's
// own rate. 16 samples is the delivery bar; 4 is draft. The disk only ever sees the result.
//
// Chunks: a range (from/to, master frames, inclusive, counted from the composition's first frame) written to .mkv is
// a lossless intermediate (x264 at qp 0, the same 10-bit 4:2:2 the ProRes is encoded from; 8–13× smaller), which the
// render module splices and conforms to ProRes. Every output is written to <out>.part, its frames are counted with
// ffprobe, and only an exact count replaces <out>. A short render is a failed render, never a quiet one.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {renderFrames, selectComposition} from '@remotion/renderer';
import {EngineError, childEnv, log, refused, requireTool, run, sha256File, usage} from './lib/common.mjs';
import {parseTarget} from './lib/film.mjs';
import {browserExecutable, chromiumOptions, renderTimeoutMs} from './browser.mjs';
import {useBundle} from './bundle.mjs';
import {ensureEntry, refuseBroken} from './entry.mjs';
import {browserLogCollector, fontGate} from './kit_bridge.mjs';

export const DELIVERY_SAMPLES = 16;
export const DRAFT_SAMPLES = 4;

/** ProRes 422 HQ, as the films' masters and After Effects expect it. */
export const PRORES = ['-c:v', 'prores_ks', '-profile:v', '3', '-vendor', 'apl0', '-pix_fmt', 'yuv422p10le'];
/** The lossless chunk intermediate: x264 qp 0 in the same 10-bit 4:2:2 the ProRes is made from. */
export const LOSSLESS = ['-c:v', 'libx264', '-qp', '0', '-preset', 'veryfast', '-pix_fmt', 'yuv422p10le'];
export const BT709 = ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709'];

/**
 * The ffmpeg filter chain for `sub` samples at a film rate of num/den.
 *
 * The RGB→YUV matrix is spelled out (BT.709, limited range). ffmpeg 9 already negotiates it from the output tags,
 * and the explicit form is bit-identical there (checked 2026-09-30); older builds default to BT.601, which would
 * shift saturated colours while the file claims BT.709.
 *
 * setparams then stamps all three colour tags on the frames. ffmpeg 9 lets the frames' own metadata (unknown, from
 * PNG) override -color_primaries and -color_trc, so the films' renders came out tagged with the BT.709 matrix but
 * unknown primaries and transfer (one of the films' act renders, probed 2026-09-30). The pixels are identical
 * either way (same frame md5); only the tags, which After Effects reads, change.
 */
export function filterChain(sub, fps) {
  const steps = ['format=rgb48le'];
  if (sub > 1) {
    steps.push(`tmix=frames=${sub}`, `select='eq(mod(n\\,${sub})\\,${sub - 1})'`);
  }
  steps.push(
    `setpts=N/(${fps.num}/${fps.den})/TB`,
    'scale=out_color_matrix=bt709:out_range=tv',
    'format=yuv422p10le',
    'setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv',
  );
  return steps.join(',');
}

/** The full ffmpeg argv reading PNGs on stdin and writing `out` in `codec` ('prores' or 'lossless'). */
export function ffmpegArgs({sub, fps, codec, out, format}) {
  return [
    '-y', '-v', 'error',
    '-f', 'image2pipe', '-framerate', `${fps.num * sub}/${fps.den}`, '-c:v', 'png', '-i', '-',
    '-vf', filterChain(sub, fps),
    '-r', `${fps.num}/${fps.den}`, '-fps_mode', 'cfr',
    ...(codec === 'lossless' ? LOSSLESS : PRORES),
    ...BT709,
    '-f', format,
    out,
  ];
}

/** Frames in a video file, counted packet by packet (exact for ProRes and for intra/inter x264). */
export async function countFrames(file) {
  const ffprobe = requireTool('ffprobe');
  const res = await run(ffprobe, ['-v', 'error', '-count_packets', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_packets', '-of', 'csv=p=0', file]);
  if (res.code !== 0) throw new EngineError(`ffprobe couldn't read ${file}: ${res.stderr.trim()}`);
  return Number(res.stdout.trim());
}

/** Stream facts for the render's evidence: codec, profile, pixel format, colour tags, rate, size. */
export async function probe(file) {
  const ffprobe = requireTool('ffprobe');
  const res = await run(ffprobe, ['-v', 'error', '-select_streams', 'v:0', '-show_entries',
    'stream=codec_name,profile,pix_fmt,color_space,color_transfer,color_primaries,r_frame_rate,width,height', '-of', 'json', file]);
  if (res.code !== 0) return null;
  return JSON.parse(res.stdout).streams?.[0] ?? null;
}

function containerFor(out) {
  const ext = path.extname(out).toLowerCase();
  if (ext === '.mov') return 'mov';
  if (ext === '.mkv') return 'matroska';
  throw usage(`Render to a .mov (ProRes) or a .mkv (a lossless chunk), not ${ext || 'a file with no extension'}.`);
}

function samplesFrom(args) {
  if (args.sub !== undefined && args.sub !== null) {
    const n = Number(args.sub);
    if (!Number.isInteger(n) || n < 1 || n > 64) throw usage(`sub must be a whole number from 1 to 64 (got ${JSON.stringify(args.sub)}).`);
    return n;
  }
  return args.draft ? DRAFT_SAMPLES : DELIVERY_SAMPLES;
}

function defaultConcurrency() {
  // Leave the Mac usable: at most half the cores, never more than the films' 8.
  return Math.max(1, Math.min(8, Math.floor(os.cpus().length / 2)));
}

/**
 * Render one composition (an act or the whole film) with motion blur.
 * args: {film (ctx), act | comp, from?, to?, sub?, draft?, out?, codec?, concurrency?, gl?, serve_url?, props?}
 */
export async function renderMb(ctx, args, {onTrace = null} = {}) {
  const ffmpeg = requireTool('ffmpeg', 'Install ffmpeg (`slate doctor --install` offers it).');
  requireTool('ffprobe');
  const target = parseTarget(ctx, args.act ?? args.comp);
  const sub = samplesFrom(args);
  const out = path.resolve(args.out ?? path.join(ctx.dir, 'renders', `${target.comp.toLowerCase()}.mov`));
  const format = containerFor(out);
  const codec = args.codec ?? (format === 'matroska' ? 'lossless' : 'prores');
  if (!['prores', 'lossless'].includes(codec)) throw usage(`codec must be "prores" or "lossless" (got ${JSON.stringify(codec)}).`);
  if (codec === 'prores' && format !== 'mov') throw usage('ProRes goes in a .mov.');
  const gl = args.gl ?? 'angle';
  const concurrency = Number(args.concurrency ?? defaultConcurrency());
  const inputProps = {...(args.props ?? {}), sub};
  // An act whose module doesn't build is left out of the bundle (so the others still render): refuse it here, by name,
  // before any bundling.
  refuseBroken(await ensureEntry(ctx, {brandPath: args.brand_path ?? null}), target.comp);

  const b = await useBundle(ctx, args);
  const part = `${out}.part`;
  // the kit's font gate prints its verdict in the page; it becomes the render's `fonts` gate result
  const logs = browserLogCollector();
  let ff = null;
  const stop = () => {
    if (ff && !ff.killed) ff.kill('SIGKILL');
    fs.rmSync(part, {force: true});
  };
  process.once('exit', stop);
  try {
    const composition = await selectComposition({serveUrl: b.serve_url, id: target.comp, inputProps, chromiumOptions: chromiumOptions(gl), browserExecutable: browserExecutable(), timeoutInMilliseconds: renderTimeoutMs(), logLevel: 'warn', onBrowserLog: logs.onBrowserLog});
    const masterFrames = Math.round(composition.durationInFrames / sub);
    if (masterFrames !== target.frames) {
      throw refused(
        `${target.comp} registers ${masterFrames} frames but timing/shots.json gives it ${target.frames}.`,
        'Regenerate the entry (it is rewritten before every render); if this persists the act ignores its `sub` prop.',
      );
    }
    const from = Number(args.from ?? 0);
    const to = Math.min(Number(args.to ?? masterFrames - 1), masterFrames - 1);
    if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || to < from) {
      throw usage(`Frame range ${args.from ?? 0}–${args.to ?? masterFrames - 1} isn't inside ${target.comp}'s 0–${masterFrames - 1}.`);
    }
    const nMaster = to - from + 1;
    const frameRange = [from * sub, (to + 1) * sub - 1];
    fs.mkdirSync(path.dirname(out), {recursive: true});
    fs.rmSync(part, {force: true});

    ff = spawn(ffmpeg, ffmpegArgs({sub, fps: ctx.fps, codec, out: part, format}), {env: childEnv(), stdio: ['pipe', 'ignore', 'pipe']});
    const ffErr = [];
    ff.stderr.on('data', (d) => ffErr.push(d));
    const ffDone = new Promise((resolve, reject) => {
      ff.on('error', reject);
      ff.on('close', (code) => (code === 0 ? resolve() : reject(new EngineError(`ffmpeg exited ${code}: ${Buffer.concat(ffErr).toString().trim().slice(-800)}`))));
    });
    ff.stdin.on('error', () => {}); // a dead ffmpeg surfaces through ffDone

    // Frames can finish out of order; they go to ffmpeg strictly in order.
    let next = frameRange[0];
    const pending = new Map();
    const write = (buf) => new Promise((resolve) => (ff.stdin.write(buf) ? resolve() : ff.stdin.once('drain', resolve)));
    const flush = async () => {
      while (pending.has(next)) {
        const buf = pending.get(next);
        pending.delete(next);
        next++;
        await write(buf);
      }
    };
    const t0 = Date.now();
    let lastLog = 0;
    const total = nMaster * sub;
    log(`${target.comp}: ${nMaster} frames × ${sub} samples → ${path.basename(out)} (${codec}, gl ${gl}, concurrency ${concurrency})`);
    await renderFrames({
      composition,
      serveUrl: b.serve_url,
      inputProps,
      outputDir: null,
      imageFormat: 'png',
      frameRange,
      concurrency,
      chromiumOptions: chromiumOptions(gl),
      // every tile finished before a sample is drawn (chrome-complete.sh)
      browserExecutable: browserExecutable(),
      timeoutInMilliseconds: renderTimeoutMs(),
      logLevel: 'warn',
      onBrowserLog: logs.onBrowserLog,
      onStart: () => {},
      onFrameUpdate: (done) => {
        const now = Date.now();
        if (now - lastLog > 15000) {
          lastLog = now;
          const rate = done / ((now - t0) / 1000);
          log(`  ${done}/${total} samples, ${rate.toFixed(1)}/s, ~${Math.round((total - done) / Math.max(rate, 1e-6))} s left`);
        }
      },
      onFrameBuffer: async (buffer, frame) => {
        pending.set(frame, buffer);
        await flush();
      },
    });
    await flush();
    ff.stdin.end();
    await ffDone;

    const count = await countFrames(part);
    if (count !== nMaster) {
      // Keep the short render for a look (as render_local.sh did), under a name nobody mistakes for the real one.
      const short = out.replace(/(\.[^.]+)$/, '.short$1');
      fs.renameSync(part, short);
      throw refused(
        `${target.comp} rendered ${count} frames, but ${nMaster} were asked for; ${path.basename(out)} is untouched.`,
        'Render the range again; if it repeats, look for frames that throw in the act.',
        {short_render: short, frame_count: count, expected: nMaster},
      );
    }
    fs.renameSync(part, out);
    const seconds = (Date.now() - t0) / 1000;
    log(`${path.basename(out)}: ${nMaster} frames in ${seconds.toFixed(0)} s`);
    const result = {
      file: out,
      comp: target.comp,
      act: target.kind === 'act' ? target.act : null,
      frames: [from, to],
      film_frames: [target.frameIn + from, target.frameIn + to + 1],
      frame_count: count,
      samples: sub,
      codec,
      gl,
      fps: ctx.fps.rational,
      width: ctx.width,
      height: ctx.height,
      sha256: await sha256File(out),
      bundle_hash: b.hash,
      bundle_reused: b.reused,
      seconds: Math.round(seconds * 10) / 10,
      stream: await probe(out),
      gates: [],
    };
    const fonts = await fontGate(logs.lines);
    if (fonts) result.gates.push(fonts);
    if (args.trace && onTrace && target.kind === 'act') {
      // the same '@film/data' (brand included) the picture rendered with; the scene files it read stay out of the result
      const {sources: _sources, ...trace} = await onTrace(ctx, target.act, {brandPath: args.brand_path ?? null});
      result.trace = trace;
    }
    return result;
  } catch (e) {
    // A failed font gate cancels the render with its report in the message: hand the gate result back with the error.
    const fonts = await fontGate([...logs.lines, String(e?.message ?? '')]);
    if (fonts && fonts.status !== 'PASS') {
      throw new EngineError(`${target.comp} stopped: the font gate failed (${fonts.summary}).`, {
        kind: 'refused',
        hint: "The brand's fonts must render as declared, never substituted: install or register the missing face.",
        details: {gates: [fonts]},
      });
    }
    throw e;
  } finally {
    process.removeListener('exit', stop);
    if (ff && ff.exitCode === null) ff.kill('SIGKILL');
    fs.rmSync(part, {force: true});
    b.release();
  }
}
