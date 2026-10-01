// A brand's motif mark, drawn from the rig (core/motif.ts), and the mark as a character: a disc with the motif in it,
// moved by gestures that each carry one meaning. Drawing follows the rig's rules: one flat SVG in the layer the mark is
// placed in, the two groups as two sheets in a fixed paint order (so a turn never pops a layer), opacity on a whole
// sheet or the whole mark and never on one part, 2D only (tilt the layer to tilt the mark), and nothing that clips it.
import type React from 'react';
import {brandPack} from './core/brand';
import {alpha} from './core/color';
import {frameAt} from './core/film';
import {gestureSpans, motifPose, motifSpec, placeParts, STILL_FRAMES, type MotifGesture, type MotifLayout, type MotifSpec, type MotifTrack, type PartShape} from './core/motif';
import {useFilmTime, useFrameSize, useTokens} from './context';

function PartPath({shape, corner, x, y, size, rot, fill}: {shape: PartShape; corner: number; x: number; y: number; size: number; rot: number; fill: string}) {
  const f = (n: number) => n.toFixed(3);
  const tf = `translate(${f(x)} ${f(y)}) rotate(${f(rot)})`;
  if (typeof shape === 'object') {
    const [vx, vy, vw, vh] = shape.viewBox;
    const s = size / Math.max(vw, vh);
    return <path d={shape.path} transform={`${tf} scale(${f(s)}) translate(${f(-vx - vw / 2)} ${f(-vy - vh / 2)})`} fill={fill} />;
  }
  switch (shape) {
    case 'circle':
      return <circle cx={f(x)} cy={f(y)} r={f(size / 2)} fill={fill} />;
    case 'pill':
      return <rect x={f(-size / 2)} y={f(-size / 4)} width={f(size)} height={f(size / 2)} rx={f(size / 4)} transform={tf} fill={fill} />;
    case 'diamond':
    case 'square':
      return <rect x={f(-size / 2)} y={f(-size / 2)} width={f(size)} height={f(size)} transform={tf} fill={fill} />;
    default:
      return <rect x={f(-size / 2)} y={f(-size / 2)} width={f(size)} height={f(size)} rx={f(corner * size)} transform={tf} fill={fill} />;
  }
}

export type MotifMarkProps = {
  track?: MotifTrack;
  /** centre and diameter of the mark (px, in the layer it's drawn in), or a layout at any film frame */
  x?: number;
  y?: number;
  size?: number;
  layout?: (frame: number) => MotifLayout;
  /** film time override */
  t?: number;
  color?: string;
  /** a second colour for the upper group (defaults to the same) */
  color2?: string;
  spec?: MotifSpec;
  opacity?: number;
  /**
   * The layout the gestures are worked out at (which regime a gesture runs in, where a lean points), when the mark is
   * drawn staged at another (MarkCharacter's markScale and markOffset): default, its own layout. Staging never changes
   * a gesture.
   */
  poseLayout?: (frame: number) => MotifLayout;
};

/** The motif mark: its parts on the ring, moved by the track. At rest it is exactly the brand's mark. */
export function MotifMark({track = [], x = 0, y = 0, size = 100, layout, t: tOverride, color, color2, spec: given, opacity, poseLayout}: MotifMarkProps) {
  const clock = useFilmTime();
  const tk = useTokens();
  const {short} = useFrameSize();
  const t = tOverride ?? clock;
  const frame = frameAt(t);
  const spec = given ?? motifSpec().spec;
  const layoutAt = layout ?? (() => ({x, y, size}));
  const L = layoutAt(frame);
  const pose = motifPose(track, frame, {spec, layout: poseLayout ?? layoutAt, short});
  const parts = placeParts(spec, pose, L);
  const c0 = color ?? tk.color.accent;
  const c1 = color2 ?? c0;
  const sheets = [0, 1] as const;
  const D = L.size * pose.scale;
  const cx = L.x + pose.ox * L.size;
  const cy = L.y + pose.oy * L.size;
  return (
    <svg width={1} height={1} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', opacity: opacity !== undefined && opacity < 0.999 ? opacity : undefined}}>
      {pose.ripples.map((r, i) => (
        <circle key={`r${i}`} cx={cx} cy={cy} r={r.r * D} fill="none" stroke={c0} strokeWidth={Math.max(1, 0.0015 * short)} opacity={r.op} />
      ))}
      {sheets.map((s) => {
        const mine = parts.filter((p) => p.group === s);
        if (!mine.length) return null;
        const op = mine[0].op;
        if (op <= 0.001) return null;
        return (
          <g key={s} opacity={op < 0.999 ? op : undefined}>
            {mine.map((p) => (
              <PartPath key={p.i} shape={spec.shape} corner={spec.corner} x={p.x} y={p.y} size={p.size} rot={p.rot} fill={s === 0 ? c0 : c1} />
            ))}
          </g>
        );
      })}
    </svg>
  );
}

// ------------------------------------------------------------------------------------------------- the character
/**
 * The gesture vocabulary: one meaning each, and what the rig does for it. A brand can give its own
 * (brand.motif.gestures: {meaning: [gesture kinds]}); these are the rig's defaults.
 */
export const MEANINGS: Record<string, MotifGesture['kind'][]> = {
  hello: ['enter', 'grow'],
  yes: ['turn'],
  attention: ['lean'],
  thinking: ['breathe'],
  answering: ['pulse'],
  delight: ['wave'],
  care: ['hold'],
  landing: ['ripple'],
  home: ['home'],
  goodbye: ['turn'],
};

/**
 * Track entries for a meaning at a contact (film seconds): gesture('thinking', {t: …, cycles: 2}). Extra options go to
 * every gesture the meaning uses. The first step lands on the contact; each step after it starts once the one before
 * has ended and 4 still frames have passed (the rig's combination rules), so a meaning's own steps never crowd each
 * other: the hello's entrance lands, then the mark grows.
 */
