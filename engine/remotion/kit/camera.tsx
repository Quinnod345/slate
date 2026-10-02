// Cameras and places, promoted from the acts. Three kinds, one job each:
// - Camera: the 2.5D world camera (CSS perspective, origin at the frame's centre): tilts, trucks and gentle pushes.
// - LookAtCamera: looks AT a world point, dollies in z (a push feels like a camera travelling, not a zoom), and tilts
//   about the look-at point so a tilt never swings the frame off its subject. camAt() chains its moves on the brand's
//   eases; project() finds where a world point lands on screen.
// - Cam2D and WorldLayer: a 2D camera over poses with zoom in log space (a huge translateZ rasterises badly), and a
//   layer that follows it with no transform at all at rest, so what it holds is bit-identical to screen space.
// Each passes its on-screen scale down (RasterScaleProvider), so type inside sizes its tilt in tilt mode; in tilt mode
// Camera also drops its raster hint and keys (it's drawn afresh every frame) and At tilts its type.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {easeNamed, roleEase} from './core/motion';
import {lerp, monoSpline} from './core/spline';
import {useFrameSize, useLook} from './context';
import {depthScale, motionHint, rasterKey, RasterScaleProvider, tiltKey, TypeTilt, useRasterMode, useRasterScale, type Pose} from './paper';

/** The look's perspective in px for this frame. */
export function usePerspective(): number {
  const look = useLook();
  const {long} = useFrameSize();
  return look.camera.perspective * long;
}

/** The world camera: children are posed in world px around the frame's centre. z > 0 pushes in. */
export function Camera({x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0, persp, atRest, children}: {x?: number; y?: number; z?: number; rx?: number; ry?: number; rz?: number; persp?: number; atRest?: boolean; children?: React.ReactNode}) {
  const {cx, cy} = useFrameSize();
  const p = persp ?? usePerspective();
  const rest = atRest ?? true;
  const tilt = useRasterMode() === 'tilt';
  const outer = useRasterScale();
  return (
    <AbsoluteFill style={{perspective: `${p}px`, perspectiveOrigin: '50% 50%', overflow: 'hidden'}}>
      <div
        // hint mode: the world moves as one smooth layer while the camera moves, rebuilt each ~3% of a push and as it
        // tilts; tilt mode: drawn afresh every frame
        key={tilt ? undefined : rasterKey(depthScale(z, p), 'cam') + tiltKey(rx, ry) + (rest ? '' : 'm')}
        style={{
          position: 'absolute',
          left: cx,
          top: cy,
          transformStyle: 'preserve-3d',
          transform: `translate3d(${-x}px, ${-y}px, ${z}px) rotateX(${rx}deg) rotateY(${ry}deg) rotateZ(${rz}deg)`,
          ...(tilt ? {} : motionHint(!rest)),
        }}
      >
        <RasterScaleProvider value={outer * depthScale(z, p)}>{children}</RasterScaleProvider>
      </div>
    </AbsoluteFill>
  );
}

/** A flat placement inside a camera (a label or overlay at a world position). In tilt mode its type is tilted. */
export function At({x, y, z = 0, children, style}: {x: number; y: number; z?: number; children?: React.ReactNode; style?: React.CSSProperties}) {
  const tilt = useRasterMode() === 'tilt';
  const outer = useRasterScale();
  const p = usePerspective();
  return (
    <div style={{position: 'absolute', left: 0, top: 0, transform: `translate3d(${x}px, ${y}px, ${z}px)`, ...style}}>
      {tilt ? (
        <RasterScaleProvider value={outer * depthScale(z, p)}>
          <TypeTilt>{children}</TypeTilt>
        </RasterScaleProvider>
      ) : (
        children
      )}
    </div>
  );
}

/** A flat group placed in world space (x, y its top-left), optionally lifted in z and tilted. */
export function Place({x, y, z = 0, rx = 0, ry = 0, rz = 0, s = 1, origin = '0 0', style, children}: {x: number; y: number; z?: number; rx?: number; ry?: number; rz?: number; s?: number; origin?: string; style?: React.CSSProperties; children?: React.ReactNode}) {
  const outer = useRasterScale();
  const p = usePerspective();
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        top: 0,
        transformStyle: 'preserve-3d',
        transformOrigin: origin,
        transform: `translate3d(${x}px, ${y}px, ${z}px) rotateX(${rx}deg) rotateY(${ry}deg) rotateZ(${rz}deg) scale(${s})`,
        ...style,
      }}
    >
      <RasterScaleProvider value={outer * Math.abs(s) * depthScale(z, p)}>{children}</RasterScaleProvider>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ look-at camera
/** s is the apparent size of the z = 0 plane at the look-at point (s = P ÷ (P − z)). */
export type Cam = {x: number; y: number; s: number; rx: number; ry: number; rz: number};
export const cam = (x = 0, y = 0, s = 1, rx = 0, ry = 0, rz = 0): Cam => ({x, y, s, rx, ry, rz});

const zOf = (s: number, P: number) => P * (1 - 1 / s);
const sOf = (z: number, P: number) => P / (P - z);

/** Blend two cameras: the dolly (z) interpolates, not the scale, so a push travels evenly. */
export function lerpCam(a: Cam, b: Cam, u: number, P: number): Cam {
  return {x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), s: sOf(lerp(zOf(a.s, P), zOf(b.s, P), u), P), rx: lerp(a.rx, b.rx, u), ry: lerp(a.ry, b.ry, u), rz: lerp(a.rz, b.rz, u)};
}

export type CamMove = {t0: number; dur: number; to: Cam; ease?: string};

