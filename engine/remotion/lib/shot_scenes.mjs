// One source resolver for both directions of a mixed code-engine act.
// Frame ranges stay on the owning act clock; timing rows retain film-global half-open bounds.
import fs from 'node:fs';
import path from 'node:path';
import {readJson, refused, sha256Buffer} from './common.mjs';

export const REVERSE_COMPOSITION_CAPABILITY = Object.freeze({schema: 1, enabled: true});
export const SCENE_EXTENSIONS = ['.tsx', '.ts', '.jsx', '.js'];

export function remotionIndex(dir) {
  for (const ext of SCENE_EXTENSIONS) {
    const file = path.join(dir, 'index' + ext);
    if (fs.statSync(file, {throwIfNoEntry: false})?.isFile()) return file;
  }
  return null;
}

export function baseScene(ctx, act) {
  const dir = path.join(ctx.dir, 'scenes', 'act' + Number(act));
  const ae = path.join(ctx.dir, 'scenes', 'ae', 'act' + Number(act) + '.json');
  if (fs.statSync(ae, {throwIfNoEntry: false})?.isFile()) return {engine: 'ae', scene: path.dirname(ae), index: ae};
  const remotion = remotionIndex(dir);
  if (remotion) return {engine: 'remotion', scene: dir, index: remotion};
  const web = path.join(dir, 'web', 'index.html');
  if (fs.statSync(web, {throwIfNoEntry: false})?.isFile()) return {engine: 'web', scene: path.dirname(web), index: web};
  return null;
}

export function sourceFile(ctx, rel) {
  const own = path.join(ctx.dir, rel);
  if (fs.existsSync(own)) return own;
  const refs = path.join(ctx.dir, '.slate', 'refs.json');
  if (!fs.existsSync(refs)) return own;
  const map = readJson(refs);
  const target = map?.[rel];
  return typeof target === 'string' && path.isAbsolute(target) && fs.existsSync(target) ? target : own;
}

/** Normalize existing sources and missing candidates through the nearest real parent (macOS temp aliases included). */
export function canonicalSource(file) {
  let cursor = path.resolve(file);
  const tail = [];
  while (!fs.existsSync(cursor)) {
    const parent = path.dirname(cursor);
    if (parent === cursor) break;
    tail.unshift(path.basename(cursor));
    cursor = parent;
  }
  return path.join(fs.realpathSync(cursor), ...tail);
}

/** Observe actual read bytes, and deliberate absence separately. A later changed read refuses. */
export function sourceCapture(initial = {}) {
  const hashes = {};
  for (const [file, digest] of Object.entries(initial)) {
    const full = canonicalSource(file);
    if (Object.hasOwn(hashes, full) && hashes[full] !== digest) throw refused('Conflicting captured source aliases: ' + file);
    hashes[full] = digest;
  }
  const record = (file, bytes = undefined) => {
    const full = canonicalSource(file);
    const data = bytes === undefined ? (fs.statSync(full, {throwIfNoEntry: false})?.isFile() ? fs.readFileSync(full) : null) : bytes;
    const digest = data === null ? null : sha256Buffer(data);
    if (Object.hasOwn(hashes, full) && hashes[full] !== digest) throw refused('Source changed while reading mixed act: ' + full);
    hashes[full] = digest;
    return data;
  };
  const verify = () => {
    for (const [file, digest] of Object.entries(hashes)) {
      const now = fs.statSync(file, {throwIfNoEntry: false})?.isFile() ? sha256Buffer(fs.readFileSync(file)) : null;
      if (now !== digest) throw refused('Source changed after mixed-act read: ' + file);
    }
  };
  return {hashes, record, verify};
}

function shotId(value) {
  if (typeof value !== 'string' || !value || value === '.' || value === '..' ||
      path.basename(value) !== value || value.includes('\\') || value.includes('\0')) {
    throw refused('A mixed shot has no safe source-folder id.');
  }
  return value;
}

