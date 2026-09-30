// A brand's motif mark, drawn from the rig (core/motif.ts), and the mark as a character: a disc with the motif in it,
// moved by gestures that each carry one meaning. Drawing follows the rig's rules: one flat SVG in the layer the mark is
// placed in, the two groups as two sheets in a fixed paint order (so a turn never pops a layer), opacity on a whole
// sheet or the whole mark and never on one part, 2D only (tilt the layer to tilt the mark), and nothing that clips it.
import {brandPack} from './core/brand';
import {alpha} from './core/color';
import {frameAt} from './core/film';
import {motifPose, motifSpec, placeParts, type MotifGesture, type MotifLayout, type MotifSpec, type MotifTrack, type PartShape} from './core/motif';
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
};

/** The motif mark: its parts on the ring, moved by the track. At rest it is exactly the brand's mark. */
export function MotifMark({track = [], x = 0, y = 0, size = 100, layout, t: tOverride, color, color2, spec: given, opacity}: MotifMarkProps) {
  const clock = useFilmTime();
  const tk = useTokens();
  const {short} = useFrameSize();
  const t = tOverride ?? clock;
  const frame = frameAt(t);
  const spec = given ?? motifSpec().spec;
  const layoutAt = layout ?? (() => ({x, y, size}));
  const L = layoutAt(frame);
  const pose = motifPose(track, frame, {spec, layout: layoutAt, short});
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
 * every gesture the meaning uses. The hello's entrance lands before its breath (the parts arrive, then she grows).
 */
export function gesture(meaning: string, opts: Record<string, unknown> & {t?: number; frame?: number}): MotifGesture[] {
  const brandMeanings = ((brandPack().motif as {gestures?: Record<string, string[]>} | null | undefined)?.gestures ?? {}) as Record<string, string[]>;
  const kinds = (brandMeanings[meaning] ?? MEANINGS[meaning] ?? []) as MotifGesture['kind'][];
  return kinds.map((kind, i) => {
    const g = {...opts, kind} as MotifGesture;
    // a two-step meaning spreads its steps: the second lands 12 frames after the first
    if (i > 0) {
      if (typeof g.frame === 'number') (g as {frame: number}).frame += 12 * i;
      else if (typeof g.t === 'number') (g as {frame?: number}).frame = Math.round(frameAt(g.t)) + 12 * i;
    }
    return g;
  });
}

export type MarkCharacterProps = MotifMarkProps & {
  /** the disc, 0 → 1: a circle growing from the centre (0 draws the mark alone) */
  disc?: number;
  discColor?: string;
  markColor?: string;
  /** the disc's soft shadow (0 for a flat avatar) */
  shadow?: number;
};

/**
 * A brand's mark as a character (character-led films): its disc, and the motif inside it at the brand's ratio, moved
 * by gestures. It never moves for decoration: between gestures it is exactly the brand's avatar.
 */
export function MarkCharacter({disc = 1, discColor, markColor, shadow = 1, ...mark}: MarkCharacterProps) {
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
  const markLayout = (f: number) => {
    const l = layoutAt(f);
    return {x: l.x, y: l.y, size: l.size * spec.markRatio};
  };
  return (
    <div style={{position: 'absolute', left: 0, top: 0, width: 0, height: 0}}>
      {r > 0.01 && (
        <div
          style={{
            position: 'absolute',
            left: L.x - r,
            top: L.y - r,
            width: 2 * r,
            height: 2 * r,
            borderRadius: '50%',
            background: dc,
            boxShadow: shadow > 0.001 && disc > 0.8 ? `0 ${(L.size * 0.1).toFixed(2)}px ${(L.size * 0.28).toFixed(2)}px -${(L.size * 0.12).toFixed(2)}px ${alpha(tk.color.inverseDeep, 0.5 * shadow * ((disc - 0.8) / 0.2) ** 2)}` : undefined,
          }}
        />
      )}
      <MotifMark {...mark} spec={spec} layout={markLayout} t={t} color={mc} />
    </div>
  );
}
