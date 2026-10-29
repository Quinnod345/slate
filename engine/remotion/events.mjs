// emit-events: every act's visual beats, from the same module the picture reads, into
// timing/visual_events/act<N>.json, and the kit's motion trace into timing/motion/act<N>.json.
//
// One emitter replaces the films' 22 (Python, esbuild TS and mjs). Its checks are the films' best ones, in one place:
// a known kind and size, a finite time inside the act and inside its shot, unique ids, a note that says what the viewer
// sees, at most one impact per act, and no beat written while a word lookup fell back to an estimate (film C's
// emitter refused that too: a beat on a guessed word time is a sound in the wrong place). An act with no beats must
// say why ({"silent": true, "reason"}), because "no file" and "no beats" look the same to a sound engine and only one
// of them is a decision.
//
// Time: t is always the beat, the film second the motion lands. A sustained motion also carries its span, start (film
// seconds) and dur: a draw and a count must give it and end on t; a whoosh and a swell peak on t, inside theirs; any
// other kind may give one, with its beat inside. spanProblems() holds slate.timing.beats.problems()'s rules, word for
// word, so the engines and every reader (sound, QA) agree on what t means.
//
// Beats come from three places, merged and sorted by time (then id):
//   - the act's own `events` export (events.*, then beats.*, then index.*),
//   - what the act registered with the kit's defineEvent() (the kit's actEvents(act), as its collectAct() reads it),
//   - the taps of placed captures (captures/<name>/beats.json, written by `slate capture beatmap`), each on the act its
//     shot belongs to, marked `source: "capture:<name>"`.
// An act built for the open web renderer (scenes/act<N>/web/index.html) has its beats read from its page by that
// renderer's own emit mode, under these same rules; its captures' taps are merged here afterwards.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {ENGINE_DIR, EngineError, log, missing, readJson, refused, sha256Buffer, sha256FileSync, usage, writeJsonAtomic} from './lib/common.mjs';
import {actOf, realFile} from './lib/film.mjs';
import {ensureEntry, missingIndexNote, sceneIndex, shotScenes} from './entry.mjs';
import {actMarginList, declarations, loadAct, loadRemotionShot, rigLint} from './load_act.mjs';
import {baseScene, canonicalSource, contextCapture, mixedScenesForAct, sourceCapture} from './lib/shot_scenes.mjs';

/** An act built for the open web renderer: scenes/act<N>/web/index.html (and no Remotion module). */
export function webScene(filmDir, act) {
  if (sceneIndex(filmDir, act)) return null;
  const f = path.join(filmDir, 'scenes', `act${act}`, 'web', 'index.html');
  return fs.existsSync(f) ? f : null;
}

/** The open web renderer's module (SLATE_WEB_RENDER_MODULE swaps it, as for cli.mjs's web-render). */
const webModule = () => (process.env.SLATE_WEB_RENDER_MODULE ? path.resolve(process.env.SLATE_WEB_RENDER_MODULE) : path.join(ENGINE_DIR, 'web', 'render.mjs'));

/** The open web renderer's webRender(), or the missing-prerequisite error that says how to get it. */
export async function webRenderer(act) {
  const file = webModule();
  if (!fs.existsSync(file)) {
    throw missing(`Act ${act} is a web scene, but the open web renderer isn't in this engine (${file}).`, 'Update Slate and run `slate engine install` again.');
  }
  const mod = await import(pathToFileURL(file).href);
  if (typeof mod.webRender !== 'function') throw missing(`${file} doesn't export webRender(args).`);
  return mod.webRender;
}

/**
 * A web act's beats come from its page, so the web renderer's own emit mode reads them (engine/web/render.mjs,
 * webRender({emit: true})), with these same rules (it calls validateEvents below). One command still emits every act.
 * Placed captures' taps on the act join its beats here. With `dryRun` nothing is written: the summary carries what
 * would be (`beats`), every problem that would stop the write, and the page's own gates (network, fonts), which is
 * what `slate emit-events --dry-run` and the hand-in check read. `extra` passes render options (on_blocked, gl).
 */
export async function emitWebAct(ctx, act, args, captures, {dryRun = false, extra = {}} = {}) {
  if (captures.strays.length && !dryRun) {
    throw refused(`Capture taps no act holds; nothing was written:\n  ${captures.strays.join('\n  ')}`, 'Place the capture on the film clock again (`slate capture beatmap`).', {problems: captures.strays});
  }
  const webRender = await webRenderer(act);
  const res = await webRender({
    film: ctx.dir,
    act,
    emit: true,
    ...(dryRun ? {dry_run: true} : {}),
    ...(args.brand_path ? {brand_path: args.brand_path} : {}),
    ...(args.gl ? {gl: args.gl} : {}),
    ...extra,
  });
  // the browser that read the page (its pid and profile folder), so a caller can check nothing was left behind
  const out = (res?.acts ?? []).map((a) => ({...a, engine: 'web', ...(res?.browser ? {browser: res.browser} : {})}));
  const summary = out.find((a) => Number(a.act) === Number(act));
  const taps = captures.byAct.get(Number(act)) ?? [];
  if (dryRun) {
    if (!summary) return out;
    const problems = [...(summary.problems ?? []), ...captures.strays.map((s) => `a capture tap no act holds: ${s}`)];
    if (taps.length) {
      if (!Array.isArray(summary.beats)) {
        problems.push(`act ${act} (a web scene) says it's silent, but ${taps.length} capture tap${taps.length > 1 ? 's land' : ' lands'} in it: keep one, its taps or its silence`);
      } else {
        const merged = [...summary.beats, ...taps].sort(byTimeThenId);
        // the page's beat-by-beat problems are numbered within its own list; judge the merged list afresh
        const kept = problems.filter((p) => !/^beat \d+/.test(p));
        for (const p of validateEvents(ctx, act, merged)) if (!kept.includes(p)) kept.push(p);
        problems.splice(0, problems.length, ...kept);
        const kinds = {};
        for (const e of merged) kinds[e.kind] = (kinds[e.kind] ?? 0) + 1;
        Object.assign(summary, {beats: merged, events: merged.length, kinds, span_s: [merged[0].t, merged.at(-1).t], capture_beats: taps.length, captures: {beats: taps.length, files: captures.files}});
      }
    }
    summary.problems = problems;
    return out;
  }
  // The web emitter reads the page; placed captures' taps on this act join its beats here, under the same rules.
  if (taps.length) {
    const target = eventsFile(ctx, act);
    const data = readJson(target);
    if (!Array.isArray(data)) {
      throw refused(`Act ${act} (a web scene) says it's silent, but ${taps.length} capture tap${taps.length > 1 ? 's land' : ' lands'} in it: keep one, its taps or its silence.`);
    }
    const merged = [...data, ...taps].sort(byTimeThenId);
    const problems = validateEvents(ctx, act, merged);
    if (problems.length) {
      throw refused(`Act ${act}'s beats with its capture taps don't pass (${problems.length} problem${problems.length > 1 ? 's' : ''}):\n  ${problems.slice(0, 20).join('\n  ')}`, 'Fix them in the scene or the capture, then emit again.', {problems});
    }
    writeJsonAtomic(target, merged);
    const kinds = {};
    for (const e of merged) kinds[e.kind] = (kinds[e.kind] ?? 0) + 1;
    if (summary) Object.assign(summary, {events: merged.length, kinds, span_s: [merged[0].t, merged.at(-1).t], capture_beats: taps.length, captures: {beats: taps.length, files: captures.files}});
  }
  return out;
}

export const KINDS = ['tap', 'type', 'land', 'snap', 'count', 'draw', 'data', 'page', 'stamp', 'whoosh', 'swell', 'character', 'impact'];
/** Kinds whose beat is the end of their span: a draw ends on t, a count reaches its `to` at t. */
export const ENDS_ON_BEAT = ['draw', 'count'];
/** Kinds whose beat is their peak, somewhere inside their span. */
export const PEAKS_ON_BEAT = ['whoosh', 'swell'];
/** How far a beat may sit from its span's edge without a frame rate: writers round to the millisecond, so two. */
export const NO_CLOCK_TOLERANCE_S = 0.002;
export const SIZES = ['s', 'm', 'l'];
/** Sizes other writers spell out (the capture beat map's default is "small"). */
export const SIZE_ALIASES = {small: 's', medium: 'm', large: 'l'};
export const GENERATED = 'do not edit by hand: slate emit-events';

export const eventsFile = (ctx, act) => path.join(ctx.dir, 'timing', 'visual_events', `act${act}.json`);
export const motionFile = (ctx, act) => path.join(ctx.dir, 'timing', 'motion', `act${act}.json`);

const isSeconds = (v) => typeof v === 'number' && Number.isFinite(v);

