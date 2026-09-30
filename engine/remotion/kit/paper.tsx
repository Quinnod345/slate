// Cards, the ground and grain, in whatever look the brand chose: paper cards with real thickness and pages behind
// them in the papery look, flat surfaces in the others. Colours come from the brand's tokens, sizes from the frame.
//
// Rules the films learned the hard way, kept as defaults:
// - opacity or a filter on a preserve-3d element flattens it, so both go on the leaves; a card that fades draws as one
//   flat sheet (FadeCard), or its grey thickness shows through its face;
// - pages behind a card live in the card's own space, so they stay behind its face at any angle;
// - while a layer moves it carries a raster hint (Chrome then moves it at sub-pixel precision and text glides instead
//   of stepping a pixel at a time); at rest it drops it and rasterises crisply; its key changes each ~3% of scale and
//   every 6° of tilt so Chrome rebuilds it at its new size.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {alpha, mix} from './core/color';
import {fps} from './core/film';
import {lerp} from './core/spline';
import type {Tokens} from './core/tokens';
import {useFrameSize, useLook, useTokens} from './context';

// ------------------------------------------------------------------------------------------------------------ poses
export type Pose = {x: number; y: number; z: number; rx: number; ry: number; rz: number; s: number; op: number; blur: number};
export const P = (x = 0, y = 0, z = 0, rx = 0, ry = 0, s = 1, op = 1, rz = 0, blur = 0): Pose => ({x, y, z, rx, ry, rz, s, op, blur});
export const lerpPose = (a: Pose, b: Pose, u: number): Pose => ({
  x: lerp(a.x, b.x, u),
  y: lerp(a.y, b.y, u),
  z: lerp(a.z, b.z, u),
  rx: lerp(a.rx, b.rx, u),
  ry: lerp(a.ry, b.ry, u),
  rz: lerp(a.rz, b.rz, u),
  s: lerp(a.s, b.s, u),
  op: lerp(a.op, b.op, u),
  blur: lerp(a.blur, b.blur, u),
});
/** Chain pose changes: each step moves from the running pose toward its target by its progress u. */
export const poseChain = (start: Pose, steps: [Pose, number][]): Pose => steps.reduce((p, [to, u]) => lerpPose(p, to, u), start);

// -------------------------------------------------------------------------------------------------- raster stability
export const RASTER_STEP = 1.03;
export const RASTER_HINT: React.CSSProperties = {willChange: 'transform'};
/** A React key that changes each time `scale` moves by about 3%. */
export const rasterKey = (scale: number, prefix = 'r'): string => `${prefix}${Math.round(Math.log(Math.max(Math.abs(scale), 1e-4)) / Math.log(RASTER_STEP))}`;
/** The on-screen scale of a plane pushed to depth z under a perspective of `persp` px. */
export const depthScale = (z: number, persp: number): number => persp / Math.max(persp - z, 1);
/** The key's tilt part: changes when a layer turns exactly flat and every 6° of tilt. */
export const tiltKey = (rx: number, ry: number): string => {
  const a = Math.max(Math.abs(rx), Math.abs(ry));
  return a === 0 ? '' : `t${Math.round(a / 6)}`;
};
/** The raster key of a card with this pose under a perspective. */
export const cardKey = (pose: Pose, persp: number): string => rasterKey(pose.s * depthScale(pose.z, persp), 'card') + tiltKey(pose.rx, pose.ry);
/** Whether a pose (a function of film time) is moving at t: it differs one frame either side. */
export function poseMoving(f: (t: number) => Partial<Pose>, t: number): boolean {
  const dt = 1 / fps();
  const a = f(t - dt);
  const b = f(t);
  const c = f(t + dt);
  const keys = ['x', 'y', 'z', 'rx', 'ry', 'rz', 's'] as const;
  return keys.some((k) => Math.abs((a[k] ?? 0) - (b[k] ?? 0)) > 1e-6 || Math.abs((b[k] ?? 0) - (c[k] ?? 0)) > 1e-6);
}
/** The raster hint while an element moves on its own transform, nothing at rest. */
export const motionHint = (moving: boolean): React.CSSProperties => (moving ? RASTER_HINT : {});
/** Whether an ease's progress is strictly between its ends. */
export const inMotion = (u: number): boolean => u > 0.0001 && u < 0.9999;

// ---------------------------------------------------------------------------------------------------------- palettes
export type PaperPalette = {
  dark: boolean;
  page: string;
  card: string;
  cardBorder: string;
  edge: string;
  edgeDeep: string;
  ghost: string;
  ink: string;
  ink2: string;
  ink3: string;
  hair: string;
  skel: string;
  skel2: string;
  /** "r,g,b" for this ground's shadows */
  shadowRgb: string;
};

