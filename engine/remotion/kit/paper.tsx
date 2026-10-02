// Cards, the ground and grain, in whatever look the brand chose: paper cards with real thickness and pages behind
// them in the papery look, flat surfaces in the others. Colours come from the brand's tokens, sizes from the frame.
//
// Rules the films learned the hard way, kept as defaults:
// - opacity or a filter on a preserve-3d element flattens it, so both go on the leaves; a card that fades draws as one
//   flat sheet (FadeCard), or its grey thickness shows through its face;
// - pages behind a card live in the card's own space, so they stay behind its face at any angle;
// - while a layer moves it carries a raster hint (Chrome then moves it at sub-pixel precision and text glides instead
//   of stepping a pixel at a time); at rest it drops it and rasterises crisply; its key changes each ~3% of scale and
//   every 6° of tilt so Chrome rebuilds it at its new size. That's hint mode, the default.
// - tilt mode (opt in per act with RasterModeProvider): type drawn under a y-skew too small to see, so Skia places it at
//   quarter pixels instead of snapping it to whole ones, and everything rasterised afresh every frame: crisp and smooth
//   at once, with nothing to switch at rest. See "raster modes" below.
import React from 'react';
import {AbsoluteFill, getInputProps} from 'remotion';
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

// ------------------------------------------------------------------------------------------------------ raster modes
/**
 * Two ways to keep slow motion smooth in the 16-sample renders (the films, 2026-09-30: "It's so jittery. Things are
 * shaking up and down"). Headless Chrome's Skia snaps each line of text to whole device pixels while the text's matrix
 * keeps its x axis exactly horizontal, and lays boxes placed by left and top on whole pixels, so under a slow move type
 * steps a pixel at a time while the shapes around it glide.
 *
 * - **hint** (the default, and every act that doesn't opt in): a moving layer carries `will-change` and is rebuilt at
 *   rest (RASTER_HINT, motionHint, the keys above). It glides, but the compositor resamples what it moves: type under a
 *   slow push came out about 15% softer.
 * - **tilt**: type is drawn under a skewY too small to see (tiltDeg: about 0.034° at full size, a quarter pixel across
 *   a 900 px card), inside the layer that holds it, and nothing is hinted or kept between frames. Skia then places each
 *   glyph at quarter pixels: within about 0.13 px of its true path, as crisp as a fresh raster, and two renders are
 *   bit-identical. At rest type sits at its true place to a quarter pixel instead of a whole one.
 *
 * Opt in at an act's root: `<RasterModeProvider value={actRasterMode()}>`. A render can ask for the old drawing with
 * the input prop `raster: "hint"` (render_mb's props), so an act's reference before it converted is one command, as
 * long as the conversion is a no-op in hint mode (TiltPlane, TiltSpan and TypeTilt are). In tilt mode the kit tilts the
 * type in a Card's face (faceTilt), an `At`, and type moved by PerChar, PerWord, Arrive, KineticWords and Field; an act
 * puts TiltSpan or TypeTilt around any other type. The placement rules (the kit README keeps them too):
 * - tilt type only, never shapes: a tilt moves anything else off whole pixels by 0.0006 × its distance from the tilt's
 *   centre and softens its edges, so keep every tilted box tight around its type, and give a wide card with thin
 *   lines far from its centre `faceTilt={false}` and a TiltSpan around each text where it sits;
 * - the tilt goes inside anything Chrome may raster on its own (a blurred box, anything with an opacity below 1, a
 *   filter or a blend mode: TiltSpan directly around the text) and after a text's own transform, never on a composited
 *   layer's own transform or above one (the compositor would rotate the raster and soften it);
 * - no tilted plane in a camera's 3D world, under a scaled parent, around absolutely placed type (it skews about its
 *   empty box's origin) or under a clip-path: a TiltSpan around each text instead;
 * - running text tilts as one block, never per character (per-character boxes lose the kerning), so a trailing
 *   character fades by its colour's alpha rather than an opacity;
 * - a box that moves under a tilt lays out at 0 and moves by transform (Chrome rounds a fractional layout offset under
 *   any transform that isn't a pure translation), and a card whose size morphs takes morphPose;
 * - both acts on a boundary render in the same mode, and a hand-over draws its type with the same tilt on both sides.
 */
