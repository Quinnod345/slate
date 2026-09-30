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
import {ENGINE_DIR, EngineError, log, missing, readJson, refused, usage, writeJsonAtomic} from './lib/common.mjs';
import {actOf, realFile} from './lib/film.mjs';
import {ensureEntry, missingIndexNote, sceneIndex} from './entry.mjs';
import {declarations, loadAct} from './load_act.mjs';

/** An act built for the open web renderer: scenes/act<N>/web/index.html (and no Remotion module). */
export function webScene(filmDir, act) {
  if (sceneIndex(filmDir, act)) return null;
  const f = path.join(filmDir, 'scenes', `act${act}`, 'web', 'index.html');
  return fs.existsSync(f) ? f : null;
}

/** The open web renderer's module (SLATE_WEB_RENDER_MODULE swaps it, as for cli.mjs's web-render). */
const webModule = () => (process.env.SLATE_WEB_RENDER_MODULE ? path.resolve(process.env.SLATE_WEB_RENDER_MODULE) : path.join(ENGINE_DIR, 'web', 'render.mjs'));

/**
 * A web act's beats come from its page, so the web renderer's own emit mode writes them (engine/web/render.mjs,
 * webRender({emit: true})), with these same rules (it calls validateEvents above). One command still emits every act.
 */
async function emitWebAct(ctx, act, args, captures) {
  if (captures.strays.length) {
    throw refused(`Capture taps no act holds; nothing was written:\n  ${captures.strays.join('\n  ')}`, 'Place the capture on the film clock again (`slate capture beatmap`).', {problems: captures.strays});
  }
  const file = webModule();
  if (!fs.existsSync(file)) {
    throw missing(`Act ${act} is a web scene, but the open web renderer isn't in this engine (${file}).`, 'Update Slate and run `slate engine install` again.');
  }
  const mod = await import(pathToFileURL(file).href);
  if (typeof mod.webRender !== 'function') throw missing(`${file} doesn't export webRender(args).`);
  const res = await mod.webRender({film: ctx.dir, act, emit: true, ...(args.brand_path ? {brand_path: args.brand_path} : {}), ...(args.gl ? {gl: args.gl} : {})});
  const out = (res?.acts ?? []).map((a) => ({...a, engine: 'web'}));
  // The web emitter reads the page; placed captures' taps on this act join its beats here, under the same rules.
  const taps = captures.byAct.get(Number(act)) ?? [];
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
    const summary = out.find((a) => Number(a.act) === Number(act));
    if (summary) Object.assign(summary, {events: merged.length, kinds, span_s: [merged[0].t, merged.at(-1).t], capture_beats: taps.length, captures: {beats: taps.length, files: captures.files}});
  }
  return out;
}

export const KINDS = ['tap', 'type', 'land', 'snap', 'count', 'draw', 'data', 'page', 'stamp', 'whoosh', 'swell', 'character', 'impact'];
export const SIZES = ['s', 'm', 'l'];
/** Sizes other writers spell out (the capture beat map's default is "small"). */
export const SIZE_ALIASES = {small: 's', medium: 'm', large: 'l'};
export const GENERATED = 'do not edit by hand: slate emit-events';

export const eventsFile = (ctx, act) => path.join(ctx.dir, 'timing', 'visual_events', `act${act}.json`);
export const motionFile = (ctx, act) => path.join(ctx.dir, 'timing', 'motion', `act${act}.json`);

/**
 * Problems with an act's events (empty when they're good). `list` is the array of beats. The act's span allows half a
 * frame either side, as the kit's validateEvents() does, so the two never disagree about a beat on a boundary.
 */