/**
 * The span rules beyond the schema, exactly as slate.timing.beats.problems() states them (same checks, same order, same
 * words; tests/engine_core holds the two together): a draw and a count need start and dur and end on t; start and dur
 * go together and make a forward stretch of film time; any other beat with a span sits inside it. `fps` sets the
 * tolerance: half a frame on the film's clock, else two milliseconds. A silent act's object has nothing to check.
 */
export function spanProblems(list, fps = null) {
  if (!Array.isArray(list)) return [];
  const tol = fps ? 0.5 / fps : NO_CLOCK_TOLERANCE_S;
  const out = [];
  list.forEach((e, i) => {
    if (!e || typeof e !== 'object' || Array.isArray(e)) return;
    const where = e.id ? `beat ${i + 1} (${e.id})` : `beat ${i + 1}`;
    const kind = e.kind;
    const hasStart = 'start' in e;
    const hasDur = 'dur' in e;
    if (ENDS_ON_BEAT.includes(kind) && !(hasStart && hasDur)) {
      out.push(`${where}: a ${kind} runs over time, so it needs start and dur (it ends on t)`);
      return;
    }
    if (hasStart !== hasDur) {
      out.push(`${where}: start and dur go together (the span a sustained motion runs over)`);
      return;
    }
    if (!hasStart) return;
    if (!isSeconds(e.start) || !isSeconds(e.dur)) {
      out.push(`${where}: start and dur must be numbers of seconds`);
      return;
    }
    const start = e.start;
    const end = e.start + e.dur;
    if (start < 0 || end <= start) {
      out.push(`${where}: the span ${start.toFixed(3)}–${end.toFixed(3)} s isn't a forward stretch of film time`);
      return;
    }
    if (!isSeconds(e.t)) return; // the beat's own check reports a beat without a time
    const t = e.t;
    if (ENDS_ON_BEAT.includes(kind)) {
      if (Math.abs(end - t) > tol) {
        const ms = Math.round((end - t) * 1000);
        out.push(`${where}: a ${kind} ends on its beat, but start + dur is ${end.toFixed(3)} s and t is ${t.toFixed(3)} s (${ms >= 0 ? '+' : ''}${ms} ms)`);
      }
    } else if (!(start - tol <= t && t <= end + tol)) {
      const what = PEAKS_ON_BEAT.includes(kind) ? 'peaks' : 'lands';
      out.push(`${where}: a ${kind} ${what} inside its span, but t ${t.toFixed(3)} s is outside ${start.toFixed(3)}–${end.toFixed(3)} s`);
    }
  });
  return out;
}

/**
 * Problems with an act's events (empty when they're good). `list` is the array of beats. The act's span allows half a
 * frame either side, as the kit's validateEvents() does, so the two never disagree about a beat on a boundary; a span
 * (start, dur) is held to spanProblems().
 */
export function validateEvents(ctx, act, list, {raw = false} = {}) {
  const problems = [];
  if (!Array.isArray(list)) return ['the events are not a list'];
  const a = actOf(ctx, act);
  const fps = ctx.fps.value;
  const start = a.frameIn / fps;
  const end = a.frameOut / fps;
  const half = 0.5 / fps;
  const shots = new Map((ctx.shots?.shots ?? []).filter((s) => Number(s.act) === a.act).map((s) => [s.id, s]));
  const ids = new Set();
  let impacts = 0;
  list.forEach((e, i) => {
    const where = `beat ${i + 1}${e && e.id ? ` (${e.id})` : ''}`;
    if (!e || typeof e !== 'object' || Array.isArray(e)) {
      problems.push(`${where}: not an object`);
      return;
    }
    if (typeof e.id !== 'string' || !e.id.trim()) problems.push(`${where}: needs an id`);
    else if (ids.has(e.id)) problems.push(`${where}: the id "${e.id}" is used twice`);
    else ids.add(e.id);
    if (!KINDS.includes(e.kind)) problems.push(`${where}: kind ${JSON.stringify(e.kind)} isn't one of ${KINDS.join(', ')}`);
    if (e.kind === 'impact') impacts++;
    if (!SIZES.includes(e.size)) problems.push(`${where}: size ${JSON.stringify(e.size)} isn't s, m or l`);
    if (typeof e.note !== 'string' || !e.note.trim()) problems.push(`${where}: needs a note saying what the viewer sees`);
    if (typeof e.t !== 'number' || !Number.isFinite(e.t)) {
      problems.push(`${where}: t must be a number of film seconds`);
    } else if (e.t < start - half || e.t > end + half) {
      problems.push(`${where}: t ${e.t.toFixed(3)} s is outside act ${a.act} (${start.toFixed(3)}–${end.toFixed(3)} s)`);
    }
    const shot = shots.get(e.shot);
    if (!shot) {
      problems.push(`${where}: shot ${JSON.stringify(e.shot)} isn't in act ${a.act} (${[...shots.keys()].join(', ')})`);
    } else if (typeof e.t === 'number' && Number.isFinite(e.t)) {
      // one frame of slack either side: a beat on a cut can be credited to either shot
      if (e.t < (shot.frame_in - 1) / fps - 1e-6 || e.t > (shot.frame_out + 1) / fps + 1e-6) {
        problems.push(`${where}: says ${e.shot} but lands at ${e.t.toFixed(3)} s, outside ${e.shot} (${(shot.frame_in / fps).toFixed(3)}–${(shot.frame_out / fps).toFixed(3)} s)`);
      }
    }
    // optional fields the sound side reads: a count's from/to, where it sits across the frame (its span: below)
    for (const k of ['from', 'to', 'x']) {
      if (k in e && (typeof e[k] !== 'number' || !Number.isFinite(e[k]))) problems.push(`${where}: ${k} must be a number`);
    }
    if ('frame' in e && !(Number.isInteger(e.frame) && e.frame >= 0)) problems.push(`${where}: frame must be a whole film frame`);
    if ('pan' in e && !(typeof e.pan === 'number' && e.pan >= -1 && e.pan <= 1)) problems.push(`${where}: pan must be a number from -1 (left) to 1 (right)`);
  });
  if (!raw && impacts > 1) problems.push(`${impacts} impacts in act ${a.act}: at most one (it is the act's one big landing)`);
  problems.push(...spanProblems(list, fps));
  return problems;
}

/** Validate a file's content (a list, or {silent: true, reason}). */
export function validateEventsFile(ctx, act, data) {
  if (data && !Array.isArray(data) && typeof data === 'object') {
    if (data.silent !== true) return ['an object here must be {"silent": true, "reason": "…"}'];
    if (typeof data.reason !== 'string' || !data.reason.trim()) return ['a silent act needs a reason'];
    return [];
  }
  return validateEvents(ctx, act, data);
}

// ---------------------------------------------------------------- capture beats

/** The act a capture beat belongs to: its shot's act, else the act whose span holds its time. */
function actForBeat(ctx, e) {
  const shot = (ctx.shots?.shots ?? []).find((s) => s.id === e.shot);
  if (shot) return Number(shot.act);
  if (typeof e.t !== 'number' || !Number.isFinite(e.t)) return null;
  const frame = e.t * ctx.fps.value;
  return ctx.acts.find((a) => frame >= a.frameIn - 0.5 && frame < a.frameOut + 0.5)?.act ?? null;
}

/**
 * Every placed capture's taps: {byAct: Map(act → beats), strays, notes, files}. A capture that isn't on the film clock
 * yet (film_in null) is a note, not a beat: its taps have no film time. A tap no act holds is a stray, never dropped.
 */
export function captureBeats(ctx, {recordSource = null} = {}) {
  const root = realFile(ctx.dir, 'captures');
  const byAct = new Map();
  const strays = [];
  const notes = [];
  const files = [];
  const sourceHashes = {};
  if (!fs.existsSync(root)) return {byAct, strays, notes, files, ...(recordSource ? {source_hashes: sourceHashes} : {})};
  for (const name of fs.readdirSync(root).sort()) {
    const file = path.join(root, name, 'beats.json');
    if (!fs.existsSync(file)) continue;
    const rel = path.relative(ctx.dir, file);
    let data;
    try {
      const bytes = fs.readFileSync(file);
      recordSource?.(file, bytes);
      if (recordSource) sourceHashes[path.resolve(file)] = sha256Buffer(bytes);
      data = JSON.parse(bytes.toString('utf8'));
    } catch (e) {
      throw refused(`${rel} isn't valid JSON: ${e.message}`, `Run \`slate capture beatmap --name ${name}\` again.`);
    }
    files.push(rel);
    if (data && !Array.isArray(data) && data.film_in === null) {
      notes.push(`capture ${name} isn't placed on the film clock yet, so its taps have no film times (give its spec a place or word anchors)`);
      continue;
    }
    const list = Array.isArray(data) ? data : data?.events;
    if (!Array.isArray(list)) {
      notes.push(`${rel} has no events list`);
      continue;
    }
    for (const raw of list) {
      if (!raw || typeof raw !== 'object') continue;
      const e = {...raw, size: SIZE_ALIASES[raw.size] ?? raw.size, source: raw.source ?? `capture:${name}`};
      const act = actForBeat(ctx, e);
      if (act === null) {
        strays.push(`${rel}: ${e.id ?? '(no id)'} at ${typeof e.t === 'number' ? `${e.t.toFixed(3)} s` : String(e.t)} on ${JSON.stringify(e.shot)} is in no act`);
        continue;
      }
      if (!byAct.has(act)) byAct.set(act, []);
      byAct.get(act).push(e);
    }
  }
  return {byAct, strays, notes, files, ...(recordSource ? {source_hashes: sourceHashes} : {})};
}

