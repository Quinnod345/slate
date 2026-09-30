// Where a web scene is: which film, which act, which frames, as the renderer sets it on window.__SLATE__ before the
// page's own scripts run. Scene time `t` is act time: 0 at the act's first frame, t = frame × fps_den ÷ fps_num.
// The film's own clock (film seconds, shots, words) comes from '@film/data' through the kit, so an act drawn here and
// an act drawn in Remotion read the same numbers.
import {actSpan, acts, fpsRational, frameSize} from '../../remotion/kit/core/index';

export const WEB_RUNTIME_VERSION = '0.1.0';

/** What the renderer tells the page (window.__SLATE__). */
export type SceneGlobals = {
  engine: 'web';
  version: string;
  mode: 'render' | 'emit';
  standalone: boolean;
  film: {id: string; fps_num: number; fps_den: number; width: number; height: number};
  act: {act: number; name: string | null; frameIn: number; frameOut: number; frames: number; shots: string[]};
  frames: [number, number];
  samples: number;
  shutter: number;
};

declare global {
  interface Window {
    __SLATE__?: SceneGlobals;
  }
}

/** The renderer's globals, or null when the page runs outside the renderer (a unit test, say). */
export function sceneGlobals(): SceneGlobals | null {
  return typeof window !== 'undefined' && window.__SLATE__ ? window.__SLATE__ : null;
}

export type SceneAct = {
  act: number;
  name: string | null;
  /** first film frame of the act */
  frameIn: number;
  /** the film frame after its last */
  frameOut: number;
  frames: number;
  shots: string[];
  /** film seconds of its first frame */
  start: number;
  /** film seconds of its end */
  end: number;
};

/** The act this page draws: the renderer's, else the film's first act. */
export function sceneAct(): SceneAct {
  const g = sceneGlobals();
  const {num, den} = fpsRational();
  if (g) {
    return {...g.act, start: (g.act.frameIn * den) / num, end: (g.act.frameOut * den) / num};
  }
  const first = acts()[0];
  const span = first === undefined ? null : actSpan(first);
  if (!span) {
    return {act: 1, name: null, frameIn: 0, frameOut: 0, frames: 0, shots: [], start: 0, end: 0};
  }
  return {act: Number(span.act), name: null, frameIn: span.frameIn, frameOut: span.frameOut, frames: span.frames, shots: [], start: span.start, end: span.end};
}

/** Film seconds of the act's first frame. */
export const actStart = (): number => sceneAct().start;

/** Act seconds → film seconds (what the film's word timings, events and shots are in). */
export const filmTime = (t: number): number => t + actStart();

/** Film seconds → act seconds (what seek(t) and GSAP positions are in). */
export const actTime = (filmSeconds: number): number => filmSeconds - actStart();

/** Act seconds of an act frame (fractional frames are fine): frame × fps_den ÷ fps_num. */
export function timeOfFrame(frame: number): number {
  const {num, den} = fpsRational();
  return (frame * den) / num;
}

/** The frame size from film.json (width, height, short and long sides). */
export const sceneSize = frameSize;
