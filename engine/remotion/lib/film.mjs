// Reading a film folder the way the engine needs it: film.json for the frame rate and size (the only source of
// either; nothing in the engine assumes a resolution or a rate), timing/shots.json for the acts and their frames.
//
// Python resolves every frame (slate.timing); the engine only reads the resolved JSON and never re-derives a
// boundary. That is what keeps Remotion, the open web renderer and After Effects on one clock.
import fs from 'node:fs';
import path from 'node:path';
import {readJson, usage} from './common.mjs';

/** Timing files the scenes read through '@film/data' (shots is required; the rest appear as they're made). */
export const TIMING_FILES = {
  shots: 'timing/shots.json',
  timeline: 'timing/timeline.json',
  voWords: 'timing/vo_words.json',
  voCues: 'timing/vo_cues.json',
  events: 'timing/events.json',
  captions: 'timing/captions.json',
  spoken: 'timing/spoken.json',
  // how the film's language turns a word into a match key (written by the Python bridge from slate.timing's table)
  lang: '.slate/lang.json',
};

/** The key each one has in '@film/data''s default export: the kit's FilmData (engine/remotion/kit/core/types.ts). */
export const DATA_KEYS = {
  shots: 'shots',
  timeline: 'timeline',
  voWords: 'vo_words',
  voCues: 'vo_cues',
  events: 'events',
  captions: 'captions',
  spoken: 'spoken',
  lang: 'lang',
};

/**
 * Where the spoken forms come from when timing/ has none: .slate/spoken.json, written by the Python bridge before
 * every engine call from slate.timing.spoken (how each number and symbol in the reads is said), so every bundle,
 * check and render reads the same map.
 */
export const SPOKEN_FALLBACK = '.slate/spoken.json';

/**
 * Where a derived film's reused files really are: .slate/refs.json, {film-relative path: absolute path in the parent},
 * written by the Python bridge from slate.state.lock.resolve before every call. A derived film reuses its parent's
 * timings and captures by reference and copies nothing, so they aren't in its own folder.
 */
export const REFS_FILE = '.slate/refs.json';

function refsOf(dir) {
  const file = path.join(dir, REFS_FILE);
  if (!fs.existsSync(file)) return {};
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
  } catch {
    return {};
  }
}

/** A film's file (or folder): its own copy, else the parent's that .slate/refs.json names, else its own (missing) path. */
export function realFile(dir, rel) {
  const own = path.join(dir, rel);
  if (fs.existsSync(own)) return own;
  const real = refsOf(dir)[rel];
  return typeof real === 'string' && path.isAbsolute(real) && fs.existsSync(real) ? real : own;
}

function positiveInt(value, what, file) {
  const n = Number(value);
  if (!Number.isInteger(n) || n <= 0) throw usage(`${file}: ${what} must be a positive whole number (got ${JSON.stringify(value)}).`);
  return n;
}

/**
 * Load a film folder. Returns {dir, id, film, shots, acts, fps: {num, den, value, rational}, width, height}.
 * Acts come from the shots (each shot names its act); names come from shots.json's acts[] when present.
 */
export function loadFilm(filmArg, {requireShots = true} = {}) {
  if (!filmArg) throw usage('No film given: pass "film" (the film folder\'s path).', 'slate commands pass it for you; run them inside a film folder or with --film.');
  const dir = path.resolve(String(filmArg));
  const filmFile = path.join(dir, 'film.json');
  if (!fs.existsSync(filmFile)) throw usage(`No film at ${dir}: there's no film.json there.`, 'slate new <id> --brand <brand> --format <format> creates one.');
  const film = readJson(filmFile);
  const num = positiveInt(film.fps_num, 'fps_num', 'film.json');
  const den = positiveInt(film.fps_den, 'fps_den', 'film.json');
  const width = positiveInt(film.width, 'width', 'film.json');
  const height = positiveInt(film.height, 'height', 'film.json');
  const ctx = {
    dir,
    id: String(film.id ?? path.basename(dir)),
    film,
    fps: {num, den, value: num / den, rational: `${num}/${den}`},
    width,
    height,
    shots: null,
    acts: [],
  };
  const shotsFile = realFile(dir, TIMING_FILES.shots);
  if (!fs.existsSync(shotsFile)) {
    if (requireShots) throw usage(`${ctx.id} has no timing/shots.json yet.`, 'Run `slate timing` first: the engine reads frames from it.');
    return ctx;
  }
  ctx.shots = readJson(shotsFile);
  if (ctx.shots.fps_num !== undefined && (Number(ctx.shots.fps_num) !== num || Number(ctx.shots.fps_den) !== den)) {
    throw usage(
      `timing/shots.json was built at ${ctx.shots.fps_num}/${ctx.shots.fps_den} fps but film.json says ${num}/${den}.`,
      'Run `slate timing` again so the shots match the film.',
    );
  }
  ctx.acts = actsOf(ctx.shots);
  return ctx;
}