// ---------------------------------------------------------------- computing an act's beats

/** The Web owning page and every Remotion replacement are read once, before any active evidence is written. */
export async function readReverseAct(ctx, act, args = {}) {
  const replacements = mixedScenesForAct(ctx, act);
  const base = baseScene(ctx, act);
  if (base?.engine !== 'web' || !replacements.length || replacements.some((sc) => sc.engine !== 'remotion')) {
    throw refused('There is no Web owning act with Remotion replacements to compose.');
  }
  const capture = contextCapture(ctx, {brandPath: args.brand_path ?? null});
  mixedScenesForAct(ctx, act, {capture});
  const entry = await ensureEntry(ctx, {brandPath: args.brand_path ?? null, recordSource: capture.record});
  capture.record(path.join(ctx.dir, '.slate', 'film-data.ts'));
  // The browser hashes its virtual module at the film root; the generated .slate module has different imports.
  // Recreate that exact virtual context with the genuine producer helper and the same observed brand bytes.
  const {filmDataFor} = await import('../web/render.mjs');
  let brandJson = null;
  if (args.brand_path) {
    const requested = path.resolve(args.brand_path);
    const file = fs.statSync(requested, {throwIfNoEntry: false})?.isDirectory() ? path.join(requested, 'brand.json') : requested;
    const bytes = capture.record(file);
    if (bytes === null) throw refused('The Web base brand context disappeared.');
    brandJson = JSON.parse(bytes.toString('utf8'));
  }
  const virtual = filmDataFor(ctx, brandJson, entry.brand ?? null, {recordSource: capture.record});
  const expectedFilmDataHash = sha256Buffer(Buffer.from(virtual.contents));
  const webRender = await webRenderer(act);
  const raw = await webRender({film: ctx.dir, act: Number(act), scene: base.scene, emit: true, dry_run: true,
    trace_rows: true, trace: false, ...(args.brand_path ? {brand_path: args.brand_path} : {}),
    ...(args.gl ? {gl: args.gl} : {}), ...(args.on_blocked ? {on_blocked: args.on_blocked} : {}),
    ...(args.load_timeout_ms ? {load_timeout_ms: args.load_timeout_ms} : {})});
  if (!Array.isArray(raw?.acts) || raw.acts.length !== 1 || raw.acts[0]?.act !== Number(act)) {
    throw refused('The raw Web reader did not return exactly the owning act.');
  }
  const page = {...raw.acts[0], ...(raw.browser ? {browser: raw.browser} : {})};
  const own = actOf(ctx, act);
  const trace = page.trace_read;
  if (!trace || trace.frame_base !== 'film' || trace.act !== own.act ||
      !Array.isArray(trace.act_frames) || trace.act_frames.length !== 2 ||
      trace.act_frames[0] !== own.frameIn || trace.act_frames[1] !== own.frameOut ||
      trace.fps_num !== ctx.fps.num || trace.fps_den !== ctx.fps.den || !Array.isArray(trace.rows) ||
      typeof trace.film_data_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(trace.film_data_sha256)) {
    throw refused('The Web base has no raw motion read on its actual film clock: ' + (trace?.reason ?? 'missing trace'));
  }
  if (trace.film_data_sha256 !== expectedFilmDataHash) {
    throw refused('The Web base virtual film data does not match the observed owning context.');
  }
  const decl = page.raw_declarations;
  if (!decl || typeof decl !== 'object' || Array.isArray(decl) ||
      !Object.hasOwn(decl, 'events') || !Object.hasOwn(decl, 'silent') ||
      !Array.isArray(decl.problems) || !Array.isArray(decl.misses)) {
    throw refused('The Web reader did not expose typed raw declarations.');
  }
  const merge = (hashes, required) => {
    if (!hashes || typeof hashes !== 'object' || Array.isArray(hashes) || !Object.keys(hashes).length) {
      throw refused('Mixed evidence has no captured source map.');
    }
    const normalized = new Map();
    for (const [file, digest] of Object.entries(hashes)) {
      if (!file || digest !== null && (typeof digest !== 'string' || !/^[0-9a-f]{64}$/.test(digest))) {
        throw refused('A captured mixed source path or hash is malformed.');
      }
      // The genuine Web producer uses film-relative keys for local files and absolute keys for dependencies.
      const absolute = path.resolve(ctx.dir, file);
      const identity = canonicalSource(absolute);
      if (normalized.has(identity) && normalized.get(identity) !== digest) throw refused('Conflicting captured source aliases: ' + file);
      normalized.set(identity, digest);
      const bytes = capture.record(absolute);
      const observed = bytes === null ? null : sha256Buffer(bytes);
      if (observed !== digest) throw refused('Captured source changed or disappeared: ' + file);
    }
    if (typeof normalized.get(canonicalSource(required)) !== 'string') throw refused('Captured sources omit actual scene entry ' + required);
  };
  const pageKeys = Object.keys(page.source_hashes ?? {}).sort();
  if (!Array.isArray(page.sources) || page.sources.length !== pageKeys.length ||
      [...page.sources].sort().some((file, i) => file !== pageKeys[i])) throw refused('Web base source list and captured map disagree.');
  merge(page.source_hashes, base.index);
  const shots = [];
  for (const sc of replacements) {
    const loaded = await loadRemotionShot(ctx, sc, {contextSources: capture.hashes});
    merge(loaded.source_hashes, sc.index);
    shots.push({sc, loaded});
  }
  capture.verify();
  return {act: own.act, base_engine: 'web', base, page, trace, declarations: decl, shots,
    capture, inputs: Object.keys(capture.hashes).sort()};
}

async function rawRemotionEvents(ctx, act, loaded) {
  const decl = declarations(loaded);
  let events = decl.events;
  if (typeof events === 'function') events = await events();
  if (events != null && !Array.isArray(events)) throw refused('A replacement events export is not a list.');
  const fromKit = loaded.hooks?.kitEvents ? loaded.hooks.kitEvents.fn() : [];
  if (!Array.isArray(fromKit)) throw refused('A replacement kit event hook did not return a list.');
  const problems = (loaded.hooks?.eventProblems ? loaded.hooks.eventProblems.fn() ?? [] : []).map((p) => 'the kit: ' + (typeof p === 'string' ? p : JSON.stringify(p)));
  const missesRaw = loaded.hooks?.wordMisses ? loaded.hooks.wordMisses.fn() : [];
  if (!Array.isArray(missesRaw)) throw refused('A replacement word-miss hook did not return a list.');
  const misses = missesRaw.map((m) => typeof m === 'string' ? m : JSON.stringify(m));
  if (misses.length) problems.push('Word lookups fell back to estimates: ' + misses.join(', '));
  for (const margin of await actMarginList(loaded)) {
    if (!(Number.isFinite(margin.slack) && margin.slack >= 0)) problems.push('A timing margin is broken: ' + margin.name);
  }
  problems.push(...rigLint(loaded).findings.map((finding) => 'the rig lint: ' + finding));
  return {events: [...(events ?? []), ...fromKit], silent: decl.silent, problems, misses};
}

function silentReason(value, label) {
  if (value === undefined || value === null || value === false) return null;
  const reason = typeof value === 'string' ? value : value?.reason;
  if (typeof reason !== 'string' || !reason.trim()) throw refused(label + ' says it is silent without a reason.');
  return reason.trim();
}