/** The paper palette on the page (light) or on the brand's inverse ground (dark), from the brand's tokens. */
export function paperPalette(t: Tokens, dark = false): PaperPalette {
  const c = t.color;
  if (!dark)
    return {
      dark,
      page: c.page,
      card: c.surface,
      cardBorder: mix(c.surface, c.ink, 0.07),
      edge: mix(c.surface, c.ink, 0.09),
      edgeDeep: mix(c.surface, c.ink, 0.15),
      ghost: mix(c.surface, c.page, 0.55),
      ink: c.ink,
      ink2: c.ink2,
      ink3: c.ink3,
      hair: mix(c.surface, c.ink, 0.08),
      skel: mix(c.surface, c.ink, 0.08),
      skel2: mix(c.surface, c.ink, 0.05),
      shadowRgb: c.shadow,
    };
  return {
    dark,
    page: c.inverse,
    card: alpha(c.onInverse, 0.04),
    cardBorder: alpha(c.onInverse, 0.11),
    edge: mix(c.inverse, c.onInverse, 0.07),
    edgeDeep: mix(c.inverse, c.onInverse, 0.04),
    ghost: alpha(c.onInverse, 0.025),
    ink: c.onInverse,
    ink2: alpha(c.onInverse, 0.78),
    ink3: alpha(c.onInverse, 0.55),
    hair: alpha(c.onInverse, 0.12),
    skel: alpha(c.onInverse, 0.09),
    skel2: alpha(c.onInverse, 0.055),
    shadowRgb: c.shadow,
  };
}

/** The palette for the ground in context (light unless asked for dark). */
export function usePaper(dark = false): PaperPalette {
  return paperPalette(useTokens(), dark);
}

/** Shadows by look, sized from the frame's short side. */
export function cardShadow(kind: 'layered' | 'soft' | 'none', rgb: string, short: number, dark = false): string | undefined {
  const s = (f: number) => +(f * short).toFixed(2);
  if (kind === 'none') return undefined;
  if (dark) return `0 ${s(0.028)}px ${s(0.065)}px -${s(0.022)}px rgba(${rgb},0.55)`;
  if (kind === 'soft') return `0 ${s(0.004)}px ${s(0.012)}px rgba(${rgb},0.06), 0 ${s(0.02)}px ${s(0.046)}px -${s(0.016)}px rgba(${rgb},0.14)`;
  return `0 ${s(0.0009)}px ${s(0.0019)}px rgba(${rgb},.05), 0 ${s(0.013)}px ${s(0.028)}px -${s(0.009)}px rgba(${rgb},.12), 0 ${s(0.046)}px ${s(0.083)}px -${s(0.028)}px rgba(${rgb},.14)`;
}

// ------------------------------------------------------------------------------------------------------------- cards
/** A page behind a card, in the card's own space (a deck fanning out). Offsets in px. */
export type Page = {dx: number; dy: number; dz: number; rz: number; op: number; blur?: number; face?: string};

export type CardProps = {
  w: number;
  h: number;
  pose: Pose;
  /** on the inverse ground */
  dark?: boolean;
  radius?: number;
  /** thickness layers (the look decides; 0 is flat) */
  layers?: number;
  face?: string;
  shadow?: string;
  pages?: Page[];
  border?: boolean;
  /** pass !poseMoving(poseFn, t) for a card that moves (see the raster notes above) */
  atRest?: boolean;
  persp?: number;
  children?: React.ReactNode;
};

/** A card: paper with thickness in a papery look, a flat surface otherwise. Children are laid out in card px. */
export function Card({w, h, pose, dark = false, radius, layers, face, shadow, pages = [], border, atRest = true, persp, children}: CardProps) {
  const look = useLook();
  const {short, long} = useFrameSize();
  const pal = usePaper(dark);
  const r = radius ?? look.card.radius * short;
  const n = layers ?? look.card.layers;
  const gap = Math.max(look.card.layerGap * short, 0.5);
  const hairline = border ?? look.card.border !== 'none';
  const sh = shadow ?? cardShadow(look.card.shadow, pal.shadowRgb, short, dark);
  const p = persp ?? look.camera.perspective * long;
  if (pose.op <= 0.001) return null;
  const box: React.CSSProperties = {position: 'absolute', left: 0, top: 0, width: w, height: h, borderRadius: r};
  const leaf: React.CSSProperties = {opacity: pose.op < 0.999 ? pose.op : undefined, filter: pose.blur > 0.2 ? `blur(${pose.blur.toFixed(2)}px)` : undefined};
  const bw = Math.max(1, 0.0014 * short);
  return (
    <div
      key={cardKey(pose, p) + (atRest ? '' : 'm')}
      style={{
        ...box,
        left: -w / 2,
        top: -h / 2,
        transformStyle: 'preserve-3d',
        transform: `translate3d(${pose.x}px, ${pose.y}px, ${pose.z}px) rotateX(${pose.rx}deg) rotateY(${pose.ry}deg) rotateZ(${pose.rz}deg) scale(${pose.s})`,
        ...motionHint(!atRest),
      }}
    >
      {pages.map((pg, i) =>
        pg.op * pose.op > 0.002 ? (
          <div
            key={`p${i}`}
            style={{
              ...box,
              background: pg.face ?? pal.ghost,
              boxShadow: `inset 0 0 0 ${bw}px ${pal.cardBorder}`,
              transform: `translate3d(${pg.dx}px, ${pg.dy}px, ${pg.dz}px) rotateZ(${pg.rz}deg)`,
              opacity: pg.op * pose.op,
              filter: (pg.blur ?? 0) > 0.2 ? `blur(${pg.blur}px)` : undefined,
            }}
          />
        ) : null,
      )}
      {Array.from({length: n}, (_, i) => (
        <div
          key={i}
          style={{
            ...box,
            ...leaf,
            background: i === n - 1 ? pal.edgeDeep : pal.edge,
            transform: `translateZ(${-(i + 1) * gap}px)`,
            boxShadow: i === n - 1 ? sh : undefined,
          }}
        />
      ))}
      <div
        style={{
          ...box,
          ...leaf,
          overflow: 'hidden',
          background: face ?? pal.card,
          boxShadow: [n === 0 ? sh : undefined, hairline ? `inset 0 0 0 ${bw}px ${pal.cardBorder}` : undefined].filter(Boolean).join(', ') || undefined,
        }}
      >
        {children}
      </div>
    </div>
  );
}