/** [{act, name, firstShot, lastShot, shots, frameIn, frameOut, frames}] sorted by act, checked contiguous. */
export function actsOf(shotsJson) {
  const list = Array.isArray(shotsJson?.shots) ? shotsJson.shots : [];
  if (!list.length) throw usage('timing/shots.json has no shots.');
  const names = new Map();
  for (const a of shotsJson.acts ?? []) {
    const key = Number(a.index ?? a.act ?? a.id);
    if (Number.isInteger(key)) names.set(key, a.name ?? null);
  }
  const byAct = new Map();
  for (const s of list) {
    const act = Number(s.act);
    if (!Number.isInteger(act) || act < 1) throw usage(`Shot ${s.id} has no act number (got ${JSON.stringify(s.act)}).`);
    if (!Number.isInteger(s.frame_in) || !Number.isInteger(s.frame_out) || s.frame_out <= s.frame_in) {
      throw usage(`Shot ${s.id} has bad frames (${s.frame_in}–${s.frame_out}): frame_out must be after frame_in.`);
    }
    if (!byAct.has(act)) byAct.set(act, []);
    byAct.get(act).push(s);
  }
  const acts = [...byAct.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([act, shots]) => {
      shots.sort((a, b) => a.frame_in - b.frame_in);
      const frameIn = shots[0].frame_in;
      const frameOut = shots[shots.length - 1].frame_out;
      return {
        act,
        name: names.get(act) ?? null,
        firstShot: shots[0].id,
        lastShot: shots[shots.length - 1].id,
        shots: shots.map((s) => s.id),
        frameIn,
        frameOut,
        frames: frameOut - frameIn,
      };
    });
  for (let i = 1; i < acts.length; i++) {
    if (acts[i].frameIn !== acts[i - 1].frameOut) {
      throw usage(
        `Act ${acts[i].act} starts at frame ${acts[i].frameIn}, but act ${acts[i - 1].act} ends at ${acts[i - 1].frameOut}: acts must be contiguous.`,
        'Rebuild the timing with `slate timing`.',
      );
    }
  }
  return acts;
}

/** One act by number, or a usage error naming the acts there are. */
export function actOf(ctx, act) {
  const n = Number(act);
  const found = ctx.acts.find((a) => a.act === n);
  if (!found) {
    throw usage(`${ctx.id} has no act ${JSON.stringify(act)}. Acts: ${ctx.acts.map((a) => a.act).join(', ') || 'none'}.`);
  }
  return found;
}

/** The shot a film frame falls in. */
export function shotAtFrame(ctx, filmFrame) {
  const list = ctx.shots?.shots ?? [];
  return list.find((s) => filmFrame >= s.frame_in && filmFrame < s.frame_out) ?? null;
}

/** Composition ids: Act1…ActN for acts, Film for the whole film, Contract-<Name> for boundary rest states. */
export const compIdForAct = (act) => `Act${act}`;
export const FILM_COMP = 'Film';

/** Turn "Act3" / "Film" / 3 into {kind: 'act', act} or {kind: 'film'}. */
export function parseTarget(ctx, target) {
  if (target === undefined || target === null || target === '') throw usage('Say which act to use ("act": N) or "film".');
  if (target === 'film' || target === FILM_COMP) return {kind: 'film', comp: FILM_COMP, frameIn: ctx.acts[0].frameIn, frames: ctx.acts.at(-1).frameOut - ctx.acts[0].frameIn};
  const m = /^Act(\d+)$/.exec(String(target));
  const n = m ? Number(m[1]) : Number(target);
  const a = actOf(ctx, n);
  return {kind: 'act', act: a.act, comp: compIdForAct(a.act), frameIn: a.frameIn, frames: a.frames, info: a};
}