export type RasterMode = 'hint' | 'tilt';
const RasterModeCtx = React.createContext<RasterMode>('hint');
/** Opt an act (or any subtree) into a raster mode: `<RasterModeProvider value={actRasterMode()}>`. */
export const RasterModeProvider = RasterModeCtx.Provider;
/** The raster mode in force here. */
export const useRasterMode = (): RasterMode => React.useContext(RasterModeCtx);

/**
 * The mode an act renders in: `fallback` (an act that has moved over passes 'tilt', the default), unless the render
 * asks for another with the input prop `raster` ("hint" or "tilt"), as a reference render of the act before it moved
 * over does. `props` stands in for the render's input props where there are none (tests, Node).
 */
export function actRasterMode(fallback: RasterMode = 'tilt', props?: Record<string, unknown>): RasterMode {
  let given: unknown;
  try {
    given = (props ?? (getInputProps() as Record<string, unknown>))?.raster;
  } catch {
    given = undefined;
  }
  return given === 'hint' || given === 'tilt' ? given : fallback;
}

/** The on-screen scale of the raster the current element is drawn into (the camera × the card × any scale between). */
const RasterScaleCtx = React.createContext(1);
export const RasterScaleProvider = RasterScaleCtx.Provider;
export const useRasterScale = (): number => React.useContext(RasterScaleCtx);
/** True inside a tilt, so type there doesn't tilt itself again (it would do no harm, just nothing). */
const TiltedCtx = React.createContext(false);

/** The smallest y-skew Skia can't round away (it rounds a text matrix to 1/1024ths), with a margin. */
export const TILT_SKEW = 6e-4;
/** The tilt in degrees that Skia still sees at on-screen scale `scale`: larger for smaller type, the same from 1 up. */
export const tiltDeg = (scale = 1): number => (Math.atan(TILT_SKEW / Math.min(Math.max(Math.abs(scale), 0.1), 1)) * 180) / Math.PI;
/** The tilt as a transform, at on-screen scale `scale`. */
export const tiltTransform = (scale = 1): string => `skewY(${tiltDeg(scale).toFixed(4)}deg)`;

/** The transform to append to type that moves on its own transform: the tilt in tilt mode, unless already in one. */
export function useTypeTilt(scale = 1): string {
  const mode = useRasterMode();
  const tilted = React.useContext(TiltedCtx);
  const s = useRasterScale();
  return mode === 'tilt' && !tilted ? ` ${tiltTransform(s * scale)}` : '';
}

/** The raster hint for something moving on its own transform: motionHint(moving) in hint mode, nothing in tilt mode. */
export function useMotionHint(moving: boolean): React.CSSProperties {
  return useRasterMode() === 'tilt' ? {} : motionHint(moving);
}

/**
 * The tilt on an inline box of its own, directly around a text: for type in a box Chrome may raster apart from its
 * card (a character or word under a blur, anything holding an opacity below 1, a filter or a blend mode). It pivots on
 * the text itself. In hint mode, just the text.
 */
export function TiltSpan({scale = 1, children}: {scale?: number; children?: React.ReactNode}) {
  const mode = useRasterMode();
  const s = useRasterScale();
  if (mode !== 'tilt') return <>{children}</>;
  return (
    <span data-slate-tilt="span" style={{display: 'inline-block', transform: tiltTransform(s * scale)}}>
      <TiltedCtx.Provider value={true}>{children}</TiltedCtx.Provider>
    </span>
  );
}

