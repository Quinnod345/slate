// The scene protocol: what a web scene registers so the renderer can draw it at any time t.
//
//   window.slateScene = {duration, fps, seek(t), ready()}
//
// The renderer loads the page, awaits ready() once, then for every sub-frame sets the page clock to t and awaits
// seek(t), waits for fonts, images and a real painted frame, and takes a screenshot. `t` is act time in seconds: 0 at
// the act's first frame, t = frame × fps_den ÷ fps_num. A scene must be a pure function of t: any frame renders alone,
// in any order, on any page, and comes out the same every time.
//
// defineScene() builds that object for you and keeps the promises the renderer relies on:
//   - GSAP's root timeline is taken off the wall clock the moment this module loads, and driven by t instead
//     (gsap.updateRoot(t)), so plain gsap.to() tweens made at load follow the frame clock;
//   - timelines made with timeline() are paused and seeked per frame (totalTime(t), callbacks included), as HyperFrames
//     and the films' own renderer did: the page never plays, it seeks;
//   - seek hooks (sequence frames, R3F's advance, a three.js render) run in order after the timelines;
//   - ready() waits for the page's fonts and runs the web font gate, so a substituted face fails the render the way it
//     fails a Remotion render;
//   - trace() and events() hand the renderer the act's motion trace and visual beats from the same modules the
//     picture reads, and misses() the word lookups that fell back to an estimate (emit refuses beats on a guess).
import {gsap} from 'gsap';
import {actEvents, eventProblems, missLabels, motionProblems, traceMotions} from '../../remotion/kit/core/index';
import {sceneAct, sceneGlobals} from './env';
import {runWebFontGate, type WebFontGateReport} from './fonts';
import {traceExtras, tweenProblems} from './motion';
import {timelines} from './timeline';

export {addTimeline, mainTimeline, timeline} from './timeline';

/** The protocol version the renderer checks. Bump it when the shape of slateScene changes. */
export const SCENE_PROTOCOL = 1;

/** Where a seek sits: the act frame it belongs to, which sub-frame sample it is, and the film frame it draws. */
export type SeekInfo = {
  /** act seconds (the same t as seek's first argument) */
  t: number;
  /** the master frame, counted from the act's first frame */
  frame: number;
  /** which sub-frame sample of that frame (0-based) and how many there are */
  sample: number;
  samples: number;
  /** the fractional act frame this sample draws (frame ± a fraction of the shutter) */
  local: number;
  /** the fractional film frame this sample draws: the act's first film frame + local */
  filmFrame: number;
};

type Awaitable<T> = T | Promise<T>;

/** What the renderer reads from window.slateScene. */
export type SlateScene = {
  protocol?: number;
  runtime?: string;
  /** the scene's length in act seconds (the renderer checks it against timing/shots.json) */
  duration?: number;
  /** the scene's frame rate; the renderer refuses a scene whose rate isn't the film's */
  fps?: number | {num: number; den: number};
  seek(t: number, info?: SeekInfo): Awaitable<void>;
  ready?(): Awaitable<void>;
  /** the act's visual beats (film seconds), for `web-render {emit: true}` */
  events?: unknown[] | (() => Awaitable<unknown[]>);
  /** why the act has no beats, when it has none on purpose */
  silent?: string | {reason: string} | null;
  /** the act's motion trace (timing/motion/actN.json rows) */
  trace?(): unknown[];
  /** everything the kit had to assume or refuse (unknown eases, word misses, bad events) */
  problems?(): string[];
  /** the kit's word lookups that fell back to an estimate (also in problems()): a beat on one would land on a guess */
  misses?(): string[];
  /** the web font gate's report, once ready() has run */
  fonts?(): WebFontGateReport | null;
};

declare global {
  interface Window {
    slateScene?: SlateScene;
  }
}

export type SeekHook = (t: number, info: SeekInfo) => Awaitable<void>;
export type ReadyHook = () => Awaitable<void>;

const seekHooks: SeekHook[] = [];
const renderHooks: SeekHook[] = [];
const readyHooks: ReadyHook[] = [];
let current = 0;
let currentInfo: SeekInfo | null = null;

// GSAP's root timeline follows t from here on, not the wall clock. The page clock the renderer installs already holds
// time still during load, so everything a scene makes at load sits at t = 0.
gsap.ticker.remove(gsap.updateRoot);
gsap.ticker.lagSmoothing(0);

