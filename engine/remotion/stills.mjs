// Stills from one bundle: review stills, labelled contact sheets, and any entry's compositions (the kit's review
// sheets). Generalized from the films' stills.mjs and src/c/kit/review/stills.mjs.
//
//   specs          ["Act2:120", "Film:900", …]: composition:frame, frames counted from the composition's first frame
//   contact_sheet  {comp, every_s | count | frames, columns, scale}: stills across a composition, each with its
//                  frame, shot and timecode burned into a corner label, tiled into one PNG with an index beside it
//   entry          another entry file with its own registerRoot (a kit review sheet); it still reads the film (or the
//                  neutral film) through '@film/data'
//
// Stills render with one motion-blur sample (sub 1) unless asked otherwise: a still is a look, not a delivery frame.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {renderStill, selectComposition} from '@remotion/renderer';
import {EngineError, log, requireTool, run, sha256Buffer, sha256File, timecode, usage} from './lib/common.mjs';
import {FILM_COMP, shotAtFrame} from './lib/film.mjs';
import {chromiumOptions} from './browser.mjs';
import {useBundle} from './bundle.mjs';
import {ensureEntry, refuseBroken} from './entry.mjs';
import {browserLogCollector, fontGate} from './kit_bridge.mjs';

function parseSpec(spec) {
  const m = /^([A-Za-z0-9-]+):(\d+)$/.exec(String(spec));
  if (!m) throw usage(`A still is "<composition>:<frame>", e.g. "Act2:120" (got ${JSON.stringify(spec)}).`);
  return {comp: m[1], frame: Number(m[2])};
}

const stillName = (comp, frame) => `${comp}_f${String(frame).padStart(5, '0')}.png`;

/** A composition's first film frame, for labels: Act<N> starts at its act, Film at the first act. */
function filmFrameOf(ctx, comp, frame) {
  if (comp === FILM_COMP) return (ctx.acts[0]?.frameIn ?? 0) + frame;
  const m = /^Act(\d+)$/.exec(comp);
  const a = m ? ctx.acts.find((x) => x.act === Number(m[1])) : null;
  return a ? a.frameIn + frame : null;
}

export function labelFor(ctx, comp, frame) {
  const ff = filmFrameOf(ctx, comp, frame);
  if (ff === null) return `${comp} · f${frame}`;
  const shot = shotAtFrame(ctx, ff);
  return `${comp} · f${frame} · ${shot ? `${shot.id} · ` : ''}${timecode(ff, ctx.fps.value)}`;
}

/** Hash of a PNG's decoded pixels (RGBA), so two stills compare by picture, never by PNG encoder bytes. */
export async function pixelHash(file) {
  const ffmpeg = requireTool('ffmpeg');
  const res = await run(ffmpeg, ['-v', 'error', '-i', file, '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'], {binary: true});
  if (res.code !== 0) throw usage(`Couldn't decode ${file}: ${res.stderr.trim()}`);
  return sha256Buffer(res.stdout);
}

/** PSNR between two stills (dB; Infinity when identical), for evidence when a contract doesn't match. */
export async function psnr(a, b) {
  const ffmpeg = requireTool('ffmpeg');
  const res = await run(ffmpeg, ['-v', 'info', '-i', a, '-i', b, '-lavfi', 'psnr', '-f', 'null', '-']);
  const m = /average:(inf|[\d.]+)/.exec(res.stderr);
  if (!m) return null;
  return m[1] === 'inf' ? Infinity : Number(m[1]);
}

/**
 * Render stills. `comps` caches selectComposition per id. Returns [{comp, frame, file, sha256, pixels}].
 * opts: {serve_url, gl, scale, sub, props, labels (bool), outDir}
 */