/**
 * A box whose type is drawn with the tilt in tilt mode (a plain box in hint mode, laid out the same). `style` lays it
 * out as any div; a `transform` in it comes first and the tilt after. `scale`: its on-screen scale inside the current
 * raster (1 unless it scales its own content). Put it inside the layer that carries the type: in a 3D container,
 * around the container's flat content, never around anything 3D. The skew pivots on the box's own centre, so the box
 * must enclose its type (an empty wrapper around absolutely placed text shears it by pixels: use TiltSpan there).
 *
 * `plane`: for free type (on the page, in no card, often over a camera's layers) and type that is the direct child of
 * your own preserve-3d container. Chrome gives such type a layer of its own, and a layer whose own transform carries the
 * tilt is rasterised untilted (it snaps) and skewed by the compositor (it softens). As a plane the box sits in depth
 * (translateZ(0.01px), too little to see) and the tilt goes on a block inside it, drawn into that raster; its children
 * then sit in that block, so a flex or grid box takes no `plane`.
 */
export function TypeTilt({scale = 1, style, plane = false, face = false, children}: {scale?: number; style?: React.CSSProperties; plane?: boolean; face?: boolean; children?: React.ReactNode}) {
  const mode = useRasterMode();
  const s = useRasterScale();
  if (mode !== 'tilt') return <div style={style}>{children}</div>;
  const skew = tiltTransform(s * scale);
  if (plane)
    return (
      <div data-slate-tilt="plane" style={{...style, transform: `${style?.transform ?? ''} translateZ(0.01px)`.trim()}}>
        <div style={{transform: skew}}>
          <TiltedCtx.Provider value={true}>{children}</TiltedCtx.Provider>
        </div>
      </div>
    );
  return (
    <div data-slate-tilt={face ? 'face' : 'type'} style={{...style, transform: `${style?.transform ?? ''} ${skew}`.trim()}}>
      <TiltedCtx.Provider value={true}>{children}</TiltedCtx.Provider>
    </div>
  );
}

/**
 * TypeTilt as a plane in tilt mode, and nothing at all in hint mode (the children as they are): for type a conversion
 * adds a box around, so the act's hint-mode drawing, its reference, stays exactly what it was. Give it a style that lays
 * the box out where its content already was.
 */
export function TiltPlane({style, scale = 1, children}: {style?: React.CSSProperties; scale?: number; children?: React.ReactNode}) {
  if (useRasterMode() !== 'tilt') return <>{children}</>;
  return (
    <TypeTilt plane scale={scale} style={style}>
      {children}
    </TypeTilt>
  );
}

// ------------------------------------------------------------------------------------------------ morphing cards
/**
 * morphPose's mark on a pose: in tilt mode a Card whose size morphs lays itself out at whole even sizes and scales to its
 * true size, so its edges and content sit on their true places as its size changes. A symbol key keeps Pose all numbers,
 * and a spread copy of the pose keeps it.
 */
export const EXACT: unique symbol = Symbol('exact');
export const isExact = (pose: Pose): boolean => !!(pose as Pose & {[EXACT]?: boolean})[EXACT];
/**
 * For a Card whose w or h changes over frames, in tilt mode: `pose={tilt ? morphPose(pose) : pose}`. Chrome lays every
 * box out on whole pixels, so a card's offset (−w/2, −h/2) and the far edges of its face, layers and pages each round on
 * their own, and the card stepped a pixel at a time and could step back near rest. Marked, the Card lays itself out at
 * whole even sizes and scales the face, the layers and the pages to the true size, with the content scaled back: every
 * edge to Skia's quarter pixel, the content to a few hundredths. At even sizes the scale is 1 and nothing changes.
 * (A first version that only moved the pose by its offset's rounding left the far edges sawtoothing a pixel: don't.)
 */
export const morphPose = (pose: Pose): Pose => ({...pose, [EXACT]: true}) as Pose;