export async function computeReverseEvents(ctx, act, {captures = null, args = {}, evidence = null} = {}) {
  const read = evidence ?? await readReverseAct(ctx, act, args);
  const rawBase = read.declarations.events ?? [];
  if (!Array.isArray(rawBase)) throw refused('The raw Web base events are not a list.');
  const problems = [...read.declarations.problems.map(String), ...read.declarations.misses.map((m) => 'A Web word lookup missed: ' + String(m))];
  problems.push(...validateEvents(ctx, act, rawBase, {raw: true}).map((p) => 'the raw Web base: ' + p));
  const replaced = new Set(read.shots.map((item) => item.sc.shot));
  const all = rawBase.filter((event) => !replaced.has(event?.shot));
  const misses = [...read.declarations.misses];
  for (const item of read.shots) {
    const own = await rawRemotionEvents(ctx, act, item.loaded);
    problems.push(...own.problems.map((p) => 'shot ' + item.sc.shot + ': ' + p));
    misses.push(...own.misses);
    problems.push(...validateEvents(ctx, act, own.events, {raw: true}).map((p) => 'shot ' + item.sc.shot + ': ' + p));
    for (const event of own.events) {
      if (event?.shot !== item.sc.shot) problems.push('Replacement ' + item.sc.shot + ' emitted a beat owned by another shot.');
      else all.push(event);
    }
    if (silentReason(own.silent, 'Shot ' + item.sc.shot) && own.events.length) {
      problems.push('Shot ' + item.sc.shot + ' declares silence and beats together.');
    }
  }
  // Captures are independent visible inputs. Read their bytes once and retain their source evidence.
  const caps = captures ?? captureBeats(ctx, {recordSource: read.capture.record});
  const fromCaptures = caps.byAct.get(Number(act)) ?? [];
  if (captures && !captures.source_hashes) throw refused('A pre-read capture set has no observed source hashes for mixed composition.');
  if (captures?.source_hashes) {
    for (const [file, digest] of Object.entries(captures.source_hashes)) {
      const bytes = read.capture.record(path.resolve(ctx.dir, file));
      if ((bytes === null ? null : sha256Buffer(bytes)) !== digest) throw refused('Capture source changed after its read.');
    }
  }
  all.push(...fromCaptures);
  problems.push(...caps.strays.map((stray) => 'A capture tap has no owning act: ' + stray));
  const reason = silentReason(read.declarations.silent, 'Act ' + act);
  if (reason && all.length) problems.push('Act ' + act + ' declares whole-act silence but has visible beats or capture taps.');
  const sorted = all.map((event) => ({...event})).sort(byTimeThenId);
  problems.push(...validateEvents(ctx, act, sorted));
  if (!reason && !sorted.length) problems.push('Act ' + act + ' has no visible beats and gives no whole-act silence reason.');
  read.capture.verify();
  const loaded = {mixed: read, inputs: Object.keys(read.capture.hashes).sort()};
  return {...(reason ? {silent: true, reason} : {events: sorted}), problems, notes: caps.notes, misses, loaded,
    webShots: [], captures: {beats: fromCaptures.length, files: caps.files}};
}

function checkedRawMotion(ctx, act, rows, label, shot = null) {
  if (!Array.isArray(rows)) throw refused(label + ' has no raw motion list.');
  const a = actOf(ctx, act);
  const owners = new Set((ctx.shots?.shots ?? []).filter((item) => Number(item.act) === a.act).map((item) => item.id));
  const ids = new Set();
  for (const row of rows) {
    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        !['id', 'prop', 'frame_in', 'frame_out', 'samples'].every((key) => Object.hasOwn(row, key)) ||
        typeof row.id !== 'string' || !row.id || typeof row.prop !== 'string' || !Array.isArray(row.samples) ||
        row.shot !== undefined && !owners.has(row.shot) || ids.has(row.id) ||
        !Number.isFinite(row.frame_in) || !Number.isFinite(row.frame_out) || row.frame_out < row.frame_in ||
        row.frame_in < a.frameIn || row.frame_out > a.frameOut) {
      throw refused(label + ' contains a malformed, duplicate or out-of-act raw motion registration.');
    }
    ids.add(row.id);
    if (shot && (row.shot !== shot.id || row.frame_in < shot.frame_in || row.frame_out > shot.frame_out)) {
      throw refused(label + ' contains motion outside its explicit shot owner.');
    }
    let dimensions = null;
    for (const sample of row.samples) {
      if (!Array.isArray(sample) || sample.length !== 2 || typeof sample[0] !== 'number' ||
          !Number.isFinite(sample[0]) || !motionValue(sample[1])) throw refused(label + ' contains malformed or nonfinite motion samples.');
      const size = Array.isArray(sample[1]) ? sample[1].length : 1;
      if (dimensions !== null && dimensions !== size) throw refused(label + ' mixes motion sample dimensions.');
      dimensions = size;
    }
    // These two optional labels follow motion._entry_problem's existing accepted aliases; no motion threshold is changed.
    const key = (value) => String(value).toLowerCase().replace(/[^a-z0-9]/g, '');
    if (row.class != null && !['rig', 'character', 'mark', 'ui', 'element', 'carry', 'page', 'camera', 'whip'].includes(key(row.class)) ||
        row.exempt != null && !['readingsweep', 'steady'].includes(key(row.exempt))) throw refused(label + ' contains an unknown motion class or exemption.');
    if (row.loop_period != null && !(Number.isFinite(row.loop_period) && row.loop_period > 0) ||
        row.entrance !== undefined && typeof row.entrance !== 'boolean' ||
        row.rig != null && typeof row.rig !== 'string') throw refused(label + ' contains malformed motion metadata.');
  }
  return rows;
}

export function writeReverseMotion(ctx, act, read, {dryRun = false} = {}) {
  const base = checkedRawMotion(ctx, act, read.trace.rows, 'Raw Web base');
  const replaced = read.shots.map((item) => item.sc.shot_record);
  const retained = [];
  for (const row of base) {
    const owned = replaced.find((shot) => shot.id === row.shot);
    const overlaps = replaced.some((shot) => row.frame_in < shot.frame_out && row.frame_out > shot.frame_in ||
      row.frame_in === row.frame_out && row.frame_in >= shot.frame_in && row.frame_in < shot.frame_out);
    if (owned) {
      if (row.frame_in < owned.frame_in || row.frame_out > owned.frame_out) throw refused('Base motion straddles replacement ' + owned.id + '.');
      continue;
    }
    if (overlaps) throw refused('Base motion crosses a replaced shot without explicit bounded ownership; curves are not clipped or resampled.');
    retained.push(row);
  }
  const rows = [...retained];
  const ids = new Set(rows.map((row) => row.id));
  const proofs = [];
  for (const item of read.shots) {
    const raw = computeMotion(ctx, act, item.loaded, {raw: true});
    if (!raw.rows) throw refused('Replacement ' + item.sc.shot + ' has unavailable motion coverage: ' + raw.reason);
    checkedRawMotion(ctx, act, raw.rows, 'Remotion shot ' + item.sc.shot, item.sc.shot_record);
    for (const row of raw.rows) {
      const id = 'remotion:' + item.sc.shot + ':' + row.id;
      if (ids.has(id)) throw refused('Composed motion id is repeated: ' + id);
      ids.add(id);
      rows.push({...row, id, source_id: row.id, engine: 'remotion', scene: item.sc.scene});
    }
    proofs.push({shot: item.sc.shot, frames: [item.sc.shot_record.frame_in, item.sc.shot_record.frame_out],
      scene: item.sc.scene, index: item.sc.index, entries: raw.rows.length, source_hashes: {...item.loaded.source_hashes}});
  }
  read.capture.verify();
  const hashes = {...read.capture.hashes};
  const observed = Object.fromEntries(Object.entries(hashes).filter(([, digest]) => digest !== null));
  const file = motionFile(ctx, act);
  const baseFile = path.join(ctx.dir, 'timing', 'motion', 'sources', 'act' + act + '.base.json');
  const compositionFile = path.join(ctx.dir, 'timing', 'motion', 'sources', 'act' + act + '.composition.json');
  const meta = {act: Number(act), frame_base: 'film', base_engine: 'web', remotion_shots: proofs.map((item) => item.shot),
    sources: Object.keys(hashes).sort(), source_hashes: observed};
  if (dryRun) return {written: false, would_write: path.relative(ctx.dir, file), entries: rows.length, reason: 'dry run', ...meta};
  writeJsonAtomic(baseFile, base);
  writeJsonAtomic(compositionFile, {act: Number(act), frame_base: 'film', base_engine: 'web',
    act_frames: [actOf(ctx, act).frameIn, actOf(ctx, act).frameOut], fps_num: ctx.fps.num, fps_den: ctx.fps.den,
    source_hashes: hashes, remotion_shots: proofs, base_entries: base.length, visible_base_entries: retained.length,
    limitation: 'Curves with ambiguous ownership across a splice are refused, not clipped or resampled.'});
  writeJsonAtomic(file, rows);
  return {written: true, file: path.relative(ctx.dir, file), entries: rows.length, ...meta,
    base_file: path.relative(ctx.dir, baseFile), composition_file: path.relative(ctx.dir, compositionFile)};
}

const byTimeThenId = (x, y) => x.t - y.t || String(x.id).localeCompare(String(y.id));

/**
 * What the act says its events are, merged with the kit's registrations and the captures' taps, and everything that
 * stops them being written: {silent, reason} or {events}, plus {problems, notes, misses, loaded, captures}.
 * `problems` block emit-events and fail the check; a structural error (the module doesn't build, events() throws)
 * throws instead.
 */