/**
 * A card that fades as one sheet: while translucent it is drawn flat inside one wrapper that carries the opacity (so
 * its thickness never shows through its face); once opaque it is the full card again.
 */
export function FadeCard(props: CardProps) {
  const {pose} = props;
  if (pose.op <= 0.001) return null;
  if (pose.op >= 0.999) return <Card {...props} />;
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        top: 0,
        opacity: pose.op,
        transform: `translate3d(${pose.x}px, ${pose.y}px, ${pose.z}px) rotateX(${pose.rx}deg) rotateY(${pose.ry}deg) rotateZ(${pose.rz}deg) scale(${pose.s})`,
      }}
    >
      <Card {...props} pose={{...pose, x: 0, y: 0, z: 0, rx: 0, ry: 0, rz: 0, s: 1, op: 1}} />
    </div>
  );
}

// -------------------------------------------------------------------------------------------------- ground and grain
/** Grain: fractal noise, multiplied on light grounds, soft light on dark ones. */
export function Grain({dark = false, op}: {dark?: boolean; op?: number}) {
  const {width, height} = useFrameSize();
  const look = useLook();
  const amount = op ?? (dark ? look.ground.grain * 2.4 : look.ground.grain);
  if (amount <= 0) return null;
  const id = dark ? 'slate-grain-d' : 'slate-grain-l';
  return (
    <AbsoluteFill style={{mixBlendMode: dark ? 'soft-light' : 'multiply', opacity: amount, pointerEvents: 'none'}}>
      <svg width={width} height={height}>
        <filter id={id}>
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="7" stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width={width} height={height} filter={`url(#${id})`} />
      </svg>
    </AbsoluteFill>
  );
}

/** A faint construction grid (the blueprint look), from the ink colour. */
export function Grid({op, cells = 12}: {op?: number; cells?: number}) {
  const {width, height, short} = useFrameSize();
  const look = useLook();
  const t = useTokens();
  const amount = op ?? look.ground.grid;
  if (amount <= 0) return null;
  const step = short / cells;
  const lines: React.ReactNode[] = [];
  for (let x = (width / 2) % step; x < width; x += step) lines.push(<line key={`v${x}`} x1={x} x2={x} y1={0} y2={height} />);
  for (let y = (height / 2) % step; y < height; y += step) lines.push(<line key={`h${y}`} x1={0} x2={width} y1={y} y2={y} />);
  return (
    <AbsoluteFill style={{pointerEvents: 'none', opacity: amount}}>
      <svg width={width} height={height}>
        <g stroke={t.color.ink} strokeWidth={Math.max(1, short * 0.0009)}>
          {lines}
        </g>
      </svg>
    </AbsoluteFill>
  );
}

/** The page: its colour, the look's soft light (a bloom on paper, a lift and a vignette on the inverse), grid, grain. */
export function Ground({dark = false, children}: {dark?: boolean; children?: React.ReactNode}) {
  const look = useLook();
  const t = useTokens();
  const pal = paperPalette(t, dark);
  const c = t.color;
  const bloom = look.ground.bloom;
  const vig = look.ground.vignette;
  return (
    <AbsoluteFill style={{backgroundColor: pal.page}}>
      {bloom > 0 && (
        <AbsoluteFill
          style={{
            background: dark
              ? `radial-gradient(ellipse 70% 60% at 50% 45%, ${alpha(mix(c.inverse, c.accent, 0.35), 0.55 * bloom)}, ${alpha(c.inverse, 0)} 70%)`
              : `radial-gradient(ellipse 70% 60% at 50% 42%, ${alpha(c.surface, 0.9 * bloom)}, ${alpha(c.surface, 0)} 70%)`,
          }}
        />
      )}
      {look.ground.grid > 0 && <Grid />}
      {children}
      {dark && vig > 0 && <AbsoluteFill style={{background: `radial-gradient(ellipse 85% 75% at 50% 46%, ${alpha(c.inverseDeep, 0)} 55%, ${alpha(mix(c.inverseDeep, c.ink, 0.5), 0.45 * vig)} 100%)`}} />}
      <Grain dark={dark} />
    </AbsoluteFill>
  );
}