/** A card's layout: whole even sizes W × H and the scale (sx, sy) to its true size w × h when exact, else w × h at 1. */
export function exactLayout(w: number, h: number, exact: boolean): {W: number; H: number; sx: number; sy: number; sized: boolean} {
  const W = exact ? Math.max(2, 2 * Math.round(w / 2)) : w;
  const H = exact ? Math.max(2, 2 * Math.round(h / 2)) : h;
  const sx = w / W;
  const sy = h / H;
  return {W, H, sx, sy, sized: exact && (Math.abs(sx - 1) > 1e-9 || Math.abs(sy - 1) > 1e-9)};
}

/** An exact card's content: laid out at w × h and scaled back by the face's scale, so it's drawn at 1 on its true place. */
function ExactContent({sized, w, h, sx, sy, children}: {sized: boolean; w: number; h: number; sx: number; sy: number; children?: React.ReactNode}) {
  return (
    <div style={{position: 'absolute', left: 0, top: 0, width: w, height: h, transformOrigin: '0 0', transform: sized ? `scale(${(1 / sx).toFixed(6)}, ${(1 / sy).toFixed(6)})` : undefined}}>
      {children}
    </div>
  );
}

/** A face's content in tilt mode: the exact layout's content box when the card is exact, the face's tilt unless off. */
function TiltFace({exact, sized, w, h, sx, sy, scale, faceTilt, children}: {exact: boolean; sized: boolean; w: number; h: number; sx: number; sy: number; scale: number; faceTilt: boolean; children?: React.ReactNode}) {
  const inner = (
    <RasterScaleProvider value={scale}>
      {faceTilt ? (
        <TypeTilt face style={{position: 'absolute', left: 0, top: 0, width: w, height: h}}>
          {children}
        </TypeTilt>
      ) : (
        children
      )}
    </RasterScaleProvider>
  );
  return exact ? <ExactContent sized={sized} w={w} h={h} sx={sx} sy={sy}>{inner}</ExactContent> : inner;
}

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
  /**
   * A back face, so the card can turn past 90° and show a face rather than a missing edge: `true` (the card's own
   * face colour), a colour, or {face, children}. It sits at the far side of the edge slices, turned to face the other
   * way, and each face hides its own back (L-kit-052).
   */
  back?: true | string | {face?: string; children?: React.ReactNode};
  /**
   * Tilt mode: draw the whole face under the type tilt (the default). The tilt moves everything on the face by up to
   * 0.0006 × its distance from the face's centre, so on a wide card a thin line or bar far from the centre sits off
   * whole pixels at rest and its edges soften: give such a card `faceTilt={false}` and a TiltSpan around each text.
   */
  faceTilt?: boolean;
  children?: React.ReactNode;
};

/**
 * A card: paper with thickness in a papery look, a flat surface otherwise. Children are laid out in card px.
 *
 * In tilt mode (RasterModeProvider) it's drawn afresh every frame (no raster hint, no keys: `atRest` is ignored), its
 * face's type is tilted (faceTilt), and a pose marked by morphPose lays it out at whole even sizes (see morphPose).
 */
