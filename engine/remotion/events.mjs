// emit-events: every act's visual beats, from the same module the picture reads, into
// timing/visual_events/act<N>.json, and the kit's motion trace into timing/motion/act<N>.json.
//
// One emitter replaces the films' 22 (Python, esbuild TS and mjs). Its checks are the films' best ones, in one place:
// a known kind and size, a finite time inside the act, the shot the time falls in, unique ids, a note that says what
// the viewer sees, and at most one impact per act. An act with no beats must say why ({"silent": true, "reason"}),
// because "no file" and "no beats" look the same to a sound engine and only one of them is a decision.
import fs from 'node:fs';
import path from 'node:path';
import {EngineError, log, readJson, refused, usage, writeJsonAtomic} from './lib/common.mjs';
import {actOf} from './lib/film.mjs';
import {ensureEntry} from './entry.mjs';
import {declarations, loadAct} from './load_act.mjs';

export const KINDS = ['tap', 'type', 'land', 'snap', 'count', 'draw', 'data', 'page', 'stamp', 'whoosh', 'swell', 'character', 'impact'];
export const SIZES = ['s', 'm', 'l'];
export const GENERATED = 'do not edit by hand: slate emit-events';

export const eventsFile = (ctx, act) => path.join(ctx.dir, 'timing', 'visual_events', `act${act}.json`);
export const motionFile = (ctx, act) => path.join(ctx.dir, 'timing', 'motion', `act${act}.json`);

/** Problems with an act's events (empty when they're good). `list` is the array of beats. */
export function validateEvents(ctx, act, list) {
  const problems = [];
  if (!Array.isArray(list)) return ['the events are not a list'];
  const a = actOf(ctx, act);
  const fps = ctx.fps.value;
  const start = a.frameIn / fps;
  const end = a.frameOut / fps;
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
    } else if (e.t < start - 1e-6 || e.t > end + 1e-6) {
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
    for (const k of ['from', 'to']) {
      if (k in e && (typeof e[k] !== 'number' || !Number.isFinite(e[k]))) problems.push(`${where}: ${k} must be a number`);
    }
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

/** What the act's module says its events are: {silent, reason} or {events: [...]}, sorted by time. */
export async function computeEvents(ctx, act, loaded = null) {
  const mod = loaded ?? (await loadAct(ctx, act));
  if (mod.missing) {
    throw usage(`Act ${act} isn't built yet (no scenes/act${act}/index.tsx).`, 'A placeholder act has no beats to emit; build the act first.');
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
  // Beats the act registered with the kit's defineEvent(), filtered to its shots by the kit's actEvents(act).
  const fromKit = mod.hooks?.kitEvents ? mod.hooks.kitEvents.fn() : [];
  const all = [...(Array.isArray(events) ? events : []), ...(Array.isArray(fromKit) ? fromKit : [])];
  if (events !== undefined && events !== null && !Array.isArray(events)) {
    throw refused(`Act ${act}'s events export isn't a list (or a function returning one).`);
  }
  const silent = decl.silent;
  if (silent !== undefined && silent !== null && silent !== false) {
    const reason = typeof silent === 'string' ? silent : silent?.reason;
    if (typeof reason !== 'string' || !reason.trim()) throw refused(`Act ${act} says it's silent but gives no reason.`, "export const silent = 'why this act has no beats';");
    if (all.length) throw refused(`Act ${act} says it's silent but also has ${all.length} beats.`, 'Keep one: its beats, or its silence.');
    return {silent: true, reason: reason.trim(), loaded: mod};
  }
  if (!all.length) {
    throw refused(
      `Act ${act} has no beats and doesn't say it's silent.`,
      `Export its beats from scenes/act${act} ("export const events = [...]"), or say why it has none ("export const silent = '...'").`,
    );
  }
  const sorted = all.map((e) => ({...e})).sort((x, y) => x.t - y.t);
  return {events: sorted, loaded: mod};
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

export function writeMotion(ctx, act, loaded) {
  const m = computeMotion(ctx, act, loaded);
  if (!m.rows) return m;
  const file = motionFile(ctx, act);
  writeJsonAtomic(file, m.rows);
  return {written: true, file: path.relative(ctx.dir, file), entries: m.rows.length};
}

/** emit-events for one act: validate, write, report. */
export async function emitAct(ctx, act) {
  const res = await computeEvents(ctx, act);
  const file = eventsFile(ctx, act);
  let summary;
  if (res.silent) {
    writeJsonAtomic(file, {silent: true, reason: res.reason, generated: GENERATED});
    summary = {act, file: path.relative(ctx.dir, file), silent: true, reason: res.reason, events: 0};
  } else {
    const problems = validateEvents(ctx, act, res.events);
    if (problems.length) {
      throw refused(`Act ${act}'s beats don't pass (${problems.length} problem${problems.length > 1 ? 's' : ''}):\n  ${problems.slice(0, 20).join('\n  ')}`, 'Fix them in the act, then emit again.', {problems});
    }
    writeJsonAtomic(file, res.events);
    const kinds = {};
    for (const e of res.events) kinds[e.kind] = (kinds[e.kind] ?? 0) + 1;
    summary = {act, file: path.relative(ctx.dir, file), silent: false, events: res.events.length, kinds, span_s: [res.events[0].t, res.events.at(-1).t]};
  }
  summary.motion = writeMotion(ctx, act, res.loaded);
  summary.sources = res.loaded.inputs;
  log(`act ${act}: ${summary.silent ? `silent (${summary.reason})` : `${summary.events} beats`} → ${summary.file}`);
  return summary;
}

/** The emit-events command: one act ({act: N}) or every built act. */
export async function emitEvents(ctx, args) {
  const acts = args.act !== undefined && args.act !== null ? [actOf(ctx, args.act).act] : ctx.acts.map((a) => a.act);
  // Acts read '@film/data', which the entry writes: refresh it first (a fresh film has none yet).
  await ensureEntry(ctx, {brandPath: args.brand_path ?? null});
  const results = [];
  const skipped = [];
  for (const act of acts) {
    try {
      results.push(await emitAct(ctx, act));
    } catch (e) {
      // With every act asked for, an unbuilt placeholder act is skipped; any other failure stops the run.
      if (args.act === undefined && e instanceof EngineError && e.kind === 'usage' && /isn't built yet/.test(e.message)) {
        skipped.push({act, reason: 'not built yet'});
        continue;
      }
      throw e;
    }
  }
  return {acts: results, skipped};
}

/** The motion trace alone (render's trace option). */
export async function traceAct(ctx, act) {
  await ensureEntry(ctx);
  const loaded = await loadAct(ctx, act);
  return writeMotion(ctx, act, loaded);
}

export function readEventsFile(ctx, act) {
  const file = eventsFile(ctx, act);
  return fs.existsSync(file) ? readJson(file) : null;
}
