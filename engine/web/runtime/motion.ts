// Brand eases for GSAP, and tweens that write the act's motion trace.
//
// A web scene moves things with the brand's motion law, exactly as a Remotion act does: the brand's named eases, its
// duration tokens, moves that start and end on whole film frames. tween() is the one door:
//
//   tween('#title', {opacity: [0, 1], y: [24, 0]}, {id: 'title-in', at: word('W01', 'clock'), dur: 'base', ease: 'enter', entrance: true});
//
// For each property it
//   1. rounds the move onto whole film frames (the start from `at`, film seconds, or `frame_in`; the length from `dur`,
//      a duration token or seconds, or `frame_out`), the same rounding slate.clock uses;
//   2. registers the move with the kit's motion registry (defineMotion), so `web-render` writes it into
//      timing/motion/actN.json and `slate qa motion` checks it like any Remotion move;
//   3. adds a GSAP fromTo on a paused timeline at the matching act time, eased by the brand's own curve.
// The trace samples the curve with the kit's maths at whole frames and GSAP draws with the very same function, so the
// picture and the trace agree frame for frame.
//
// Exact endpoints: the kit's eases return exactly 0 at the start and exactly 1 at the end. GSAP rounds positions and
// durations to 1e-7 s, which can leave a whole frame a hair before a move's end, so the GSAP ease snaps to its ends
// within 1e-4 of a frame (far inside the smallest motion-blur sample offset, about 0.004 of a frame at 64 samples).
import {gsap} from 'gsap';
import {defineMotion, durFrames, easeNamed, fpsRational, frameOf, law, roleEase, secOf, shotAt} from '../../remotion/kit/core/index';
import {sceneAct} from './env';
import {mainTimeline} from './timeline';

/** How close to a move's start or end (in frames) the GSAP ease snaps to exactly 0 or 1. */
export const END_SNAP_FRAMES = 1e-4;
/** The snap for eases registered by name (their duration isn't known): exact up to tweens of ~15,000 frames. */
export const NAMED_END_SNAP = 1e-6;

export type GsapEase = (p: number) => number;

/**
 * A brand ease as a GSAP ease: exactly 0 at the start and exactly 1 at the end. `frames` is the move's length, which
 * sets how close to the ends GSAP's rounding is absorbed.
 */
export function brandEase(name: string, frames?: number): GsapEase {
  const e = easeNamed(name);
  const eps = frames && frames > 0 ? END_SNAP_FRAMES / frames : NAMED_END_SNAP;
  return (p: number) => (p <= eps ? 0 : p >= 1 - eps ? 1 : e(p));
}

/**
 * Register the brand's eases with GSAP as "brand.<name>" (and each role as "brand.<role>"), for tweens written by
 * hand. Only tween() writes the motion trace, so moves the film's QA should see go through tween().
 */
export function registerBrandEases(): string[] {
  const L = law();
  const names: string[] = [];
  for (const name of L.names) {
    gsap.registerEase(`brand.${name}`, brandEase(name));
    names.push(`brand.${name}`);
  }
  for (const [role, name] of Object.entries(L.roles)) {
    if (!name || L.eases[role]) continue;
    gsap.registerEase(`brand.${role}`, brandEase(name));
    names.push(`brand.${role}`);
  }
  return names;
}

/** Extra trace keys `slate qa motion` reads when an engine writes them. */
export type TraceExtra = {
  /** the unit of the values: 'px' (GSAP x/y), 'deg', 'rad', 'world' (three.js units) … */
  unit?: string;
  /** the element's size in px, so a scale or a rotation can be speed-checked at its edges */
  size?: number | [number, number];
  /** a reason this move breaks the law on purpose; its violations become REVIEW items a person accepts */
  exception?: string;
};

/**
 * What the element is, for the speed limits `slate qa motion` holds it to (the kit's defineMotion writes these into the
 * trace): `class` rig, ui, carry or whip (a page or card carried across the frame says carry; a rig part is rig and the
 * camera a carry without saying so), and a declared exemption (`exempt`: reading_sweep or steady) with its `reason`.
 */
export type TraceClass = {
  class?: string;
  exempt?: string;
  reason?: string;
  /**
   * For type: the type role it's set in (hero, h1, body, label …) and its words. The hero-entrance, settled and
   * legibility checks read them, and stay quiet without them.
   */
  role?: string;
  text?: string;
};

const extras = new Map<string, TraceExtra>();
const problems = new Set<string>();

/** Extra trace keys by move id (defineScene merges them into the trace rows). */
export const traceExtras = (): Map<string, TraceExtra> => extras;

/** What tween() had to assume (listed with the kit's problems by slateScene.problems()). */
export const tweenProblems = (): string[] => [...problems];

/** A move's value: a number (the end, starting from where the property is now) or [from, to]. */
export type TweenValue = number | [number, number];

