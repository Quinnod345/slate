// Charts at film scale: a distribution curve, bars, a line, a radar and a ring. Every number comes from props (real
// values only: a chart in a film is a claim), every colour from the brand's tokens, and every draw is a 0 → 1 the
// scene drives on the brand's eases: a stroke that draws on, bars that grow, a curve that rises from its axis.
//
// Rules the films learned, kept as defaults:
// - the accent line is the look's line weight (useLine), so an act's own line matches the chart it hands to;
// - a drawn-on stroke isn't drawn at all while its progress is 0: a zero-length dash with round caps still paints a dot;
// - a dot or marker on a drawing line grows in over the tenth of the draw before the line reaches it, never popping on
//   at its threshold;
// - an axis's labels each land on their own word (`op` per label) and read at body size.
import React, {useId} from 'react';
import {alpha, mix} from './core/color';
import {clamp01, lerp} from './core/spline';
import {useAppPx, useLine, useTokens, useType} from './context';

/**
 * How grown a marker on a drawing line is (0 → 1) when the line has drawn `drawn` of its length and reaches the marker
 * at `at` (both shares of the line's length): it grows in over the `span` of the draw before the line reaches it (over
 * the first `span` for a marker the line starts at), so it's absent until then and whole when the line arrives. A
 * marker that switched on at its threshold popped at 98% (L-kit-D143). Scale its radius and opacity by this.
 */
export function dotGrowth(drawn: number, at: number, span = 0.1): number {
  const end = Math.max(at, span);
  // whole exactly when the line arrives (no float shortfall), absent until its tenth begins
  if (drawn >= end - 1e-9) return 1;
  return clamp01((drawn - (end - span)) / span);
}

/** Where a line through `pts` reaches each point, as a share of its length (a dash runs along a path's length, not x). */
export function lineArrivals(pts: readonly (readonly [number, number])[]): number[] {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  const total = cum[cum.length - 1];
  return cum.map((c, i) => (total > 0 ? c / total : pts.length > 1 ? i / (pts.length - 1) : 1));
}

// -------------------------------------------------------------------------------------------------- distributions
/** Error function (Abramowitz and Stegun 7.1.26, |error| < 1.5e-7). */
export function erf(x: number): number {
  const s = Math.sign(x);
  const a = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * a);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-a * a);
  return s * y;
}

export type Normal = {mean: number; sd: number; min: number; max: number};

/** The normal curve's height at x, scaled so its peak is 1. */
export const normalShape = (x: number, d: Normal): number => Math.exp(-0.5 * ((x - d.mean) / d.sd) ** 2);
/** The share of the distribution below x. */
export const normalCdf = (x: number, d: Normal): number => 0.5 * (1 + erf((x - d.mean) / (d.sd * Math.SQRT2)));
/** Where x sits along an axis w wide from d.min to d.max. */
export const axisX = (x: number, d: Normal, w: number): number => ((x - d.min) / (d.max - d.min)) * w;

/** Points of the curve over [min, max] in a w × h box, baseline at y = h. */
export function curvePoints(d: Normal, w: number, h: number, n = 181): [number, number][] {
  return Array.from({length: n}, (_, i) => {
    const x = d.min + ((d.max - d.min) * i) / (n - 1);
    return [axisX(x, d, w), h - normalShape(x, d) * h] as [number, number];
  });
}

export const pathOf = (pts: readonly (readonly [number, number])[]): string => pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`).join('');

/**
 * A distribution curve. `drawn` 0 → 1 draws the stroke left to right, `rise` 0 → 1 lifts it from the flat axis,
 * `fill` 0 → 1 fades its area in. The line keeps the brand's accent; its weight is the look's line (useLine) unless a
 * scene gives one (heavy while drawing, a hairline once it settles, if the brand's app draws it that way).
 */
export function DistributionCurve({dist, w, h, drawn = 1, rise = 1, weight, fill = 1, color}: {dist: Normal; w: number; h: number; drawn?: number; rise?: number; weight?: number; fill?: number; color?: string}) {
  const t = useTokens();
  const line = useLine();
  const id = `dist${useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  const c = color ?? t.color.line;
  const sw = weight ?? line;
  const pts = curvePoints(dist, w, h).map(([x, y]) => [x, lerp(h, y, clamp01(rise))] as [number, number]);
  const d = pathOf(pts);
  return (
    <svg width={w} height={h} style={{overflow: 'visible', display: 'block'}}>
      <defs>
        <linearGradient id={`${id}-f`} x1="0" x2="0" y1="0" y2={h} gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={c} stopOpacity={0.22} />
          <stop offset="1" stopColor={t.color.softAccent} stopOpacity={0.35} />
        </linearGradient>
        <clipPath id={`${id}-c`}>
          <rect x={-sw} y={-h} width={(w + 2 * sw) * clamp01(drawn)} height={3 * h} />
        </clipPath>
      </defs>
      {fill > 0 && clamp01(drawn) > 0 && <path d={`${d}L${w},${h}L0,${h}Z`} fill={`url(#${id}-f)`} opacity={clamp01(fill)} clipPath={`url(#${id}-c)`} />}
      {/* nothing at zero length: a zero-length dash with round caps still paints a dot (L-kit-D133) */}
      {clamp01(drawn) > 0 && <path d={d} fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={`${clamp01(drawn)} 2`} />}
    </svg>
  );
}