/** Resolve declarations from timing first, then current script (including the legacy lines form). */
export function mixedScenesForAct(ctx, act, {capture = null} = {}) {
  const n = Number(act);
  const owner = ctx.acts.find((a) => a.act === n);
  if (!owner) throw refused('No owning act ' + act + ' for mixed source resolution.');
  const base = baseScene(ctx, n);
  const scriptFile = path.join(ctx.dir, 'script', 'script.json');
  const scriptBytes = capture ? capture.record(scriptFile) : fs.existsSync(scriptFile) ? fs.readFileSync(scriptFile) : null;
  const fallback = new Map();
  if (scriptBytes !== null) {
    let script;
    try { script = JSON.parse(scriptBytes.toString('utf8')); }
    catch { throw refused('script/script.json cannot be read for per-shot ownership.'); }
    for (const a of script?.acts ?? []) {
      for (const row of (a?.shots?.length ? a.shots : a?.lines ?? [])) {
        if (row?.id && row.engine) fallback.set(String(row.id), String(row.engine));
      }
    }
  }
  const all = ctx.shots?.shots;
  if (!Array.isArray(all)) throw refused('Mixed ownership has no resolved shot list.');
  const ids = new Set();
  for (const row of all) {
    if (!row || typeof row !== 'object' || Array.isArray(row) || !Number.isInteger(row.act) || row.act <= 0) {
      throw refused('A resolved mixed shot needs a positive whole owning act.');
    }
    const id = shotId(row.id);
    if (ids.has(id)) throw refused('Resolved shot id is repeated: ' + id);
    ids.add(id);
  }
  const result = [];
  let previousEnd = owner.frameIn;
  for (const row of all.filter((s) => Number(s.act) === n).sort((a, b) => a.frame_in - b.frame_in)) {
    const id = shotId(row.id);
    if (!Number.isInteger(row.frame_in) || !Number.isInteger(row.frame_out) ||
        row.frame_in < owner.frameIn || row.frame_out > owner.frameOut || row.frame_out <= row.frame_in) {
      throw refused('Mixed shot ' + id + ' has no bounded film interval in its owning act.');
    }
    if (row.frame_in < previousEnd) throw refused('Resolved shot intervals overlap inside act ' + n + '.');
    previousEnd = row.frame_out;
    const root = path.join(ctx.dir, 'scenes', 'shots', id);
    const candidates = {
      web: path.join(root, 'web', 'index.html'),
      remotion: remotionIndex(path.join(root, 'remotion')),
    };
    if (capture) {
      capture.record(candidates.web);
      for (const ext of SCENE_EXTENSIONS) capture.record(path.join(root, 'remotion', 'index' + ext));
    }
    const found = Object.keys(candidates).filter((engine) => candidates[engine] &&
      fs.statSync(candidates[engine], {throwIfNoEntry: false})?.isFile());
    const engine = row.engine || fallback.get(id) || null;
    if (engine && !['web', 'remotion', 'ae'].includes(engine)) throw refused('Shot ' + id + ' declares unknown engine ' + engine + '.');
    if (found.length > 1) throw refused('Shot ' + id + ' has competing Web and Remotion source entries.');
    if (engine === 'ae' || base?.engine === 'ae') {
      if (found.length || engine && engine !== base?.engine) throw refused('After Effects per-shot composition is not built.');
      continue;
    }
    if (!found.length) {
      if (engine && base && engine !== base.engine) throw refused('Shot ' + id + ' declares ' + engine + ' but its replacement source is missing.');
      continue;
    }
    if (!base) throw refused('Shot ' + id + ' has replacement sources but its owning act is not built.');
    if (!engine || found[0] !== engine) throw refused('Shot ' + id + ' source does not agree with its declared engine.');
    if (engine === base.engine) throw refused('Shot ' + id + ' uses its owning base engine; a separate replacement scene is ambiguous.');
    const index = candidates[engine];
    result.push({shot: id, act: n, engine, base_engine: base.engine,
      scene: path.dirname(index), index, frames: [row.frame_in - owner.frameIn, row.frame_out - 1 - owner.frameIn],
      comp: engine === 'remotion' ? 'Shot-' + id : null, shot_record: row});
  }
  const shotRoot = path.join(ctx.dir, 'scenes', 'shots');
  if (fs.existsSync(shotRoot)) {
    for (const entry of fs.readdirSync(shotRoot, {withFileTypes: true})) {
      if (!entry.isDirectory() || ids.has(entry.name)) continue;
      if (remotionIndex(path.join(shotRoot, entry.name, 'remotion')) ||
          fs.statSync(path.join(shotRoot, entry.name, 'web', 'index.html'), {throwIfNoEntry: false})?.isFile()) {
        throw refused('Replacement source ' + entry.name + ' has no resolved shot owner.');
      }
    }
  }
  return result;
}

