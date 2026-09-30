// The timelines a scene seeks every frame. Kept apart from scene.ts so the tween helper (motion.ts) and the scene can
// both reach them without importing each other.
import {gsap} from 'gsap';

/** Every timeline defineScene() seeks, in the order they were made. */
export const timelines = new Set<gsap.core.Timeline>();

/** A paused GSAP timeline the scene seeks every frame. Positions are act seconds (0 = the act's first frame). */
export function timeline(vars: gsap.TimelineVars = {}): gsap.core.Timeline {
  const tl = gsap.timeline({...vars, paused: true});
  timelines.add(tl);
  return tl;
}

/** Seek a timeline you made yourself every frame (it is paused first). */
export function addTimeline<T extends gsap.core.Timeline>(tl: T): T {
  tl.pause();
  timelines.add(tl);
  return tl;
}

let main: gsap.core.Timeline | null = null;

/** The scene's default timeline, where tween() puts moves that don't name one. */
export function mainTimeline(): gsap.core.Timeline {
  if (!main) main = timeline();
  return main;
}
