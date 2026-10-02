// App UI primitives at film scale: surfaces, controls, chips, status marks, progress, avatars, a streamed answer.
// Authored in app pixels (useAppPx: an app pixel at film scale, which follows the frame) and coloured from the brand's
// tokens, so a brand's product can be shown as its own app draws it. They carry no copy: every label is a prop. Build a
// screen from these with the product's exact words; never draw UI the product doesn't have.
import React from 'react';
import {alpha, mix, parseColor} from './core/color';
import {clamp01} from './core/spline';
import type {ToneName} from './core/tokens';
import {useAppPx, useTokens, useType} from './context';
import {inMotion, TypeTilt, useMotionHint, useRasterMode, useTypeTilt} from './paper';

/** A surface: the brand's card colour, a hairline, its radius. Put it in a Card for paper thickness. */
export function Surface({w, h, pad = 24, style, children}: {w: number | string; h?: number | string; pad?: number; style?: React.CSSProperties; children?: React.ReactNode}) {
  const a = useAppPx();
  const t = useTokens();
  return <div style={{width: w, height: h, padding: a(pad), boxSizing: 'border-box', background: t.color.surface, borderRadius: a(12), boxShadow: `inset 0 0 0 ${a(1)}px ${t.color.hairline}`, ...style}}>{children}</div>;
}

export function Eyebrow({children, color, style}: {children?: React.ReactNode; color?: string; style?: React.CSSProperties}) {
  const s = useType('eyebrow', color);
  return <div style={{...s, ...style}}>{children}</div>;
}

export function Hairline({w = '100%', vertical, style}: {w?: number | string; vertical?: boolean; style?: React.CSSProperties}) {
  const a = useAppPx();
  const t = useTokens();
  return <div style={vertical ? {width: a(1), height: w, background: t.color.hairline, ...style} : {height: a(1), width: w, background: t.color.hairline, ...style}} />;
}

// --------------------------------------------------------------------------------------------------------- icons
type IconProps = {size?: number; color?: string; stroke?: number; draw?: number};
const box = (size: number) => ({width: size, height: size, viewBox: '0 0 24 24', fill: 'none', style: {display: 'block', overflow: 'visible'} as React.CSSProperties});

function useIcon(p: IconProps, fallback: 'ink' | 'accent' | 'ink3' | 'onAccent') {
  const a = useAppPx();
  const t = useTokens();
  return {size: p.size ?? a(14), color: p.color ?? t.color[fallback], stroke: p.stroke ?? 2.2};
}

/** A check mark that draws on (`draw` 0 → 1). At 0 there's no path at all: a zero-length dash with round caps still
 * paints a dot (L-kit-D133). */
export function IconCheck(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'onAccent');
  const draw = clamp01(p.draw ?? 1);
  return <svg {...box(size)}>{draw > 0 && <path d="M5 12.5l4.5 4.5L19 7.5" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={`${draw} 2`} />}</svg>;
}
export function IconArrow(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'accent');
  return (
    <svg {...box(size)}>
      <path d="M5 12h14M13 6l6 6-6 6" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
export function IconLock(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'ink3');
  return (
    <svg {...box(size)}>
      <rect x="5" y="10.5" width="14" height="10" rx="2.5" stroke={color} strokeWidth={stroke} />
      <path d="M8 10.5V8a4 4 0 018 0v2.5" stroke={color} strokeWidth={stroke} strokeLinecap="round" />
    </svg>
  );
}
export function IconDoc(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'ink');
  return (
    <svg {...box(size)}>
      <path d="M7 3h7l5 5v12a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z" stroke={color} strokeWidth={stroke} strokeLinejoin="round" />
      <path d="M14 3v5h5" stroke={color} strokeWidth={stroke} strokeLinejoin="round" />
    </svg>
  );
}
export function IconClock(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'ink3');
  return (
    <svg {...box(size)}>
      <circle cx="12" cy="12" r="8.5" stroke={color} strokeWidth={stroke} />
      <path d="M12 7.5V12l3 2" stroke={color} strokeWidth={stroke} strokeLinecap="round" />
    </svg>
  );
}
export function IconFlag(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'accent');
  return (
    <svg {...box(size)}>
      <path d="M5 21V4M5 4h11l-2 4 2 4H5" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
export function IconPlus(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'ink');
  return (
    <svg {...box(size)}>
      <path d="M12 5v14M5 12h14" stroke={color} strokeWidth={stroke} strokeLinecap="round" />
    </svg>
  );
}
export function IconSearch(p: IconProps) {
  const {size, color, stroke} = useIcon(p, 'ink3');
  return (
    <svg {...box(size)}>
      <circle cx="11" cy="11" r="6.5" stroke={color} strokeWidth={stroke} />
      <path d="M16 16l4 4" stroke={color} strokeWidth={stroke} strokeLinecap="round" />
    </svg>
  );
}