export function frameOwner(ctx, act, frame) {
  const n = Number(act);
  const a = ctx.acts.find((item) => item.act === n);
  if (!a || !Number.isInteger(frame) || frame < 0 || frame >= a.frames) throw refused('Owned frame is outside its act.');
  const replacement = mixedScenesForAct(ctx, n).find((item) => frame >= item.frames[0] && frame <= item.frames[1]);
  return replacement ?? {...baseScene(ctx, n), act: n, shot: null, comp: 'Act' + n,
    frames: [0, a.frames - 1], base_engine: baseScene(ctx, n)?.engine ?? null};
}

/** Snapshot every context read used to generate aliases before module/page reads begin. */
export function contextCapture(ctx, {brandPath = null} = {}) {
  const capture = sourceCapture();
  const filmBytes = capture.record(path.join(ctx.dir, 'film.json'));
  const shotBytes = capture.record(sourceFile(ctx, 'timing/shots.json'));
  if (filmBytes === null || shotBytes === null ||
      JSON.stringify(JSON.parse(filmBytes.toString('utf8'))) !== JSON.stringify(ctx.film) ||
      JSON.stringify(JSON.parse(shotBytes.toString('utf8'))) !== JSON.stringify(ctx.shots)) {
    throw refused('Film context changed after its owning clock was loaded.');
  }
  capture.record(path.join(ctx.dir, '.slate', 'refs.json'));
  capture.record(path.join(ctx.dir, 'script', 'script.json'));
  for (const row of ctx.shots?.shots ?? []) {
    const root = path.join(ctx.dir, 'scenes', 'shots', shotId(row.id));
    capture.record(path.join(root, 'web', 'index.html'));
    for (const ext of SCENE_EXTENSIONS) capture.record(path.join(root, 'remotion', 'index' + ext));
  }
  for (const a of ctx.acts) {
    const dir = path.join(ctx.dir, 'scenes', 'act' + a.act);
    for (const ext of SCENE_EXTENSIONS) capture.record(path.join(dir, 'index' + ext));
    capture.record(path.join(dir, 'web', 'index.html'));
    capture.record(path.join(ctx.dir, 'scenes', 'ae', 'act' + a.act + '.json'));
  }
  if (brandPath) {
    const file = fs.statSync(brandPath, {throwIfNoEntry: false})?.isDirectory() ? path.join(brandPath, 'brand.json') : path.resolve(brandPath);
    const bytes = capture.record(file);
    if (bytes !== null) {
      const brand = JSON.parse(bytes.toString('utf8'));
      const logos = Array.isArray(brand.logos) ? brand.logos : brand.logos && typeof brand.logos === 'object' ? Object.values(brand.logos) : [];
      for (let logo of [...logos, brand.motif]) {
        if (typeof logo === 'string') logo = {path: logo};
        const rel = logo && typeof logo === 'object' && !logo.svg ? logo.path ?? logo.file : null;
        if (typeof rel !== 'string' || !rel.toLowerCase().endsWith('.svg')) continue;
        const source = path.resolve(path.dirname(file), rel);
        if (source.startsWith(path.resolve(path.dirname(file)) + path.sep)) capture.record(source);
      }
    }
  }
  return capture;
}