/** Run `fn` on every seek, after the timelines (a sequence frame, a canvas drawn by hand). */
export function onSeek(fn: SeekHook): () => void {
  seekHooks.push(fn);
  return () => {
    const i = seekHooks.indexOf(fn);
    if (i >= 0) seekHooks.splice(i, 1);
  };
}

/** Run `fn` last on every seek, once everything has moved: this is where three.js renders and R3F advances. */
export function onRender(fn: SeekHook): () => void {
  renderHooks.push(fn);
  return () => {
    const i = renderHooks.indexOf(fn);
    if (i >= 0) renderHooks.splice(i, 1);
  };
}

/** Run `fn` once before the first frame (load a model, decode textures). The renderer waits for it. */
export function onReady(fn: ReadyHook): void {
  readyHooks.push(fn);
}

/** The act time of the frame being drawn (0 before the first seek). */
export const sceneTime = (): number => current;

/** Where the current seek sits, or null before the first one. */
export const seekInfo = (): SeekInfo | null => currentInfo;

/** The act's length in seconds, from the film clock (timing/shots.json, through the renderer or '@film/data'). */
export function actDuration(): number {
  const g = sceneGlobals();
  if (g) return (g.act.frames * g.film.fps_den) / g.film.fps_num;
  const a = sceneAct();
  return a.end - a.start;
}

function infoFor(t: number): SeekInfo {
  const g = sceneGlobals();
  const num = g?.film.fps_num ?? 1;
  const den = g?.film.fps_den ?? 1;
  const local = (t * num) / den;
  const frame = Math.round(local);
  return {t, frame, sample: 0, samples: 1, local, filmFrame: (g?.act.frameIn ?? sceneAct().frameIn) + local};
}

export type SceneOptions = {
  /** moves everything to time t (after the timelines and seek hooks) */
  seek?: SeekHook;
  /** draws, once everything has moved (three.js renders here) */
  render?: SeekHook;
  /** once before the first frame */
  ready?: ReadyHook;
  /** override the act's length (seconds); leave it out and the film clock decides */
  duration?: number;
  /** the act's visual beats, film seconds (plus any the scene registered with the kit's defineEvent) */
  events?: unknown[] | (() => Awaitable<unknown[]>);
  /** why the act has no beats, when it has none on purpose */
  silent?: string | {reason: string} | null;
  /** run the web font gate in ready() (default true) */
  fonts?: boolean;
  /** register the scene on window (default true; tests turn it off) */
  register?: boolean;
};

let fontReport: WebFontGateReport | null = null;

/**
 * Build and register window.slateScene. Call it once, at module level, after making the scene's timelines and tweens.
 */
export function defineScene(opts: SceneOptions = {}): SlateScene {
  const g = sceneGlobals();
  const scene: SlateScene = {
    protocol: SCENE_PROTOCOL,
    runtime: g?.version,
    duration: opts.duration ?? actDuration(),
    fps: g ? {num: g.film.fps_num, den: g.film.fps_den} : undefined,
    async seek(t: number, info?: SeekInfo) {
      current = t;
      currentInfo = info ?? infoFor(t);
      // The early motion-blur samples of an act's first frame sit just before t = 0: GSAP draws its start state there.
      const at = Math.max(0, t);
      gsap.updateRoot(at);
      for (const tl of timelines) tl.totalTime(at, false);
      for (const fn of seekHooks) await fn(t, currentInfo);
      if (opts.seek) await opts.seek(t, currentInfo);
      for (const fn of renderHooks) await fn(t, currentInfo);
      if (opts.render) await opts.render(t, currentInfo);
    },
    async ready() {
      if (typeof document !== 'undefined' && document.fonts) await document.fonts.ready;
      if (opts.fonts !== false) fontReport = await runWebFontGate();
      for (const fn of readyHooks) await fn();
      if (opts.ready) await opts.ready();
    },
    async events() {
      const declared = typeof opts.events === 'function' ? await opts.events() : (opts.events ?? []);
      const act = sceneAct().act;
      const registered = actEvents(act);
      return [...(Array.isArray(declared) ? declared : []), ...(Array.isArray(registered) ? registered : [])];
    },
    silent: opts.silent ?? null,
    trace() {
      const extras = traceExtras();
      return traceMotions({act: sceneAct().act}).map((row) => ({...row, ...(extras.get(row.id) ?? {})}));
    },
    problems() {
      return [...motionProblems(), ...tweenProblems(), ...eventProblems(), ...missLabels()];
    },
    misses() {
      return missLabels();
    },
    fonts: () => fontReport,
  };
  if (opts.register !== false && typeof window !== 'undefined') window.slateScene = scene;
  return scene;
}