export function validateEvents(ctx, act, list) {
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
    // optional fields the sound side reads: a count's from/to, a sound's length, where it sits across the frame
    for (const k of ['from', 'to', 'x']) {
      if (k in e && (typeof e[k] !== 'number' || !Number.isFinite(e[k]))) problems.push(`${where}: ${k} must be a number`);
    }
    if ('dur' in e && !(typeof e.dur === 'number' && Number.isFinite(e.dur) && e.dur > 0)) problems.push(`${where}: dur must be a positive number of seconds`);
    if ('pan' in e && !(typeof e.pan === 'number' && e.pan >= -1 && e.pan <= 1)) problems.push(`${where}: pan must be a number from -1 (left) to 1 (right)`);
  });
  if (impacts > 1) problems.push(`${impacts} impacts in act ${a.act}: at most one (it is the act's one big landing)`);
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
export function captureBeats(ctx) {
  const root = realFile(ctx.dir, 'captures');
  const byAct = new Map();
  const strays = [];
  const notes = [];
  const files = [];
  if (!fs.existsSync(root)) return {byAct, strays, notes, files};
  for (const name of fs.readdirSync(root).sort()) {
    const file = path.join(root, name, 'beats.json');
    if (!fs.existsSync(file)) continue;
    const rel = path.relative(ctx.dir, file);
    let data;
    try {
      data = JSON.parse(fs.readFileSync(file, 'utf8'));
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
  return {byAct, strays, notes, files};
}

// ---------------------------------------------------------------- computing an act's beats

const byTimeThenId = (x, y) => x.t - y.t || String(x.id).localeCompare(String(y.id));

/**
 * What the act says its events are, merged with the kit's registrations and the captures' taps, and everything that
 * stops them being written: {silent, reason} or {events}, plus {problems, notes, misses, loaded, captures}.
 * `problems` block emit-events and fail the check; a structural error (the module doesn't build, events() throws)
 * throws instead.
 */
export async function computeEvents(ctx, act, loaded = null, {captures = null} = {}) {
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
  const caps = captures ?? captureBeats(ctx);
  const fromCaptures = caps.byAct.get(Number(act)) ?? [];
  for (const s of caps.strays) problems.push(`a capture tap no act holds: ${s}`);
  notes.push(...caps.notes);
  const all = [...(Array.isArray(events) ? events : []), ...(Array.isArray(fromKit) ? fromKit : []), ...fromCaptures];
  const extra = {problems, notes, misses, loaded: mod, captures: {beats: fromCaptures.length, files: caps.files}};
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
export function computeMotion(ctx, act, loaded) {
  if (!loaded || loaded.missing) return {written: false, reason: `act ${act} isn't built yet`};
  if (!loaded.usesKit) return {written: false, reason: "the act doesn't use the kit, so the kit's motion registry has nothing for it"};
  if (!loaded.hooks.motionTrace) return {written: false, reason: 'the kit has no motion registry (traceMotions())'};
  let rows = loaded.hooks.motionTrace.fn();
  if (!Array.isArray(rows)) return {written: false, reason: `the kit's ${loaded.hooks.motionTrace.name}() didn't return a list`};
  const a = actOf(ctx, act);
  rows = rows.filter((r) => r && (r.shot === undefined || (ctx.shots?.shots ?? []).some((s) => s.id === r.shot && Number(s.act) === a.act)));
  return {rows};
}

export function writeMotion(ctx, act, loaded, {dryRun = false} = {}) {
  const m = computeMotion(ctx, act, loaded);
  if (!m.rows) return m;
  const file = motionFile(ctx, act);
  if (dryRun) return {written: false, would_write: path.relative(ctx.dir, file), entries: m.rows.length, reason: 'dry run'};
  writeJsonAtomic(file, m.rows);
  return {written: true, file: path.relative(ctx.dir, file), entries: m.rows.length};
}

/** emit-events for one act: compute, check, write (or, in a dry run, say what would be written), report. */
export async function emitAct(ctx, act, {dryRun = false, captures = null} = {}) {
  const res = await computeEvents(ctx, act, null, {captures});
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
  if (res.captures.beats) summary.capture_beats = res.captures.beats;
  if (res.captures.files.length) summary.captures = res.captures;
  if (res.notes.length) summary.notes = res.notes;
  summary.motion = writeMotion(ctx, act, res.loaded, {dryRun});
  summary.sources = res.loaded.inputs;
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
}

/** The emit-events command: one act ({act: N}) or every built act. {dry_run: true} writes nothing. */
export async function emitEvents(ctx, args) {
  const acts = args.act !== undefined && args.act !== null ? [actOf(ctx, args.act).act] : ctx.acts.map((a) => a.act);
  const dryRun = Boolean(args.dry_run);
  // Acts read '@film/data', which the entry writes: refresh it first (a fresh film has none yet).
  await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
  const captures = captureBeats(ctx);
  const results = [];
  const skipped = [];
  for (const act of acts) {
    if (webScene(ctx.dir, act)) {
      if (dryRun) {
        skipped.push({act, reason: "a web scene: its beats are read from its page, which a dry run doesn't open", engine: 'web'});
      } else {
        results.push(...(await emitWebAct(ctx, act, args, captures)));
      }
      continue;
    }
    try {
      results.push(await emitAct(ctx, act, {dryRun, captures}));
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
  await ensureEntry(ctx, {brandPath});
  const loaded = await loadAct(ctx, act);
  return {...writeMotion(ctx, act, loaded), sources: loaded.inputs ?? []};
}

export function readEventsFile(ctx, act) {
  const file = eventsFile(ctx, act);
  return fs.existsSync(file) ? readJson(file) : null;
}