/** A marker on a curve: a dot on the line, a dotted drop to the axis, and a label above. */
export function CurveMarker({dist, w, h, value, label, op = 1}: {dist: Normal; w: number; h: number; value: number; label?: React.ReactNode; op?: number}) {
  const t = useTokens();
  const a = useAppPx();
  const s = useType('label', t.color.onInverse);
  const x = axisX(value, dist, w);
  const y = h - normalShape(value, dist) * h;
  return (
    <div style={{position: 'absolute', left: x, top: 0, opacity: op}}>
      <div style={{position: 'absolute', left: -a(0.75), top: y, width: a(1.5), height: h - y, backgroundImage: `linear-gradient(${t.color.line} 50%, ${alpha(t.color.line, 0)} 0)`, backgroundSize: `${a(1.5)}px ${a(4)}px`}} />
      <div style={{position: 'absolute', left: -a(4.5), top: y - a(4.5), width: a(9), height: a(9), borderRadius: a(9), background: t.color.surface, boxShadow: `inset 0 0 0 ${a(2)}px ${t.color.line}`}} />
      {label !== undefined && (
        <div style={{...s, position: 'absolute', left: 0, top: y - a(38), transform: 'translateX(-50%)', background: t.color.inverse, borderRadius: a(6), padding: `${a(4)}px ${a(9)}px`, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap'}}>{label}</div>
      )}
    </div>
  );
}

/**
 * An axis under a chart: a hairline and labels at given positions (fractions of the width). Each label can land on its
 * own word: `op` (0 → 1) per label, so a scene stages them one by one. Labels are set in the body role by default,
 * which reads at 28 px or more on 1080 lines; an axis that drew every label at once at 22 to 24 px made a film redraw
 * it by hand (L-kit-D103).
 */
export function Axis({w, labels = [], op = 1, role = 'body'}: {w: number; labels?: {at: number; text: string; op?: number}[]; op?: number; role?: string}) {
  const t = useTokens();
  const a = useAppPx();
  const s = useType(role, t.color.ink3);
  return (
    <div style={{position: 'relative', width: w, height: a(8) + s.fontSize * s.lineHeight, opacity: op < 1 ? clamp01(op) : undefined}}>
      <div style={{position: 'absolute', left: 0, top: 0, width: w, height: a(1), background: t.color.hairline}} />
      {labels.map((l, i) => {
        const lo = clamp01(l.op ?? 1);
        if (lo <= 0) return null;
        return (
          <div key={i} style={{...s, position: 'absolute', left: l.at * w, top: a(8), transform: l.at <= 0 ? undefined : l.at >= 1 ? 'translateX(-100%)' : 'translateX(-50%)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', opacity: lo < 1 ? lo : undefined}}>
            {l.text}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------- bars
/** Bars that grow from the baseline; `grow` is 0 → 1 per bar (the scene staggers it along the data's direction). */
export function BarChart({values, w, h, max, grow = 1, gap = 0.28, color, highlight}: {values: number[]; w: number; h: number; max?: number; grow?: number | number[]; gap?: number; color?: string; highlight?: number}) {
  const t = useTokens();
  const a = useAppPx();
  const n = Math.max(1, values.length);
  const top = max ?? Math.max(...values, 1e-9);
  const bw = w / (n + (n - 1) * gap);
  return (
    <svg width={w} height={h} style={{display: 'block', overflow: 'visible'}}>
      <line x1={0} x2={w} y1={h} y2={h} stroke={t.color.hairline} strokeWidth={a(1)} />
      {values.map((v, i) => {
        const g = clamp01(Array.isArray(grow) ? grow[i] ?? 0 : grow);
        const bh = (Math.max(0, v) / top) * h * g;
        const x = i * bw * (1 + gap);
        return <rect key={i} x={x} y={h - bh} width={bw} height={bh} rx={Math.min(a(3), bw / 2)} fill={i === highlight ? t.color.accent : color ?? mix(t.color.accent, t.color.surface, 0.45)} />;
      })}
    </svg>
  );
}


// ---------------------------------------------------------------------------------------------------------- line
/**
 * A line through values (evenly spaced), drawn left to right with `drawn` 0 → 1, in the look's line weight. With `dots`
 * each point gets a dot that grows in (radius and opacity from 0) over the tenth of the draw before the line reaches it
 * (dotGrowth), so none pops on at its threshold; the last one is the marker at the line's end.
 */
export function LineChart({values, w, h, min, max, drawn = 1, weight, color, dots = false}: {values: number[]; w: number; h: number; min?: number; max?: number; drawn?: number; weight?: number; color?: string; dots?: boolean}) {
  const t = useTokens();
  const a = useAppPx();
  const line = useLine();
  const lo = min ?? Math.min(...values);
  const hi = max ?? Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => [values.length > 1 ? (i / (values.length - 1)) * w : w / 2, h - ((v - lo) / span) * h] as [number, number]);
  const c = color ?? t.color.line;
  const d = clamp01(drawn);
  const sw = weight ?? line;
  const reach = lineArrivals(pts);
  return (
    <svg width={w} height={h} style={{display: 'block', overflow: 'visible'}}>
      {/* nothing at zero length: a zero-length dash with round caps still paints a dot (L-kit-D133) */}
      {d > 0 && <path d={pathOf(pts)} fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={`${d} 2`} />}
      {dots &&
        pts.map(([x, y], i) => {
          const g = dotGrowth(d, reach[i]);
          if (g <= 0) return null;
          return <circle key={i} cx={x} cy={y} r={a(3) * g} fill={t.color.surface} stroke={c} strokeWidth={sw * g} opacity={g < 1 ? g : undefined} />;
        })}
    </svg>
  );
}

// ---------------------------------------------------------------------------------------------------------- radar
/** The point on spoke i of n at radius r (0 at the centre), clockwise from twelve o'clock. */
export function spokePoint(i: number, n: number, r: number, cx: number, cy: number): [number, number] {
  const a = (2 * Math.PI * i) / n;
  return [cx + r * Math.sin(a), cy - r * Math.cos(a)];
}

/** A radar: spokes with labels, rings, and series (values 0 → 1 per spoke) that grow out with `grow` 0 → 1. */
export function RadarChart({size, spokes, series, grow = 1, rings = 4, colors}: {size: number; spokes: string[]; series: number[][]; grow?: number; rings?: number; colors?: string[]}) {
  const t = useTokens();
  const a = useAppPx();
  const s = useType('small', t.color.ink3);
  const n = Math.max(3, spokes.length);
  const r = size / 2;
  const g = clamp01(grow);
  const palette = colors ?? [t.color.accent, t.color.ink3];
  return (
    <div style={{position: 'relative', width: size, height: size}}>
      <svg width={size} height={size} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
        {Array.from({length: rings}, (_, k) => {
          const rr = (r * (k + 1)) / rings;
          return <polygon key={k} points={Array.from({length: n}, (_, i) => spokePoint(i, n, rr, r, r).join(',')).join(' ')} fill="none" stroke={t.color.hairline} strokeWidth={a(1)} />;
        })}
        {Array.from({length: n}, (_, i) => {
          const [x, y] = spokePoint(i, n, r, r, r);
          return <line key={i} x1={r} y1={r} x2={x} y2={y} stroke={t.color.hairline} strokeWidth={a(1)} />;
        })}
        {series.map((vals, si) => (
          <polygon key={si} points={Array.from({length: n}, (_, i) => spokePoint(i, n, r * clamp01(vals[i] ?? 0) * g, r, r).join(',')).join(' ')} fill={alpha(palette[si % palette.length], 0.16)} stroke={palette[si % palette.length]} strokeWidth={a(1.5)} strokeLinejoin="round" />
        ))}
      </svg>
      {spokes.map((label, i) => {
        // labels sit just outside the rim and scale down with a small radar, so they never outweigh it
        const [x, y] = spokePoint(i, n, r + Math.min(a(16), size * 0.12), r, r);
        return (
          <div key={i} style={{...s, fontSize: Math.min(s.fontSize, Math.max(6, size * 0.13)), position: 'absolute', left: x, top: y, transform: 'translate(-50%, -50%)', whiteSpace: 'nowrap'}}>
            {label}
          </div>
        );
      })}
    </div>
  );
}

// ----------------------------------------------------------------------------------------------------------- ring
/** A progress ring: `value` 0 → 1 of the way round, drawn from twelve o'clock, on a hairline track. */
export function RingChart({size, value, weight, color, track = true, children}: {size: number; value: number; weight?: number; color?: string; track?: boolean; children?: React.ReactNode}) {
  const t = useTokens();
  const a = useAppPx();
  const sw = weight ?? a(5);
  const r = (size - sw) / 2;
  return (
    <div style={{position: 'relative', width: size, height: size}}>
      <svg width={size} height={size} style={{position: 'absolute', left: 0, top: 0, transform: 'rotate(-90deg)'}}>
        {track && <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={t.color.gray[1] ?? t.color.hairline} strokeWidth={sw} />}
        {clamp01(value) > 0 && <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color ?? t.color.accent} strokeWidth={sw} strokeLinecap="round" pathLength={1} strokeDasharray={`${clamp01(value)} 2`} />}
      </svg>
      <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>{children}</div>
    </div>
  );
}