export async function renderStills(ctx, b, list, opts) {
  const cache = new Map();
  const out = [];
  // collects the kit's font gate line when a caller passes opts.logs (a browserLogCollector)
  const onBrowserLog = opts.logs?.onBrowserLog;
  fs.mkdirSync(opts.outDir, {recursive: true});
  for (const {comp, frame, label, name} of list) {
    const inputProps = {...(opts.props ?? {}), sub: opts.sub ?? 1};
    if (!cache.has(comp)) {
      cache.set(comp, await selectComposition({serveUrl: b.serve_url, id: comp, inputProps, chromiumOptions: chromiumOptions(opts.gl), logLevel: 'warn', onBrowserLog}));
    }
    const composition = cache.get(comp);
    const lastFrame = Math.round(composition.durationInFrames / (inputProps.sub || 1)) - 1;
    if (frame < 0 || frame > lastFrame) throw usage(`${comp} has frames 0–${lastFrame}; ${frame} isn't one of them.`);
    const file = path.join(opts.outDir, name ?? stillName(comp, frame));
    const props = label ? {...inputProps, label} : inputProps;
    // With sub > 1 the composition is longer: master frame f is drawn at f × sub + (sub − 1), the last sample.
    // The component receives the composition's resolved props (fixed when it was selected), so a per-still label
    // goes into them here, not only into inputProps.
    await renderStill({
      composition: {...composition, props: {...composition.props, ...props}},
      serveUrl: b.serve_url,
      output: file,
      frame: frame * inputProps.sub + (inputProps.sub - 1),
      scale: opts.scale,
      inputProps: props,
      chromiumOptions: chromiumOptions(opts.gl),
      overwrite: true,
      logLevel: 'warn',
      onBrowserLog,
    });
    out.push({comp, frame, file, sha256: await sha256File(file), pixels: opts.pixels ? await pixelHash(file) : undefined});
  }
  return out;
}

/** A PNG's pixel size, read from its IHDR chunk (bytes 16–24), or null when the file isn't a PNG. */
export function pngSize(file) {
  const fd = fs.openSync(file, 'r');
  try {
    const head = Buffer.alloc(24);
    if (fs.readSync(fd, head, 0, 24, 0) < 24 || head.toString('latin1', 12, 16) !== 'IHDR') return null;
    return [head.readUInt32BE(16), head.readUInt32BE(20)];
  } finally {
    fs.closeSync(fd);
  }
}

async function tile(cells, {columns, outFile, pad}) {
  const ffmpeg = requireTool('ffmpeg');
  const rows = Math.ceil(cells.length / columns);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-sheet-'));
  try {
    // The tile filter wants a numbered sequence; blanks fill the last row so every sheet is a full grid.
    const total = rows * columns;
    for (let i = 0; i < total; i++) {
      const dest = path.join(tmp, `c${String(i).padStart(4, '0')}.png`);
      if (i < cells.length) fs.copyFileSync(cells[i].file, dest);
      else fs.copyFileSync(cells[0].file, dest);
    }
    const blanks = total - cells.length;
    const vf = [
      // blank out the filler cells (drawbox over the copies) so they read as empty
      blanks ? `drawbox=enable='gte(n\\,${cells.length})':x=0:y=0:w=iw:h=ih:color=0x1b1b1b:t=fill` : null,
      `tile=${columns}x${rows}:padding=${pad}:margin=${pad}:color=0x1b1b1b`,
    ].filter(Boolean).join(',');
    const res = await run(ffmpeg, ['-y', '-v', 'error', '-framerate', '1', '-i', path.join(tmp, 'c%04d.png'), '-vf', vf, '-frames:v', '1', outFile]);
    if (res.code !== 0) throw usage(`ffmpeg couldn't tile the contact sheet: ${res.stderr.trim()}`);
    return {rows, columns};
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
  }
}

function sheetFrames(ctx, comp, frames, spec) {
  if (Array.isArray(spec.frames) && spec.frames.length) return spec.frames.map(Number);
  if (spec.count) {
    const n = Math.max(1, Number(spec.count));
    return [...new Set(Array.from({length: n}, (_, i) => Math.round((i * (frames - 1)) / Math.max(1, n - 1))))];
  }
  const every = Number(spec.every_s ?? 0.5);
  if (!(every > 0)) throw usage('contact_sheet.every_s must be a positive number of seconds.');
  const step = every * ctx.fps.value;
  const out = [];
  for (let f = 0; Math.round(f) < frames; f += step) out.push(Math.round(f));
  return [...new Set(out)];
}