/** The act's web shots (scenes/shots/<SHOT>/web), each with its shot record from timing/shots.json. */
export function actShotScenes(ctx, act) {
  const a = actOf(ctx, act);
  const shots = new Map((ctx.shots?.shots ?? []).filter((s) => Number(s.act) === a.act).map((s) => [String(s.id), s]));
  return shotScenes(ctx.dir)
    .filter((sc) => shots.has(sc.shot))
    .map((sc) => ({...sc, shotRecord: shots.get(sc.shot)}));
}

/** Whether a film time lies in a shot (half a frame of slack either side, the act's own rule). */
function shotHolds(ctx, shot, t) {
  if (typeof t !== 'number' || !Number.isFinite(t)) return false;
  const fps = ctx.fps.value;
  return t >= (shot.frame_in - 0.5) / fps && t <= (shot.frame_out + 0.5) / fps;
}

/** A web shot's page, read in a dry run (nothing written): {beats, problems, gates, misses, error}. */
export async function webShotPage(ctx, act, sc, args = {}, extra = {}) {
  const none = {byAct: new Map(), strays: [], notes: [], files: []};
  try {
    const out = await emitWebAct(ctx, act, args, none, {dryRun: true, extra: {scene: sc.dir, trace: false, ...extra}});
    const page = out.find((x) => Number(x.act) === Number(act)) ?? {};
    return {beats: Array.isArray(page.beats) ? page.beats : [], problems: (page.problems ?? []).filter((p) => !/^beat \d+/.test(p)), gates: page.gates ?? [], misses: page.misses ?? [], declared_misses: page.declared_misses, scene_problems: page.scene_problems ?? [], error: null, page};
  } catch (e) {
    if (!(e instanceof EngineError)) throw e;
    return {beats: [], problems: [], gates: [], misses: [], error: e.message.split('\n')[0], failure: e};
  }
}

export async function computeEvents(ctx, act, loaded = null, {captures = null, args = {}} = {}) {
  if (baseScene(ctx, act)?.engine === 'web' && mixedScenesForAct(ctx, act).length) {
    return computeReverseEvents(ctx, act, {captures, args, evidence: loaded?.mixed ?? null});
  }
  const mod = loaded ?? (await loadAct(ctx, act));
  if (mod.missing) {
    throw usage(`Act ${act} isn't built yet (${missingIndexNote(ctx.dir, act)}).`, 'A placeholder act has no beats to emit; build the act first.');
  }
  const decl = declarations(mod);
  let events = decl.events;
  if (typeof events === 'function') {
    try {
      events = await events();
    } catch (e) {
      throw new EngineError(`Act ${act}'s events() throws: ${e?.message ?? e}`, {kind: 'refused'});
    }
  }
  if (events !== undefined && events !== null && !Array.isArray(events)) {
    throw refused(`Act ${act}'s events export isn't a list (or a function returning one).`);
  }
  const problems = [];
  const notes = [];
  // Beats the act registered with the kit's defineEvent(), filtered to its shots by the kit's actEvents(act).
  const fromKit = mod.hooks?.kitEvents ? mod.hooks.kitEvents.fn() : [];
  // What defineEvent() had to record (an event with no time, one id defined twice with different values).
  const kitEventProblems = mod.hooks?.eventProblems ? mod.hooks.eventProblems.fn() : [];
  for (const p of kitEventProblems) problems.push(`the kit: ${typeof p === 'string' ? p : JSON.stringify(p)}`);
  // Word lookups that fell back to an estimate: a beat on a guessed time lands its sound in the wrong place.
  const missesRaw = mod.hooks?.wordMisses ? mod.hooks.wordMisses.fn() : [];
  const misses = (Array.isArray(missesRaw) ? missesRaw : []).map((m) => (typeof m === 'string' ? m : JSON.stringify(m)));
  if (misses.length) {
    problems.push(`${misses.length} word lookup${misses.length > 1 ? 's' : ''} fell back to an estimate (${misses.slice(0, 5).join(', ')}${misses.length > 5 ? '…' : ''}): the beats would land on guessed times`);
  }
  // After a re-voice a beat the picture depends on can overrun another: nothing is written while a timing margin the act
  // records is negative or the rig lint finds anything (L-timing-D039; `slate check` lists them all).
  const broken = (await actMarginList(mod)).filter((m) => !(Number.isFinite(m.slack) && m.slack >= 0));
  for (const m of broken) problems.push(`a timing margin is broken: ${m.name} (${Number.isFinite(m.slack) ? `${m.slack.toFixed(3)} s` : 'not a number'})`);
  for (const f of rigLint(mod).findings) problems.push(`the rig lint: ${f}`);
  const caps = captures ?? captureBeats(ctx);
  const fromCaptures = caps.byAct.get(Number(act)) ?? [];
  for (const s of caps.strays) problems.push(`a capture tap no act holds: ${s}`);
  notes.push(...caps.notes);
  // the act's shots drawn in the open web renderer: their pages' beats, each inside its own shot
  const fromShots = [];
  const webShots = [];
  for (const sc of motionShotScenes(ctx, act)) {
    const page = await webShotPage(ctx, act, sc, args, {trace_rows: true});
    webShots.push({sc, ...page});
    if (page.error) problems.push(`shot ${sc.shot} (a web shot): its page didn't open: ${page.error}`);
    for (const p of page.problems) problems.push(`shot ${sc.shot} (a web shot): ${p}`);
    for (const e of page.beats) {
      if (!shotHolds(ctx, sc.shotRecord, e.t)) problems.push(`shot ${sc.shot} (a web shot): its beat ${e.id ?? '(no id)'} at ${typeof e.t === 'number' ? `${e.t.toFixed(3)} s` : String(e.t)} isn't inside ${sc.shot}`);
      else fromShots.push({...e, shot: e.shot ?? sc.shot});
    }
  }
  const rawBase = [...(Array.isArray(events) ? events : []), ...(Array.isArray(fromKit) ? fromKit : [])];
  let visibleBase = rawBase;
  if (webShots.length) {
    // Validate source evidence before excluding replaced content. The act's impact limit applies after composition.
    problems.push(...validateEvents(ctx, act, rawBase, {raw: true}).map((p) => `the base act: ${p}`));
    const replaced = new Set(webShots.map((item) => item.sc.shot));
    visibleBase = rawBase.filter((event) => !replaced.has(event?.shot));
  }
  const all = [...visibleBase, ...fromCaptures, ...fromShots];
  const extra = {problems, notes, misses, loaded: mod, webShots, captures: {beats: fromCaptures.length, files: caps.files}};
  const silent = decl.silent;
  if (silent !== undefined && silent !== null && silent !== false) {
    const reason = typeof silent === 'string' ? silent : silent?.reason;
    if (typeof reason !== 'string' || !reason.trim()) throw refused(`Act ${act} says it's silent but gives no reason.`, "export const silent = 'why this act has no beats';");
    if (all.length) problems.push(`act ${act} says it's silent but also has ${all.length} beat${all.length > 1 ? 's' : ''}${fromCaptures.length ? ` (${fromCaptures.length} from captures)` : ''}: keep one, its beats or its silence`);
    return {silent: true, reason: reason.trim(), ...extra};
  }
  if (!all.length) {
    problems.push(`act ${act} has no beats and doesn't say it's silent: export its beats from scenes/act${act} ("export const events = [...]"), or say why it has none ("export const silent = '...'")`);
    return {events: [], ...extra};
  }
  const sorted = all.map((e) => ({...e})).sort(byTimeThenId);
  problems.push(...validateEvents(ctx, act, sorted));
  return {events: sorted, ...extra};
}

/** The motion trace for an act, from the kit's registry, or why there isn't one. */
export function computeMotion(ctx, act, loaded, {raw = false} = {}) {
  if (!loaded || loaded.missing) return {written: false, reason: `act ${act} isn't built yet`};
  if (!loaded.usesKit) return {written: false, reason: "the act doesn't use the kit, so the kit's motion registry has nothing for it"};
  if (!loaded.hooks.motionTrace) return {written: false, reason: 'the kit has no motion registry (traceMotions())'};
  let rows = loaded.hooks.motionTrace.fn();
  if (!Array.isArray(rows)) return {written: false, reason: `the kit's ${loaded.hooks.motionTrace.name}() didn't return a list`};
  const a = actOf(ctx, act);
  if (!raw) rows = rows.filter((r) => r && (r.shot === undefined || (ctx.shots?.shots ?? []).some((s) => s.id === r.shot && Number(s.act) === a.act)));
  return {rows};
}