/** The camera at t: its start, then each move blending from the running camera to its target on its ease. */
export function camAt(t: number, start: Cam, moves: readonly CamMove[], P: number): Cam {
  let c = start;
  for (const m of moves) {
    const e = easeNamed(m.ease ?? roleEase('move'));
    const u = m.dur > 0 ? e((t - m.t0) / m.dur) : t >= m.t0 ? 1 : 0;
    if (u <= 0) continue;
    c = lerpCam(c, m.to, u, P);
  }
  return c;
}

/** Where a world point lands on screen under a camera with no tilt (and its scale there). */
export function project(c: Cam, x: number, y: number, z: number, P: number, width: number, height: number): {x: number; y: number; k: number} {
  const k = P / (P - zOf(c.s, P) - z);
  return {x: width / 2 + (x - c.x) * k, y: height / 2 + (y - c.y) * k, k};
}

/** The look-at camera. */
export function LookAtCamera({c, persp, style, children}: {c: Cam; persp?: number; style?: React.CSSProperties; children?: React.ReactNode}) {
  const {cx, cy} = useFrameSize();
  const P = persp ?? usePerspective();
  const outer = useRasterScale();
  return (
    <AbsoluteFill style={{perspective: `${P}px`, perspectiveOrigin: `${cx}px ${cy}px`, overflow: 'hidden', ...style}}>
      <div
        style={{
          position: 'absolute',
          left: cx,
          top: cy,
          width: 0,
          height: 0,
          transformStyle: 'preserve-3d',
          transform: `translateZ(${zOf(c.s, P).toFixed(3)}px) rotateX(${c.rx}deg) rotateY(${c.ry}deg) rotateZ(${c.rz}deg) translate3d(${(-c.x).toFixed(3)}px, ${(-c.y).toFixed(3)}px, 0px)`,
        }}
      >
        <RasterScaleProvider value={outer * c.s}>{children}</RasterScaleProvider>
      </div>
    </AbsoluteFill>
  );
}

// --------------------------------------------------------------------------------------------- 2D camera and layer
/** A 2D camera: looks at world point (cx, cy) at zoom k. screen = centre + (world − (cx, cy)) × k. */
export type Cam2D = {cx: number; cy: number; k: number};

/** The camera at rest for a frame: world px are screen px. */
export const restCam2D = (width: number, height: number): Cam2D => ({cx: width / 2, cy: height / 2, k: 1});

/** A world point on screen. */
export const screenOf = (c: Cam2D, x: number, y: number, width: number, height: number) => ({x: width / 2 + (x - c.cx) * c.k, y: height / 2 + (y - c.cy) * c.k});

/** A world pose (x, y around the frame's centre) as the pose to draw with the world camera at rest. */
export function onCam2D(c: Cam2D, p: Pose, width: number, height: number): Pose {
  const s = screenOf(c, width / 2 + p.x, height / 2 + p.y, width, height);
  return {...p, x: s.x - width / 2, y: s.y - height / 2, s: p.s * c.k};
}

export type Cam2DKey = readonly [number, Cam2D, string];

/** Camera keys [t, camera, ease of the segment from there]; the zoom moves in log space (an even dolly). */
export function cam2DAt(t: number, keys: readonly Cam2DKey[]): Cam2D {
  if (!keys.length) return {cx: 0, cy: 0, k: 1};
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 0; i < keys.length - 1; i++) {
    const [t0, a, e] = keys[i];
    const [t1, b] = keys[i + 1];
    if (t < t1) {
      const u = t1 > t0 ? easeNamed(e)((t - t0) / (t1 - t0)) : 1;
      return {cx: lerp(a.cx, b.cx, u), cy: lerp(a.cy, b.cy, u), k: Math.exp(lerp(Math.log(a.k), Math.log(b.k), u))};
    }
  }
  return keys[keys.length - 1][1];
}

/**
 * A long, even drift from one camera to another through timed fractions of the way (monotone, at rest at both ends,
 * zoom in log space): for continuous camera drifts, which the motion law lets run long.
 */
export function drift2D(t: number, from: Cam2D, to: Cam2D, marks: readonly (readonly [number, number])[]): Cam2D {
  const u = monoSpline(t, marks);
  return {cx: lerp(from.cx, to.cx, u), cy: lerp(from.cy, to.cy, u), k: Math.exp(lerp(Math.log(from.k), Math.log(to.k), u))};
}

/** A 3D world camera's pan and push, for a WorldLayer that follows it over the z = 0 plane. */
export type WorldCam = {x: number; y: number; z: number};
export const worldAtRest = (c: WorldCam): boolean => Math.abs(c.x) < 1e-9 && Math.abs(c.y) < 1e-9 && Math.abs(c.z) < 1e-9;

/**
 * A layer in world px (origin at the frame's centre) that follows the camera over the z = 0 plane. At rest it has no
 * transform at all, so a mark or a contract drawn in it is bit-identical to screen space; while the camera moves (pans
 * and pushes only) it takes the same mapping as a card on the z = 0 plane, as a 2D transform.
 */
export function WorldLayer({cam: c, persp, children}: {cam: WorldCam; persp?: number; children?: React.ReactNode}) {
  const {cx, cy} = useFrameSize();
  const P = persp ?? usePerspective();
  const outer = useRasterScale();
  const rest = worldAtRest(c);
  const k = P / (P - c.z);
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <div
        style={{
          position: 'absolute',
          left: cx,
          top: cy,
          width: 0,
          height: 0,
          transformOrigin: '0 0',
          transform: rest ? undefined : `scale(${k.toFixed(6)}) translate(${(-c.x).toFixed(4)}px, ${(-c.y).toFixed(4)}px)`,
        }}
      >
        <RasterScaleProvider value={outer * (rest ? 1 : k)}>{children}</RasterScaleProvider>
      </div>
    </AbsoluteFill>
  );
}