// ------------------------------------------------------------------------------------------------------ controls
/** A button: primary (accent fill), secondary (surface with a hairline) or quiet. `enabled` 0 → 1 wakes a disabled one. */
export function Button({label, variant = 'primary', enabled = 1, press = 0, icon, trailing, w, h = 44, style}: {label: React.ReactNode; variant?: 'primary' | 'secondary' | 'quiet'; enabled?: number; press?: number; icon?: React.ReactNode; trailing?: React.ReactNode; w?: number | string; h?: number; style?: React.CSSProperties}) {
  const a = useAppPx();
  const t = useTokens();
  const s = useType('label');
  const e = clamp01(enabled);
  const primary = variant === 'primary';
  const bg = primary ? mix(t.color.gray[2] ?? t.color.hairline, t.color.accent, e) : t.color.surface;
  const color = primary ? t.color.onAccent : variant === 'quiet' ? t.color.accentInk : t.color.ink;
  return (
    <div
      style={{
        ...s,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: a(8),
        height: a(h),
        width: w,
        padding: `0 ${a(20)}px`,
        boxSizing: 'border-box',
        borderRadius: a(8),
        background: bg,
        boxShadow: primary ? `0 ${a(1 + 3 * e)}px ${a(5 + 7 * e)}px -${a(4)}px rgba(${t.color.shadow},${(0.35 * e).toFixed(3)})` : `inset 0 0 0 ${a(1)}px ${t.color.hairline}`,
        color,
        opacity: primary ? 1 : 0.4 + 0.6 * e,
        transform: `scale(${1 - 0.03 * clamp01(press)})`,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {icon}
      {label}
      {trailing}
    </div>
  );
}

/** A checkbox that ticks: `on` 0 → 1 fills the accent and draws the check (on the brand's state ease, by the caller). */
export function Checkbox({on = 0, size = 22}: {on?: number; size?: number}) {
  const a = useAppPx();
  const t = useTokens();
  const u = clamp01(on);
  return (
    <div style={{width: a(size), height: a(size), borderRadius: a(5), boxSizing: 'border-box', background: mix(t.color.surface, t.color.accent, u), boxShadow: `inset 0 0 0 ${a(1.5 * (1 - u))}px ${t.color.gray[3] ?? t.color.ink3}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none'}}>
      <IconCheck size={a(size * 0.72)} draw={clamp01((u - 0.3) / 0.7)} />
    </div>
  );
}

/** A form field: a label above, a bordered box, and a value that settles in (`fill` 0 → 1). */
export function Field({label, value, fill = 1, focus = 0, w = 320, h = 44, placeholder}: {label?: string; value?: string; fill?: number; focus?: number; w?: number; h?: number; placeholder?: string}) {
  const a = useAppPx();
  const t = useTokens();
  const labelS = useType('label', t.color.ink2);
  const body = useType('body', t.color.ink);
  const f = clamp01(fill);
  const fo = clamp01(focus);
  // tilt mode: the value's tilt goes after its own rise, and nothing is hinted (see paper.tsx, raster modes)
  const tt = useTypeTilt();
  const hint = useMotionHint(inMotion(f));
  return (
    <div style={{width: a(w)}}>
      {label && <div style={{...labelS, marginBottom: a(6)}}>{label}</div>}
      <div style={{...body, height: a(h), borderRadius: a(8), boxShadow: `inset 0 0 0 ${a(1 + fo * 0.5)}px ${mix(t.color.hairline, t.color.accent, fo)}`, background: t.color.surface, display: 'flex', alignItems: 'center', padding: `0 ${a(14)}px`, boxSizing: 'border-box', overflow: 'hidden', whiteSpace: 'nowrap'}}>
        {value && f > 0 ? <span style={{opacity: f, transform: `translateY(${((1 - f) * a(7)).toFixed(3)}px)${tt}`, display: 'inline-block', ...hint}}>{value}</span> : <span style={{color: t.color.ink3}}>{placeholder}</span>}
      </div>
    </div>
  );
}

/** A toned chip (ok, warn, risk, neutral) with its dot. */
export function Chip({label, tone = 'neutral', icon, h = 28, style}: {label: string; tone?: ToneName; icon?: React.ReactNode; h?: number; style?: React.CSSProperties}) {
  const a = useAppPx();
  const t = useTokens();
  const c = t.color.tones[tone];
  const s = useType('label', c.ink);
  return (
    <div style={{...s, display: 'inline-flex', alignItems: 'center', gap: a(6), height: a(h), padding: `0 ${a(12)}px`, borderRadius: a(h), background: c.bg, whiteSpace: 'nowrap', ...style}}>
      {icon ?? <span style={{width: a(7), height: a(7), borderRadius: a(4), background: c.dot, flex: 'none'}} />}
      {label}
    </div>
  );
}

/** An outlined pill. */
export function Pill({label, icon, h = 28, color, style}: {label: string; icon?: React.ReactNode; h?: number; color?: string; style?: React.CSSProperties}) {
  const a = useAppPx();
  const t = useTokens();
  const s = useType('label', color ?? t.color.ink);
  return (
    <div style={{...s, display: 'inline-flex', alignItems: 'center', gap: a(5), height: a(h), padding: `0 ${a(11)}px`, borderRadius: a(h), boxShadow: `inset 0 0 0 ${a(1)}px ${t.color.hairline}`, background: t.color.surface, whiteSpace: 'nowrap', ...style}}>
      {icon}
      {label}
    </div>
  );
}

/** A status disc for checklists: done (accent + check), next (accent ring), todo (grey ring). `done` animates. */
export function StatusDisc({state, done = 1, size = 22}: {state: 'done' | 'next' | 'todo'; done?: number; size?: number}) {
  const a = useAppPx();
  const t = useTokens();
  const d = state === 'done' ? clamp01(done) : 0;
  const ring = state === 'next' ? t.color.accent : t.color.gray[3] ?? t.color.ink3;
  return (
    <div style={{width: a(size), height: a(size), borderRadius: a(size), flex: 'none', background: mix(t.color.surface, t.color.accent, d), boxShadow: `inset 0 0 0 ${a(1.5 * (1 - d))}px ${d > 0 ? t.color.accent : ring}`, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
      <IconCheck size={a(size * 0.62)} draw={clamp01((d - 0.3) / 0.7)} />
    </div>
  );
}

/** A checklist row: disc, optional number, title, meta at the right. */
export function CheckRow({n, title, meta, state, done = 1, w = 500}: {n?: number; title: string; meta?: string; state: 'done' | 'next' | 'todo'; done?: number; w?: number}) {
  const a = useAppPx();
  const t = useTokens();
  const titleS = useType('label', state === 'todo' ? t.color.ink2 : t.color.ink);
  const monoS = useType('mono');
  const metaS = useType('eyebrow', state === 'next' ? t.color.accentInk : t.color.ink3);
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: a(12), width: a(w), height: a(48), boxSizing: 'border-box', padding: `0 ${a(4)}px`}}>
      <StatusDisc state={state} done={done} />
      {n !== undefined && <span style={{...monoS, width: a(18)}}>{String(n).padStart(2, '0')}</span>}
      <span style={{...titleS, flex: 1}}>{title}</span>
      {meta && <span style={metaS}>{meta}</span>}
    </div>
  );
}

/** Progress in blocks: `value` in blocks, fractional. */
export function ProgressBlocks({n = 5, value, w = 300, h = 6, gap = 5}: {n?: number; value: number; w?: number; h?: number; gap?: number}) {
  const a = useAppPx();
  const t = useTokens();
  const bw = (a(w) - a(gap) * (n - 1)) / n;
  return (
    <div style={{display: 'flex', gap: a(gap), width: a(w)}}>
      {Array.from({length: n}, (_, i) => {
        const f = clamp01(value - i);
        return (
          <div key={i} style={{width: bw, height: a(h), borderRadius: a(h), background: t.color.gray[1] ?? t.color.hairline, overflow: 'hidden'}}>
            <div style={{width: `${f * 100}%`, height: '100%', background: t.color.accent, borderRadius: a(h)}} />
          </div>
        );
      })}
    </div>
  );
}

/** A progress bar (time and progress may move linearly: they represent time). */
export function ProgressBar({value, w = 300, h = 6}: {value: number; w?: number; h?: number}) {
  const a = useAppPx();
  const t = useTokens();
  return (
    <div style={{width: a(w), height: a(h), borderRadius: a(h), background: t.color.gray[1] ?? t.color.hairline, overflow: 'hidden'}}>
      <div style={{width: `${clamp01(value) * 100}%`, height: '100%', background: t.color.accent}} />
    </div>
  );
}

/** An initials avatar. */
export function Avatar({initials, size = 32, bg, color}: {initials: string; size?: number; bg?: string; color?: string}) {
  const a = useAppPx();
  const t = useTokens();
  const s = useType('label', color ?? t.color.onAccent);
  return <div style={{...s, width: a(size), height: a(size), borderRadius: a(size), background: bg ?? t.color.accent, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: a(size * 0.36), flex: 'none'}}>{initials}</div>;
}

/** A small dot bullet. */
export function Dot({color, size = 5}: {color?: string; size?: number}) {
  const a = useAppPx();
  const t = useTokens();
  return <span style={{display: 'inline-block', width: a(size), height: a(size), borderRadius: a(size), background: color ?? t.color.accent, flex: 'none'}} />;
}

/** A small corner label (a "sample data" mark wherever product data shows): its text comes from the brand or the film. */
export function CornerLabel({text, corner = 'bottom-left', inset = 0.035, style}: {text: string; corner?: 'bottom-left' | 'bottom-right' | 'top-left' | 'top-right'; inset?: number; style?: React.CSSProperties}) {
  const a = useAppPx();
  const t = useTokens();
  const s = useType('mono');
  const pos: React.CSSProperties = {position: 'absolute'};
  const off = `${inset * 100}%`;
  if (corner.startsWith('bottom')) pos.bottom = off;
  else pos.top = off;
  if (corner.endsWith('left')) pos.left = off;
  else pos.right = off;
  return <div style={{...s, ...pos, background: t.color.gray[1] ?? t.color.hairline, borderRadius: a(4), padding: `${a(3)}px ${a(6)}px`, ...style}}>{text}</div>;
}

// ---------------------------------------------------------------------------------------------------- streaming text
/** One run of a streamed line; `token` marks a slot a carried element lands in (hidden until it lands). */
export type StreamSeg = {text: string; bold?: boolean; color?: string; token?: string};
export type StreamLine = StreamSeg[];

/** Characters in a set of lines (a stream's clock counts characters). */
export const charCount = (lines: StreamLine[]): number => lines.reduce((n, l) => n + l.reduce((m, s) => m + s.text.length, 0), 0);

/**
 * A streamed answer: characters appear in reading order as `revealed` counts up (streaming is the one place text may
 * type on, because the product really streams), each settling from faint to full over a short trailing edge. Token runs
 * stay hidden until their carried element lands (`landed[token]` 0 → 1).
 *
 * In tilt mode the stream is one tilted block (TypeTilt) and each character fades by its colour's alpha instead of an
 * opacity: a character with an opacity of its own may be painted apart from the block, out of reach of its tilt, and
 * boxes per character would lose the kerning, so running text tilts whole.
 */
export function StreamText({lines, revealed, role = 'body', color, leading = 1.5, landed = {}, edge = 7}: {lines: StreamLine[]; revealed: number; role?: string; color?: string; leading?: number; landed?: Record<string, number>; edge?: number}) {
  const s = useType(role, color);
  const tilt = useRasterMode() === 'tilt';
  // tilt mode: a fade as the colour's alpha (times any alpha the colour has), never an opacity
  const faded = (c: string | undefined, op: number): React.CSSProperties => {
    const base = c ?? String(s.color ?? '');
    if (!tilt) return {color: c, opacity: op};
    const own = parseColor(base)?.a ?? 1;
    return {color: alpha(base, own * op)};
  };
  let ci = 0;
  const body = lines.map((line, li) => (
    <div key={li}>
      {line.map((seg, si) => {
        const start = ci;
        ci += seg.text.length;
        const weight = seg.bold ? 700 : s.fontWeight;
        if (seg.token) return <span key={si} style={{fontWeight: weight, color: seg.color ?? s.color, ...faded(seg.color ?? s.color, clamp01(landed[seg.token] ?? 0))}}>{seg.text}</span>;
        return (
          <span key={si} style={{fontWeight: weight, color: seg.color ?? s.color}}>
            {Array.from(seg.text).map((ch, k) => {
              const u = clamp01((revealed - (start + k)) / edge);
              const op = u <= 0 ? 0 : 0.35 + 0.65 * u;
              return (
                <span key={k} style={tilt ? faded(seg.color ?? s.color, op) : {opacity: op}}>
                  {ch}
                </span>
              );
            })}
          </span>
        );
      })}
    </div>
  ));
  const style: React.CSSProperties = {...s, position: 'relative', lineHeight: leading, whiteSpace: 'pre'};
  return tilt ? <TypeTilt style={style}>{body}</TypeTilt> : <div style={style}>{body}</div>;
}