/** Keep the previous generated trace as failure evidence before making current QA coverage unavailable. */
export function invalidateMotion(ctx, act, reason) {
  const file = motionFile(ctx, act);
  if (!fs.existsSync(file)) return null;
  const previous = fs.readFileSync(file);
  const root = path.join(ctx.dir, 'timing', 'motion', 'failures');
  fs.mkdirSync(root, {recursive: true});
  const folder = fs.mkdtempSync(path.join(root, `act${act}-`));
  fs.writeFileSync(path.join(folder, 'previous.json'), previous, {flag: 'wx'});
  writeJsonAtomic(path.join(folder, 'failure.json'), {
    act, file: path.relative(ctx.dir, file), reason: String(reason),
    previous_sha256: sha256Buffer(previous), previous_bytes: previous.length,
  });
  // A failed combined read must not leave an older base-only array looking current to QA.
  fs.unlinkSync(file);
  return path.relative(ctx.dir, folder);
}

/**
 * Match render's routing: timing's explicit engine first, then the script's older declaration.
 * A declared web shot with no readable page cannot quietly disappear from motion coverage.
 */
function motionShotScenes(ctx, act) {
  const scenes = actShotScenes(ctx, act);
  const scriptFile = path.join(ctx.dir, 'script', 'script.json');
  const fallback = new Map();
  const routingHashes = {};
  if (fs.existsSync(scriptFile)) {
    const bytes = fs.readFileSync(scriptFile);
    let script;
    try { script = JSON.parse(bytes.toString('utf8')); }
    catch { throw refused('script/script.json cannot be read for per-shot motion ownership.'); }
    for (const a of script?.acts ?? []) {
      const shots = a?.shots?.length ? a.shots : a?.lines ?? [];
      for (const shot of shots) {
        if (shot?.id && shot.engine) fallback.set(String(shot.id), String(shot.engine));
      }
    }
    routingHashes['script/script.json'] = sha256Buffer(bytes);
  }
  const wanted = (ctx.shots?.shots ?? []).filter((r) => Number(r.act) === Number(act));
  for (const shot of wanted) {
    const engine = shot.engine || fallback.get(String(shot.id));
    const scene = scenes.find((sc) => sc.shot === String(shot.id));
    if (engine === 'web' && !scene) {
      throw refused(`Web shot ${shot.id} has no page to read into the act's motion trace.`,
        `Restore scenes/shots/${shot.id}/web/index.html, then emit or trace the act again.`, {motion_incomplete: true});
    }
    if (scene && engine !== 'web') {
      throw refused(`Motion ownership for shot ${shot.id} is ambiguous: its page exists, but the shot doesn't declare engine: web.`,
        'Keep the script, resolved shot engine and scene directory consistent, then emit again.', {motion_incomplete: true});
    }
  }
  return scenes.map((sc) => ({...sc, routing_source_hashes: routingHashes}));
}

const motionValue = (value) => typeof value === 'number' && Number.isFinite(value) ||
  Array.isArray(value) && value.length > 0 && value.every((v) => typeof v === 'number' && Number.isFinite(v));
const sameValueShape = (left, right) => Array.isArray(left) === Array.isArray(right) &&
  (!Array.isArray(left) || left.length === right.length);

/** Validate the producer's raw rows before selecting visible motion. No coercion or act-offset guess is safe here. */
function webMotionRows(ctx, act, item) {
  const {sc, page} = item;
  const who = `web shot ${sc.shot}`;
  const fail = (why) => { throw refused(`${who}: ${why}`, 'Fix the page or its trace registration, then emit or trace the act again.'); };
  if (item.error) fail(`the page read failed: ${item.error}`);
  if ((item.problems ?? []).length || (item.scene_problems ?? []).length || (item.misses ?? []).length) {
    fail(`the producer reported problems: ${[...(item.problems ?? []), ...(item.scene_problems ?? []), ...(item.misses ?? [])].join('; ')}`);
  }
  const trace = page?.trace_read;
  const a = actOf(ctx, act);
  if (!trace || trace.frame_base !== 'film' || trace.act !== Number(act) ||
      !Array.isArray(trace.act_frames) || trace.act_frames.length !== 2 ||
      trace.act_frames[0] !== a.frameIn || trace.act_frames[1] !== a.frameOut ||
      trace.fps_num !== ctx.fps.num || trace.fps_den !== ctx.fps.den) {
    fail('there is no trace read bound to this act and its film clock');
  }
  if (!Array.isArray(trace.rows)) fail(trace.reason || 'the runtime did not return a motion list');
  const ids = new Set();
  for (const row of trace.rows) {
    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        !['id', 'prop', 'ease'].every((k) => typeof row[k] === 'string' && row[k].trim()) ||
        row.shot !== sc.shot) fail('a raw motion row is malformed or belongs to another shot');
    if (ids.has(row.id)) fail(`motion id ${row.id} is repeated`);
    ids.add(row.id);
    if (!Number.isInteger(row.frame_in) || !Number.isInteger(row.frame_out) ||
        row.frame_out < row.frame_in || row.frame_in < sc.shotRecord.frame_in ||
        row.frame_out > sc.shotRecord.frame_out) fail(`motion ${row.id} has frames outside its owned shot`);
    if (!motionValue(row.from) || !motionValue(row.to) || !sameValueShape(row.from, row.to)) {
      fail(`motion ${row.id} has malformed or nonfinite endpoints`);
    }
    if (!Array.isArray(row.samples) || !row.samples.length) fail(`motion ${row.id} has no curve samples`);
    let previous = -Infinity;
    for (const sample of row.samples) {
      if (!Array.isArray(sample) || sample.length !== 2 || !Number.isInteger(sample[0]) ||
          sample[0] < row.frame_in || sample[0] > row.frame_out || sample[0] <= previous ||
          !motionValue(sample[1]) || !sameValueShape(row.from, sample[1])) fail(`motion ${row.id} has malformed curve samples`);
      previous = sample[0];
    }
    if (row.samples[0][0] !== row.frame_in || row.samples.at(-1)[0] !== row.frame_out) {
      fail(`motion ${row.id} does not sample both declared endpoints`);
    }
  }
  const hashes = page.source_hashes;
  const sources = page.sources;
  if (!hashes || typeof hashes !== 'object' || Array.isArray(hashes) || !Object.keys(hashes).length ||
      !Array.isArray(sources) || sources.length !== Object.keys(hashes).length ||
      [...sources].sort().some((name, i) => name !== Object.keys(hashes).sort()[i])) {
    fail('the read has no complete captured-source hash map');
  }
  if (!fs.statSync(sc.index, {throwIfNoEntry: false})?.isFile()) fail('the HTML page is missing after the read');
  const expectedIndex = fs.realpathSync(sc.index);
  let sawIndex = false;
  const captured = {...hashes};
  for (const [name, digest] of Object.entries(captured)) {
    const file = path.resolve(ctx.dir, name);
    if (typeof name !== 'string' || !name || typeof digest !== 'string' || !/^[0-9a-f]{64}$/.test(digest) ||
        !fs.statSync(file, {throwIfNoEntry: false})?.isFile() || sha256FileSync(file) !== digest) {
      fail(`captured source ${name} is missing, changed or malformed`);
    }
    if (fs.realpathSync(file) === expectedIndex) sawIndex = true;
  }
  if (!sawIndex) fail('the captured sources do not include the actual HTML page');
  for (const [name, digest] of Object.entries(sc.routing_source_hashes ?? {})) {
    if (captured[name] && captured[name] !== digest) fail(`routing source ${name} changed between reads`);
    if (sha256FileSync(path.resolve(ctx.dir, name)) !== digest) fail(`routing source ${name} changed between reads`);
    captured[name] = digest;
  }
  return {rows: trace.rows, hashes: captured};
}