export type TweenOptions = TraceExtra & TraceClass & {
  /** the move's id in the trace (one property) or its prefix (several: "<id>.<prop>") */
  id: string;
  /** the shot it belongs to (default: the shot its first frame falls in) */
  shot?: string;
  /** a brand ease name or a role (entrance, move, state, exit); default: entrance for entrances, else move */
  ease?: string;
  /** the start in film seconds (a kit beat: word(), startFor(), shot().start) … */
  at?: number;
  /** … or in film frames */
  frame_in?: number;
  /** the end in film frames … */
  frame_out?: number;
  /** … or the length: a duration token of the brand's scale ('base') or seconds */
  dur?: number | string;
  /** an entrance (the motion law checks entrances start from at least 85% scale) */
  entrance?: boolean;
  /** a rig name, when the move is one channel of a rig */
  rig?: string;
  /** the timeline to put it on (default: the scene's main timeline) */
  timeline?: gsap.core.Timeline;
  /** the traced property's name when it differs from the GSAP property ("position.x" for a three.js object) */
  prop?: string;
};

export type TweenResult = {ids: string[]; shot: string; ease: string; frame_in: number; frame_out: number; start: number; duration: number};

type Anim = Record<string, number>;
const lastTo = new WeakMap<object, Anim>();

function currentValue(target: object | undefined, prop: string): number {
  if (!target) return 0;
  const v = Number(gsap.getProperty(target, prop));
  return Number.isFinite(v) ? v : 0;
}

/**
 * Move `target`'s numeric properties with the brand's motion law, on whole film frames, and trace every one. Never
 * throws at module level: a missing length or an unknown ease is recorded (slateScene.problems()) and the move still
 * draws.
 */
export function tween(target: gsap.TweenTarget, props: Record<string, TweenValue>, opts: TweenOptions): TweenResult {
  const act = sceneAct();
  const {num, den} = fpsRational();
  const ease = opts.ease ?? roleEase(opts.entrance ? 'entrance' : 'move');
  let frameIn: number;
  if (opts.frame_in !== undefined) frameIn = Math.round(opts.frame_in);
  else if (opts.at !== undefined) frameIn = frameOf(opts.at);
  else {
    problems.add(`tween "${opts.id}" has no start (give at, film seconds, or frame_in); it starts at the act's first frame`);
    frameIn = act.frameIn;
  }
  let frameOut: number;
  if (opts.frame_out !== undefined) frameOut = Math.round(opts.frame_out);
  else if (opts.dur !== undefined) frameOut = frameIn + durFrames(opts.dur);
  else {
    problems.add(`tween "${opts.id}" has no length (give dur, a duration token, or frame_out); it lasts one frame`);
    frameOut = frameIn + 1;
  }
  const shot = opts.shot ?? shotAt(secOf(frameIn))?.id ?? act.shots[0] ?? '';
  const frames = Math.max(0, frameOut - frameIn);
  const duration = (frames * den) / num;
  const start = ((frameIn - act.frameIn) * den) / num;
  const tl = opts.timeline ?? mainTimeline();
  const first = (gsap.utils.toArray(target)[0] as object | undefined) ?? undefined;
  const before: Anim = (first && lastTo.get(first)) || {};
  const after: Anim = {...before};
  const keys = Object.keys(props);
  const ids: string[] = [];
  for (const key of keys) {
    const v = props[key];
    const from = Array.isArray(v) ? Number(v[0]) : (before[key] ?? currentValue(first, key));
    const to = Array.isArray(v) ? Number(v[1]) : Number(v);
    const id = keys.length > 1 ? `${opts.id}.${key}` : opts.id;
    ids.push(id);
    defineMotion({
      id,
      shot,
      prop: keys.length === 1 && opts.prop ? opts.prop : key,
      ease,
      frame_in: frameIn,
      frame_out: frameOut,
      from,
      to,
      entrance: Boolean(opts.entrance),
      rig: opts.rig ?? null,
      ...(opts.class !== undefined ? {class: opts.class} : {}),
      ...(opts.exempt !== undefined ? {exempt: opts.exempt} : {}),
      ...(opts.reason !== undefined ? {reason: opts.reason} : {}),
      ...(opts.role !== undefined ? {role: opts.role} : {}),
      ...(opts.text !== undefined ? {text: opts.text} : {}),
    });
    const extra: TraceExtra = {};
    if (opts.unit) extra.unit = opts.unit;
    if (opts.size !== undefined) extra.size = opts.size;
    if (opts.exception) extra.exception = opts.exception;
    if (Object.keys(extra).length) extras.set(id, extra);
    // The first move of a property shows its start state from the first frame; later ones take over from the move
    // before them, so they must not stamp their start state over it at load.
    const firstMove = !(key in before);
    tl.fromTo(
      target,
      {[key]: from},
      {[key]: to, duration, ease: brandEase(ease, frames), immediateRender: firstMove, overwrite: false},
      start,
    );
    after[key] = to;
  }
  if (first) lastTo.set(first, after);
  return {ids, shot, ease, frame_in: frameIn, frame_out: frameOut, start, duration};
}