export function Card({w, h, pose, dark = false, radius, layers, face, shadow, pages = [], border, atRest = true, persp, back, faceTilt = true, children}: CardProps) {
  const look = useLook();
  const {short, long} = useFrameSize();
  const pal = usePaper(dark);
  const tilt = useRasterMode() === 'tilt';
  const outer = useRasterScale();
  const r = radius ?? look.card.radius * short;
  const n = layers ?? look.card.layers;
  const gap = Math.max(look.card.layerGap * short, 0.5);
  const hairline = border ?? look.card.border !== 'none';
  const sh = shadow ?? cardShadow(look.card.shadow, pal.shadowRgb, short, dark);
  const p = persp ?? look.camera.perspective * long;
  if (pose.op <= 0.001) return null;
  // exact (tilt mode, from morphPose): laid out at whole even sizes W × H, every box scaled to the true size w × h and
  // the content scaled back; otherwise W × H is w × h and nothing is scaled
  const exact = tilt && isExact(pose);
  const {W, H, sx, sy, sized} = exactLayout(w, h, exact);
  const fit = sized ? ` scale(${sx.toFixed(6)}, ${sy.toFixed(6)})` : '';
  // the radius drawn after the scale is the true radius
  const box: React.CSSProperties = {position: 'absolute', left: 0, top: 0, width: W, height: H, borderRadius: sized ? `${(r / sx).toFixed(4)}px / ${(r / sy).toFixed(4)}px` : r};
  const leaf: React.CSSProperties = {opacity: pose.op < 0.999 ? pose.op : undefined, filter: pose.blur > 0.2 ? `blur(${pose.blur.toFixed(2)}px)` : undefined};
  const bw = Math.max(1, 0.0014 * short);
  // the face's on-screen scale, which sizes its type's tilt
  const faceScale = outer * Math.abs(pose.s) * depthScale(pose.z, p);
  const content = (kids: React.ReactNode) =>
    tilt ? (
      <TiltFace exact={exact} sized={sized} w={w} h={h} sx={sx} sy={sy} scale={faceScale} faceTilt={faceTilt}>
        {kids}
      </TiltFace>
    ) : (
      kids
    );
  return (
    <div
      // hint mode: rebuilt at its own scale each ~3% and as it tilts, hinted while it moves, fresh at rest; tilt mode:
      // none of that, it's drawn afresh every frame
      key={tilt ? undefined : cardKey(pose, p) + (atRest ? '' : 'm')}
      style={{
        ...box,
        left: -W / 2,
        top: -H / 2,
        transformStyle: 'preserve-3d',
        transform: `translate3d(${pose.x}px, ${pose.y}px, ${pose.z}px) rotateX(${pose.rx}deg) rotateY(${pose.ry}deg) rotateZ(${pose.rz}deg) scale(${pose.s})`,
        ...(tilt ? {} : motionHint(!atRest)),
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
              transform: `translate3d(${pg.dx}px, ${pg.dy}px, ${pg.dz}px) rotateZ(${pg.rz}deg)${fit}`,
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
            transform: `translateZ(${-(i + 1) * gap}px)${fit}`,
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
          backfaceVisibility: back ? 'hidden' : undefined,
          transform: sized ? fit.trim() : undefined,
        }}
      >
        {content(children)}
      </div>
      {back && (
        <div
          style={{
            ...box,
            ...leaf,
            overflow: 'hidden',
            background: (typeof back === 'string' ? back : typeof back === 'object' ? back.face : undefined) ?? face ?? pal.card,
            boxShadow: hairline ? `inset 0 0 0 ${bw}px ${pal.cardBorder}` : undefined,
            transform: `translateZ(${-(n + 1) * gap}px) rotateY(180deg)${fit}`,
            backfaceVisibility: 'hidden',
          }}
        >
          {typeof back === 'object' && back.children !== undefined ? content(back.children) : null}
        </div>
      )}
    </div>
  );
}

/**
 * A card that fades as one sheet: while translucent it is drawn flat inside one wrapper that carries the opacity (so
 * its thickness never shows through its face); once opaque it is the full card again.
 */
export function FadeCard(props: CardProps) {
  const {pose} = props;
  const look = useLook();
  const {long} = useFrameSize();
  const outer = useRasterScale();
  if (pose.op <= 0.001) return null;
  if (pose.op >= 0.999) return <Card {...props} />;
  // the card inside is drawn at scale 1 in a wrapper that carries the pose, so it's told its true on-screen scale (its
  // type's tilt in tilt mode is sized for it); nothing in the page changes
  const p = props.persp ?? look.camera.perspective * long;
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
      <RasterScaleProvider value={outer * Math.abs(pose.s) * depthScale(pose.z, p)}>
        <Card {...props} pose={{...pose, x: 0, y: 0, z: 0, rx: 0, ry: 0, rz: 0, s: 1, op: 1}} />
      </RasterScaleProvider>
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