/** Compose only the motion drawn after the web shots are spliced; retain complete base authoring evidence separately. */
function composedMotion(ctx, act, loaded, webShots) {
  const base = computeMotion(ctx, act, loaded, {raw: webShots.length > 0});
  if (!webShots.length) return base;
  if (!base.rows) throw refused(`Act ${act}'s base motion coverage is unavailable: ${base.reason}.`,
    'Register the base act motion before composing its web shots.');
  const replaced = webShots.map((item) => item.sc.shotRecord);
  const retained = [];
  const baseIds = new Set();
  for (const row of base.rows) {
    const fail = (why) => { throw refused(`Base motion ownership: ${why}.`); };
    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        !['id', 'prop', 'frame_in', 'frame_out', 'samples'].every((key) => Object.hasOwn(row, key)) ||
        typeof row.id !== 'string' || !row.id || typeof row.prop !== 'string' || !Array.isArray(row.samples)) {
      fail('a raw registration is missing the required trace shape');
    }
    if (baseIds.has(row.id)) fail(`id ${row.id} is repeated in the raw base registry`);
    baseIds.add(row.id);
    let dimensions = null;
    for (const sample of row.samples) {
      if (!Array.isArray(sample) || sample.length !== 2 || typeof sample[0] !== 'number' ||
          !Number.isFinite(sample[0]) || !motionValue(sample[1])) fail(`row ${row.id} has malformed or nonfinite samples`);
      const size = Array.isArray(sample[1]) ? sample[1].length : 1;
      if (dimensions !== null && size !== dimensions) fail(`row ${row.id} mixes sample dimensions`);
      dimensions = size;
    }
    if (row.loop_period != null && !(typeof row.loop_period === 'number' && Number.isFinite(row.loop_period) && row.loop_period > 0) ||
        row.entrance !== undefined && typeof row.entrance !== 'boolean' ||
        row.rig != null && typeof row.rig !== 'string') fail(`row ${row.id} has malformed trace metadata`);

    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        typeof row.id !== 'string' || !row.id || row.shot !== undefined &&
        !(ctx.shots?.shots ?? []).some((shot) => shot.id === row.shot && Number(shot.act) === Number(act))) {
      throw refused('Base motion ownership contains a malformed or out-of-act raw registration.');
    }
    const owns = replaced.find((s) => row.shot === s.id);
    if (typeof row.frame_in !== 'number' || !Number.isFinite(row.frame_in) ||
        typeof row.frame_out !== 'number' || !Number.isFinite(row.frame_out) || row.frame_out < row.frame_in ||
        row.frame_in < actOf(ctx, act).frameIn || row.frame_out > actOf(ctx, act).frameOut) {
      throw refused(`Motion ownership for base row ${row.id ?? '(no id)'} cannot be resolved from its frames.`);
    }
    const overlaps = replaced.some((s) => row.frame_in < s.frame_out && row.frame_out > s.frame_in ||
      row.frame_in === row.frame_out && row.frame_in >= s.frame_in && row.frame_in < s.frame_out);
    if (owns) {
      if (row.frame_in < owns.frame_in || row.frame_out > owns.frame_out) {
        throw refused(`Motion ownership for base row ${row.id} straddles replaced shot ${owns.id}.`,
          'Declare separate shot-owned registrations without changing their curve semantics.');
      }
      continue;
    }
    if (overlaps) throw refused(`Motion ownership for base row ${row.id} crosses a replaced web shot.`,
      'Declare separate shot-owned registrations; this composer does not clip or relabel curves.');
    retained.push(row);
  }
  const sources = {};
  const proofs = [];
  const rows = [...retained];
  const ids = new Set(retained.map((row) => row.id));
  const shots = new Set();
  for (const item of webShots) {
    if (shots.has(item.sc.shot)) throw refused(`Web shot ${item.sc.shot} was read twice for one motion composition.`);
    shots.add(item.sc.shot);
    const read = webMotionRows(ctx, act, item);
    for (const [name, digest] of Object.entries(read.hashes)) {
      if (sources[name] && sources[name] !== digest) throw refused(`Captured source ${name} changed between web-shot reads.`);
      sources[name] = digest;
    }
    for (const row of read.rows) {
      const id = `web:${item.sc.shot}:${row.id}`;
      if (ids.has(id)) throw refused(`Composed motion id ${id} is repeated.`);
      ids.add(id);
      rows.push({...row, id, source_id: row.id, engine: 'web', scene: path.relative(ctx.dir, item.sc.dir)});
    }
    proofs.push({shot: item.sc.shot, frames: [item.sc.shotRecord.frame_in, item.sc.shotRecord.frame_out],
      scene: path.relative(ctx.dir, item.sc.dir), entries: read.rows.length, source_hashes: read.hashes});
  }
  return {rows, base: base.rows, source_hashes: sources, webShots: proofs};
}

export function writeMotion(ctx, act, loaded, {dryRun = false, webShots = []} = {}) {
  if (loaded?.mixed) return writeReverseMotion(ctx, act, loaded.mixed, {dryRun});
  let m;
  try { m = composedMotion(ctx, act, loaded, webShots); }
  catch (error) {
    if (!dryRun) {
      const evidence = invalidateMotion(ctx, act, error.message);
      if (evidence) error.details = {...(error.details ?? {}), motion_failure: evidence};
    }
    throw error;
  }
  if (!m.rows) return m;
  const file = motionFile(ctx, act);
  const mixed = webShots.length ? {frame_base: 'film', web_shots: m.webShots.map((s) => s.shot),
    sources: Object.keys(m.source_hashes).sort(), source_hashes: m.source_hashes} : {};
  if (dryRun) return {written: false, would_write: path.relative(ctx.dir, file), entries: m.rows.length, reason: 'dry run', ...mixed};
  let evidence = {};
  if (webShots.length) {
    const baseFile = path.join(ctx.dir, 'timing', 'motion', 'sources', `act${act}.base.json`);
    const compositionFile = path.join(ctx.dir, 'timing', 'motion', 'sources', `act${act}.composition.json`);
    writeJsonAtomic(baseFile, m.base);
    writeJsonAtomic(compositionFile, {act: Number(act), frame_base: 'film',
      act_frames: [actOf(ctx, act).frameIn, actOf(ctx, act).frameOut],
      fps_num: ctx.fps.num, fps_den: ctx.fps.den, web_shots: m.webShots,
      base_entries: m.base.length, visible_base_entries: m.rows.length - m.webShots.reduce((n, sc) => n + sc.entries, 0),
      limitation: 'Curves with ambiguous ownership across a splice are refused, not clipped or resampled.'});
    evidence = {base_file: path.relative(ctx.dir, baseFile), composition_file: path.relative(ctx.dir, compositionFile)};
  }
  writeJsonAtomic(file, m.rows);
  return {written: true, file: path.relative(ctx.dir, file), entries: m.rows.length, ...mixed, ...evidence};
}

// ---------------------------------------------------------------- what changed since the last emit

/**
 * Where an act's change report goes: timing/visual_events/changes/act<N>.json. A subfolder, because everything that
 * reads the beats lists timing/visual_events/act*.json (QA's events and schema gates, the score planner, the sync check),
 * and a report named act<N>.changes.json beside them would be read as an act's beats.
 */
export const changesFile = (ctx, act) => path.join(ctx.dir, 'timing', 'visual_events', 'changes', `act${act}.json`);

/**
 * What changed between an act's previous events and its new ones, by id (L-timing-D028): beats that moved (with
 * frames), were added or removed, changed kind or size, or had their note reworded, and whether the act went silent or
 * came back. Sound reads it to know what moved. `prev` and `next` are events files' contents (a list or {silent}).
 */
export function eventChanges(prev, next, fps) {
  const list = (x) => (Array.isArray(x) ? x : []);
  const silent = (x) => Boolean(x && !Array.isArray(x) && x.silent);
  const frame = (t) => (typeof t === 'number' && Number.isFinite(t) ? Math.round(t * fps) : null);
  const before = new Map(list(prev).filter((e) => e && e.id).map((e) => [e.id, e]));
  const after = new Map(list(next).filter((e) => e && e.id).map((e) => [e.id, e]));
  const out = {first: prev === null || prev === undefined, moved: [], added: [], removed: [], kind_changed: [], size_changed: [], reworded: [], spans_changed: []};
  if (silent(prev) !== silent(next) && !out.first) out.silent = {was: silent(prev), now: silent(next), reason: silent(next) ? next.reason : null};
  for (const [id, e] of after) {
    const p = before.get(id);
    if (!p) {
      out.added.push({id, t: e.t, frame: frame(e.t), kind: e.kind, shot: e.shot});
      continue;
    }
    if (Math.abs((e.t ?? 0) - (p.t ?? 0)) > 1e-6) out.moved.push({id, from_t: p.t, to_t: e.t, from_frame: frame(p.t), to_frame: frame(e.t), frames: frame(e.t) - frame(p.t)});
    if (e.kind !== p.kind) out.kind_changed.push({id, from: p.kind, to: e.kind});
    if (e.size !== p.size) out.size_changed.push({id, from: p.size, to: e.size});
    if ((e.note ?? '') !== (p.note ?? '')) out.reworded.push({id, from: p.note, to: e.note});
    if ((e.start ?? null) !== (p.start ?? null) || (e.dur ?? null) !== (p.dur ?? null)) out.spans_changed.push({id, from: {start: p.start ?? null, dur: p.dur ?? null}, to: {start: e.start ?? null, dur: e.dur ?? null}});
  }
  for (const [id, p] of before) if (!after.has(id)) out.removed.push({id, t: p.t, frame: frame(p.t), kind: p.kind, shot: p.shot});
  const n = out.moved.length + out.added.length + out.removed.length + out.kind_changed.length + out.size_changed.length + out.reworded.length + out.spans_changed.length + (out.silent ? 1 : 0);
  out.unchanged = !out.first && n === 0;
  out.summary = out.first
    ? `first emit: ${out.added.length} beat${out.added.length === 1 ? '' : 's'}`
    : out.unchanged
      ? 'no beat changed'
      : [
          out.moved.length && `${out.moved.length} moved`,
          out.added.length && `${out.added.length} added`,
          out.removed.length && `${out.removed.length} removed`,
          out.kind_changed.length && `${out.kind_changed.length} changed kind`,
          out.size_changed.length && `${out.size_changed.length} changed size`,
          out.reworded.length && `${out.reworded.length} reworded`,
          out.spans_changed.length && `${out.spans_changed.length} changed span`,
          out.silent && (out.silent.now ? 'went silent' : 'has beats again'),
        ]
          .filter(Boolean)
          .join(', ');
  return out;
}

