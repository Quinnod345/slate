// The root of every act composition: turns Remotion's frame into film time, including the motion-blur sub-samples,
// loads and checks the brand's fonts before the first frame, and puts the kit in context.
//
// Frame 0 of an act composition is its first shot's first film frame. With sub > 1 each master frame is drawn `sub`
// times across the shutter (180° by default, centred on the frame: phase −90°), and the renderer averages them into
// true motion blur. Every scene is a pure function of film time, so any frame renders alone.
import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {actSpan, fps, frameSize, shot} from './core/film';
import {KitProvider, TokenScope} from './context';
import {FontGate} from './fonts';

export type ActRootProps = {
  /** the act (its first shot is frame 0) … */
  act?: number | string;
  /** … or the first shot, by id */
  firstShot?: string;
  /** motion-blur sub-samples per master frame (1 = none) */
  sub?: number;
  /** shutter angle in degrees (180 is film-standard) */
  shutter?: number;
  /** load and check the brand's fonts before the first frame (default on) */
  fonts?: boolean;
  /** put the brand's tokens on the page as CSS variables (default on) */
  cssVars?: boolean;
  children?: React.ReactNode;
};

/** The film frame a composition frame draws, for `sub` samples across a shutter of `shutter` degrees. */
export function filmFrameOf(compositionFrame: number, firstFrame: number, sub = 1, shutter = 180): number {
  const S = Math.max(1, Math.round(sub));
  const f = Math.floor(compositionFrame / S);
  const k = compositionFrame % S;
  const local = S === 1 ? f : f + (shutter / 360) * ((k + 0.5) / S - 0.5);
  return firstFrame + local;
}

export function ActRoot({act, firstShot, sub = 1, shutter = 180, fonts = true, cssVars = true, children}: ActRootProps) {
  const F = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const first = firstShot ?? (act !== undefined ? actSpan(act)?.first : undefined);
  const start = first ? shot(first).frameIn : 0;
  const frame = filmFrameOf(F, start, sub, shutter);
  const body = cssVars ? <TokenScope>{children}</TokenScope> : children;
  return (
    <FontGate enabled={fonts}>
      <KitProvider frame={frame} sub={Math.max(1, Math.round(sub))} width={width} height={height}>
        {body}
      </KitProvider>
    </FontGate>
  );
}

export type CompositionSpec = {id: string; durationInFrames: number; fps: number; width: number; height: number; defaultProps: {sub: number}};

/**
 * What to register for an act (the engine's generated Root uses this, so an act's length, rate and size have one
 * source): its length in master frames × sub, the film's rate and size.
 */
export function actComposition(act: number | string, opts: {sub?: number; id?: string} = {}): CompositionSpec | null {
  const span = actSpan(act);
  if (!span) return null;
  const sub = Math.max(1, Math.round(opts.sub ?? 1));
  const size = frameSize();
  return {id: opts.id ?? `Act${act}`, durationInFrames: span.frames * sub, fps: fps(), width: size.width, height: size.height, defaultProps: {sub}};
}