export function gesture(meaning: string, opts: Record<string, unknown> & {t?: number; frame?: number}): MotifGesture[] {
  const brandMeanings = ((brandPack().motif as {gestures?: Record<string, string[]>} | null | undefined)?.gestures ?? {}) as Record<string, string[]>;
  const kinds = (brandMeanings[meaning] ?? MEANINGS[meaning] ?? []) as MotifGesture['kind'][];
  const out: MotifGesture[] = [];
  for (const kind of kinds) {
    const g = {...opts, kind} as MotifGesture;
    const prev = out[out.length - 1];
    if (prev) {
      // spans at the full regime are the longest, so the spacing holds at every size
      const [p] = gestureSpans([prev]);
      const [mine] = gestureSpans([{...g, frame: 0, t: undefined} as MotifGesture]);
      const frame = p.end + STILL_FRAMES - mine.start;
      delete (g as {t?: number}).t;
      (g as {frame: number}).frame = frame;
    }
    out.push(g);
  }
  return out;
}

export type MarkCharacterProps = MotifMarkProps & {
  /** the disc, 0 → 1: a circle growing from the centre (0 draws the mark alone) */
  disc?: number;
  discColor?: string;
  markColor?: string;
  /** the disc's soft shadow (0 for a flat avatar) */
  shadow?: number;
  /**
   * Staging for a close-up, separate from gestures: the mark's scale inside its disc (1 is the brand's ratio). A builder
   * brings it back to 1 on the rig's curve as the camera pulls back; the gestures don't change with it (L-kit-D075).
   */
  markScale?: number;
  /** Staging: the mark's offset from the disc's centre, in disc diameters. Back to 0 on the rig's curve, like markScale. */
  markOffset?: {x?: number; y?: number};
  /**
   * Place and size the disc with a transform (a fixed-size circle translated and scaled), the default, so it sits under
   * the mark's parts on every frame. Laid out with left, top and width it snapped to whole pixels while the parts, drawn
   * in floats, didn't (L-kit-S013). false draws it the old way.
   */
  subpixel?: boolean;
};

/** The disc's own size before its transform: a fixed circle, scaled to the disc's size on every frame. */
export const DISC_BASE = 100;

/** The disc's style: a fixed DISC_BASE circle placed and sized by its transform (subpixel), or laid out in px. */
export function discStyle(cx: number, cy: number, r: number, subpixel = true): React.CSSProperties {
  if (!subpixel) return {position: 'absolute', left: cx - r, top: cy - r, width: 2 * r, height: 2 * r, borderRadius: '50%'};
  const k = (2 * r) / DISC_BASE;
  return {
    position: 'absolute',
    left: 0,
    top: 0,
    width: DISC_BASE,
    height: DISC_BASE,
    borderRadius: '50%',
    transformOrigin: '0 0',
    transform: `translate(${(cx - r).toFixed(4)}px, ${(cy - r).toFixed(4)}px) scale(${k.toFixed(6)})`,
  };
}

/**
 * A brand's mark as a character (character-led films): its disc, and the motif inside it at the brand's ratio, moved
 * by gestures. It never moves for decoration: between gestures it is exactly the brand's avatar.
 */
export function MarkCharacter({disc = 1, discColor, markColor, shadow = 1, markScale = 1, markOffset, subpixel = true, ...mark}: MarkCharacterProps) {
  const tk = useTokens();
  const clock = useFilmTime();
  const t = mark.t ?? clock;
  const frame = frameAt(t);
  const spec = mark.spec ?? motifSpec().spec;
  const layoutAt = mark.layout ?? (() => ({x: mark.x ?? 0, y: mark.y ?? 0, size: mark.size ?? 100}));
  const L = layoutAt(frame);
  const r = (L.size / 2) * Math.max(0, Math.min(1, disc));
  const dc = discColor ?? tk.color.accent;
  const mc = markColor ?? tk.color.onAccent;
  const ox = markOffset?.x ?? 0;
  const oy = markOffset?.y ?? 0;
  // the brand's geometry, where the gestures are worked out, and the staged one, where the mark is drawn
  const markLayout = (f: number) => {
    const l = layoutAt(f);
    return {x: l.x, y: l.y, size: l.size * spec.markRatio};
  };
  const staged = markScale === 1 && ox === 0 && oy === 0;
  const stagedLayout = staged
    ? markLayout
    : (f: number) => {
        const l = layoutAt(f);
        return {x: l.x + ox * l.size, y: l.y + oy * l.size, size: l.size * spec.markRatio * markScale};
      };
  // the shadow is given in the disc's own units, so it renders the same size whatever the disc's transform
  const k = subpixel ? Math.max((2 * r) / DISC_BASE, 1e-6) : 1;
  const px = (v: number) => (v / k).toFixed(subpixel ? 4 : 2);
  const shadowCss = shadow > 0.001 && disc > 0.8 ? `0 ${px(L.size * 0.1)}px ${px(L.size * 0.28)}px -${px(L.size * 0.12)}px ${alpha(tk.color.inverseDeep, 0.5 * shadow * ((disc - 0.8) / 0.2) ** 2)}` : undefined;
  return (
    <div style={{position: 'absolute', left: 0, top: 0, width: 0, height: 0}}>
      {r > 0.01 && <div style={{...discStyle(L.x, L.y, r, subpixel), background: dc, boxShadow: shadowCss}} />}
      <MotifMark {...mark} spec={spec} layout={stagedLayout} poseLayout={staged ? undefined : markLayout} t={t} color={mc} />
    </div>
  );
}