/** The stills command. */
export async function stillsCommand(ctx, args) {
  const outDir = path.resolve(args.out_dir ?? path.join(ctx.dir, 'review', 'stills'));
  const gl = args.gl ?? 'angle';
  const scale = Number(args.scale ?? 0.5);
  const sub = Number(args.sub ?? 1);
  if (!(scale > 0 && scale <= 4)) throw usage(`scale must be between 0 and 4 (got ${args.scale}).`);
  if (!args.specs?.length && !args.contact_sheet) throw usage('Ask for stills ("specs": ["Act1:0", …]) or a contact sheet ("contact_sheet": {"comp": "Act1"}).');
  const specs = (args.specs ?? []).map(parseSpec);
  if (!args.entry) {
    // the film's own compositions: one whose act doesn't build is refused by name, before any bundling
    const info = await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
    for (const comp of new Set([...specs.map((s) => s.comp), ...(args.contact_sheet ? [args.contact_sheet.comp ?? FILM_COMP] : [])])) refuseBroken(info, comp);
  }
  const b = await useBundle(ctx, args);
  const logs = browserLogCollector();
  try {
    const result = {out_dir: outDir, bundle_hash: b.hash, bundle_reused: b.reused, stills: [], sheet: null, gates: []};
    if (specs.length) {
      result.stills = await renderStills(ctx, b, specs, {outDir, gl, scale, sub, props: args.props, pixels: Boolean(args.pixels), logs});
      for (const s of result.stills) log(s.file);
    }
    if (args.contact_sheet) {
      const cs = args.contact_sheet;
      const comp = cs.comp ?? FILM_COMP;
      const composition = await selectComposition({serveUrl: b.serve_url, id: comp, inputProps: {sub: 1}, chromiumOptions: chromiumOptions(gl), logLevel: 'warn'});
      const frames = sheetFrames(ctx, comp, composition.durationInFrames, cs);
      const labels = cs.labels ?? !args.entry; // the generated Root draws labels; another entry may not
      const cellDir = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-cells-'));
      try {
        const cells = await renderStills(
          ctx,
          b,
          frames.map((frame) => ({comp, frame, label: labels ? labelFor(ctx, comp, frame) : undefined})),
          {outDir: cellDir, gl, scale: Number(cs.scale ?? 0.25), sub: 1, props: args.props, logs},
        );
        const name = cs.name ?? `${comp}_sheet`;
        const file = path.join(outDir, `${name}.png`);
        fs.mkdirSync(outDir, {recursive: true});
        const pad = Number(cs.padding ?? 8);
        const grid = await tile(cells, {columns: Math.max(1, Number(cs.columns ?? 6)), outFile: file, pad});
        // The sheet's geometry, so a reader (slate qa access) can cut the cells back out without guessing: every cell
        // is `cell` px, with `padding` px between cells and a `margin` of the same all round (ffmpeg's tile filter).
        const index = {
          generated: 'do not edit by hand: slate stills (contact sheet)',
          comp,
          file: path.basename(file),
          ...grid,
          cell: pngSize(cells[0].file),
          padding: pad,
          margin: pad,
          labels: Boolean(labels),
          cells: frames.map((frame, i) => {
            const ff = filmFrameOf(ctx, comp, frame);
            const shot = ff === null ? null : shotAtFrame(ctx, ff);
            return {cell: i, frame, film_frame: ff, shot: shot?.id ?? null, timecode: ff === null ? null : timecode(ff, ctx.fps.value)};
          }),
        };
        fs.writeFileSync(path.join(outDir, `${name}.json`), JSON.stringify(index, null, 1) + '\n');
        result.sheet = {file, index: path.join(outDir, `${name}.json`), cells: frames.length, ...grid, labels};
        log(file);
      } finally {
        fs.rmSync(cellDir, {recursive: true, force: true});
      }
    }
    const fonts = await fontGate(logs.lines);
    if (fonts) result.gates.push(fonts);
    return result;
  } catch (e) {
    const fonts = await fontGate([...logs.lines, String(e?.message ?? '')]);
    if (fonts && fonts.status !== 'PASS') {
      throw new EngineError(`A still stopped: the font gate failed (${fonts.summary}).`, {
        kind: 'refused',
        hint: "The brand's fonts must render as declared, never substituted: install or register the missing face.",
        details: {gates: [fonts]},
      });
    }
    throw e;
  } finally {
    b.release();
  }
}
