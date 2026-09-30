// Encoding for the open web renderer: PNG frames in, a film-rate video out, averaged into motion blur on the way.
//
// This is render_mb's chain (engine/remotion/render_mb.mjs), kept here as a mirror rather than an import, because
// render_mb loads @remotion/renderer and the web renderer must run with no Remotion package at all (PLAN decision 5:
// "Slate doesn't depend on Remotion alone"). engine/tests/web/encode.test.mjs proves the two stay byte-identical,
// so a web act and a Remotion act come out of ffmpeg the same way and splice, master and gate the same way.
//
// Each master frame arrives as `sub` PNG sub-frames. ffmpeg averages every group of `sub` in 16-bit (tmix on
// rgb48), keeps one frame per group, converts to BT.709 limited range and encodes ProRes 422 HQ 10-bit 4:2:2 (a
// master) or lossless x264 in the same 10-bit 4:2:2 (a chunk the render module splices). A .png output is one
// averaged frame as an 8-bit RGB still, for review sheets and look tests.
import path from 'node:path';
import {spawn} from 'node:child_process';
import {EngineError, childEnv, requireTool, run, usage} from '../../remotion/lib/common.mjs';

export const DELIVERY_SAMPLES = 16;
export const DRAFT_SAMPLES = 4;
export const MAX_SAMPLES = 64;

/** ProRes 422 HQ, as the films' masters and After Effects expect it. */
export const PRORES = ['-c:v', 'prores_ks', '-profile:v', '3', '-vendor', 'apl0', '-pix_fmt', 'yuv422p10le'];
/** The lossless chunk intermediate: x264 qp 0 in the same 10-bit 4:2:2 the ProRes is made from. */
export const LOSSLESS = ['-c:v', 'libx264', '-qp', '0', '-preset', 'veryfast', '-pix_fmt', 'yuv422p10le'];
export const BT709 = ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709'];

/** The averaging steps: `sub` PNG sub-frames become one frame, in 16-bit. */
function averageSteps(sub) {
  const steps = ['format=rgb48le'];
  if (sub > 1) steps.push(`tmix=frames=${sub}`, `select='eq(mod(n\\,${sub})\\,${sub - 1})'`);
  return steps;
}

/**
 * The ffmpeg filter chain for `sub` samples at a film rate of num/den (render_mb's filterChain, step for step:
 * the BT.709 matrix is spelled out and setparams stamps all three colour tags on the frames, because ffmpeg 9 lets a
 * PNG's unknown tags override -color_primaries and -color_trc).
 */
export function filterChain(sub, fps) {
  return [
    ...averageSteps(sub),
    `setpts=N/(${fps.num}/${fps.den})/TB`,
    'scale=out_color_matrix=bt709:out_range=tv',
    'format=yuv422p10le',
    'setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv',
  ].join(',');
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

/** One averaged frame as an 8-bit RGB PNG (a still). */
export function stillArgs({sub, fps, out}) {
  return [
    '-y', '-v', 'error',
    '-f', 'image2pipe', '-framerate', `${fps.num * sub}/${fps.den}`, '-c:v', 'png', '-i', '-',
    '-vf', [...averageSteps(sub), 'format=rgb24'].join(','),
    '-frames:v', '1', '-update', '1',
    '-f', 'image2', '-c:v', 'png',
    out,
  ];
}

/**
 * What an output path asks for: {format, codec, still}. A .mov is ProRes (a master), a .mkv a lossless chunk, a .png
 * a single still. An explicit codec must agree with the container.
 */
export function outputKind(out, codec = null) {
  const ext = path.extname(out).toLowerCase();
  if (ext === '.png') {
    if (codec && codec !== 'png') throw usage(`A .png output is a still; codec ${JSON.stringify(codec)} doesn't apply.`);
    return {format: 'image2', codec: 'png', still: true};
  }
  const format = ext === '.mov' ? 'mov' : ext === '.mkv' ? 'matroska' : null;
  if (!format) throw usage(`Render to a .mov (ProRes), a .mkv (a lossless chunk) or a .png (one still), not ${ext || 'a file with no extension'}.`);
  const chosen = codec ?? (format === 'matroska' ? 'lossless' : 'prores');
  if (!['prores', 'lossless'].includes(chosen)) throw usage(`codec must be "prores" or "lossless" (got ${JSON.stringify(codec)}).`);
  if (chosen === 'prores' && format !== 'mov') throw usage('ProRes goes in a .mov.');
  return {format, codec: chosen, still: false};
}

/**
 * Start ffmpeg reading PNGs on stdin. Returns {write(buf), end(), done, kill()}: write resolves when ffmpeg can take
 * more (backpressure), done resolves when it exits cleanly and rejects with its last words when it doesn't.
 */
export function startEncoder(argv) {
  const ffmpeg = requireTool('ffmpeg', 'Install ffmpeg (`slate doctor --install` offers it).');
  const child = spawn(ffmpeg, argv, {env: childEnv(), stdio: ['pipe', 'ignore', 'pipe']});
  const err = [];
  child.stderr.on('data', (d) => err.push(d));
  const done = new Promise((resolve, reject) => {
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new EngineError(`ffmpeg exited ${code}: ${Buffer.concat(err).toString().trim().slice(-800)}`));
    });
  });
  done.catch(() => {}); // surfaced through `done` by whoever awaits it
  child.stdin.on('error', () => {}); // a dead ffmpeg surfaces through `done`
  return {
    child,
    write: (buf) => new Promise((resolve) => (child.stdin.write(buf) ? resolve() : child.stdin.once('drain', resolve))),
    end: () => child.stdin.end(),
    done,
    kill: () => {
      if (child.exitCode === null && !child.killed) child.kill('SIGKILL');
    },
  };
}

/** Frames in a video file, counted packet by packet (exact for ProRes and for intra or inter x264). */
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
  try {
    return JSON.parse(res.stdout).streams?.[0] ?? null;
  } catch {
    return null;
  }
}

/**
 * The md5 of every decoded frame (ffmpeg's framemd5): the proof two renders drew the same pictures, whatever the
 * container wrote around them. Splices are proven with the same hashes.
 */
export async function frameHashes(file) {
  const ffmpeg = requireTool('ffmpeg');
  const res = await run(ffmpeg, ['-v', 'error', '-i', file, '-map', '0:v:0', '-f', 'framemd5', '-']);
  if (res.code !== 0) throw new EngineError(`ffmpeg couldn't hash the frames of ${file}: ${res.stderr.trim().slice(-400)}`);
  return res.stdout
    .split('\n')
    .filter((l) => l && !l.startsWith('#'))
    .map((l) => l.split(',').pop().trim());
}