/** Write an act's change report (timing/visual_events/changes/act<N>.json; see changesFile). */
export function writeChanges(ctx, act, changes) {
  const file = changesFile(ctx, act);
  writeJsonAtomic(file, {generated: GENERATED, act, at: new Date().toISOString(), ...changes});
  return path.relative(ctx.dir, file);
}

/** emit-events for one act: compute, check, write (or, in a dry run, say what would be written), report. */
export async function emitAct(ctx, act, {dryRun = false, captures = null, args = {}} = {}) {
  try {
  const res = await computeEvents(ctx, act, null, {captures, args});
  const file = eventsFile(ctx, act);
  const rel = path.relative(ctx.dir, file);
  if (res.problems.length && !dryRun) {
    const n = res.problems.length;
    throw refused(
      `Act ${act}'s beats don't pass (${n} problem${n > 1 ? 's' : ''}); nothing was written:\n  ${res.problems.slice(0, 20).join('\n  ')}`,
      'Fix them in the act (or its timing), then emit again. `slate emit-events --dry-run` shows them without writing.',
      {problems: res.problems, misses: res.misses},
    );
  }
  // Read and validate the combined motion before changing the event files. The same page read supplies both.
  let motion;
  try { motion = writeMotion(ctx, act, res.loaded, {dryRun, webShots: res.webShots}); }
  catch (error) {
    if (!dryRun) throw error;
    res.problems.push(error.message);
    motion = {written: false, reason: error.message};
  }
  // what the new beats change against the ones they replace, reported before the file is replaced
  const previous = fs.existsSync(file) ? readJson(file) : null;
  const next = res.silent ? {silent: true, reason: res.reason} : res.events;
  const changes = eventChanges(previous, next, ctx.fps.value);
  let summary;
  if (res.silent) {
    summary = {act, file: rel, silent: true, reason: res.reason, events: 0};
    if (!dryRun) writeJsonAtomic(file, {silent: true, reason: res.reason, generated: GENERATED});
  } else {
    const kinds = {};
    for (const e of res.events) kinds[e.kind] = (kinds[e.kind] ?? 0) + 1;
    summary = {act, file: rel, silent: false, events: res.events.length, kinds, span_s: res.events.length ? [res.events[0].t, res.events.at(-1).t] : null};
    if (!dryRun) writeJsonAtomic(file, res.events);
  }
  summary.changes = changes;
  if (!dryRun) summary.changes_file = writeChanges(ctx, act, changes);
  if (res.captures.beats) summary.capture_beats = res.captures.beats;
  if (res.captures.files.length) summary.captures = res.captures;
  if (res.notes.length) summary.notes = res.notes;
  summary.motion = motion;
  summary.sources = [...new Set([...(res.loaded.inputs ?? []), ...(motion.sources ?? [])])].sort();
  if (motion.source_hashes) summary.source_hashes = motion.source_hashes;
  if (dryRun) {
    summary.dry_run = true;
    summary.written = false;
    summary.problems = res.problems;
    summary.misses = res.misses;
    log(`act ${act}: ${res.problems.length ? `${res.problems.length} problem(s)` : 'would pass'}; nothing written (dry run)`);
  } else {
    log(`act ${act}: ${summary.silent ? `silent (${summary.reason})` : `${summary.events} beats`} → ${summary.file}`);
  }
  return summary;
  } catch (error) {
    if (!dryRun && (error.details?.motion_incomplete || actShotScenes(ctx, act).length ||
        (ctx.shots?.shots ?? []).some((shot) => Number(shot.act) === Number(act) && shot.engine === 'web') || webScene(ctx.dir, act))) {
      const evidence = invalidateMotion(ctx, act, error.message);
      if (evidence) error.details = {...(error.details ?? {}), motion_failure: evidence};
    }
    throw error;
  }
}

/** The emit-events command: one act ({act: N}) or every built act. {dry_run: true} writes nothing. */
export async function emitEvents(ctx, args) {
  const acts = args.act !== undefined && args.act !== null ? [actOf(ctx, args.act).act] : ctx.acts.map((a) => a.act);
  const dryRun = Boolean(args.dry_run);
  // Acts read '@film/data', which the entry writes: refresh it first (a fresh film has none yet).
  try { await ensureEntry(ctx, {brandPath: args.brand_path ?? null}); }
  catch (error) {
    if (!dryRun) {
      for (const act of acts) {
        const sourceRoot = path.join(ctx.dir, 'scenes', 'shots');
        const hasSource = (ctx.shots?.shots ?? []).some((shot) => Number(shot.act) === Number(act) && fs.existsSync(path.join(sourceRoot, String(shot.id))));
        const historical = fs.existsSync(path.join(ctx.dir, 'timing', 'motion', 'sources', 'act' + act + '.composition.json'));
        if (hasSource || historical) invalidateMotion(ctx, act, error.message);
      }
    }
    throw error;
  }
  const captureRead = sourceCapture();
  const captures = captureBeats(ctx, {recordSource: captureRead.record});
  const results = [];
  const skipped = [];
  for (const act of acts) {
    let replacements;
    try { replacements = mixedScenesForAct(ctx, act); }
    catch (error) {
      if (!dryRun) invalidateMotion(ctx, act, error.message);
      throw error;
    }
    if (webScene(ctx.dir, act) && !replacements.length) {
      // a web act's beats are read from its page: the open web renderer opens it (and writes nothing in a dry run)
      const file = eventsFile(ctx, act);
      const previous = fs.existsSync(file) ? readJson(file) : null;
      const out = await emitWebAct(ctx, act, args, captures, {dryRun});
      for (const a of out) {
        if (Number(a.act) !== Number(act)) continue;
        const next = dryRun ? (Array.isArray(a.beats) ? a.beats : {silent: true, reason: a.beats?.reason ?? a.reason}) : fs.existsSync(file) ? readJson(file) : null;
        a.changes = eventChanges(previous, next, ctx.fps.value);
        if (!dryRun) a.changes_file = writeChanges(ctx, act, a.changes);
      }
      results.push(...out);
      continue;
    }
    try {
      results.push(await emitAct(ctx, act, {dryRun, captures, args}));
    } catch (e) {
      // With every act asked for, an unbuilt placeholder act is skipped; any other failure stops the run.
      if (args.act === undefined && e instanceof EngineError && e.kind === 'usage' && /isn't built yet/.test(e.message)) {
        const waiting = captures.byAct.get(act)?.length ?? 0;
        skipped.push({act, reason: 'not built yet', ...(waiting ? {capture_beats: waiting} : {})});
        continue;
      }
      throw e;
    }
  }
  return {acts: results, skipped, ...(dryRun ? {dry_run: true} : {})};
}

/**
 * The motion trace alone (the `trace` command, and render's trace option): the act's modules loaded as they are now,
 * against the same '@film/data' (brand included) the picture renders with. Returns writeMotion()'s answer plus the
 * scene files it read (`sources`), so a caller can record what made the trace.
 */
export async function traceAct(ctx, act, {brandPath = null} = {}) {
  try {
    if (baseScene(ctx, act)?.engine === 'web' && mixedScenesForAct(ctx, act).length) {
      return writeReverseMotion(ctx, act, await readReverseAct(ctx, act, {brand_path: brandPath}));
    }
    await ensureEntry(ctx, {brandPath});
    const loaded = await loadAct(ctx, act);
    const webShots = [];
    for (const sc of motionShotScenes(ctx, act)) {
      webShots.push({sc, ...(await webShotPage(ctx, act, sc, {brand_path: brandPath}, {trace_rows: true}))});
    }
    const trace = writeMotion(ctx, act, loaded, {webShots});
    return {...trace, sources: [...new Set([...(loaded.inputs ?? []), ...(trace.sources ?? [])])].sort()};
  } catch (error) {
    const evidence = invalidateMotion(ctx, act, error.message);
    if (evidence) error.details = {...(error.details ?? {}), motion_failure: evidence};
    throw error;
  }
}

export function readEventsFile(ctx, act) {
  const file = eventsFile(ctx, act);
  return fs.existsSync(file) ? readJson(file) : null;
}
