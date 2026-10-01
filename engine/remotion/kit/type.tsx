// Type in motion, in the brand's faces and on the brand's eases. Hero lines write on per character, lines per word,
// blocks arrive as one piece. Every stagger is capped by the brand's law (a group's spread stays within 0.6 s and half
// its entrance by default: the films' per-character write-on had no cap and three acts re-implemented one). Hero type
// moves as well as fades, so it never arrives on opacity alone.
import React from 'react';
import {fps} from './core/film';
import {durRank, easeNamed, exitDur, law, roleEase, staggerStep} from './core/motion';
import {clamp01} from './core/spline';
import {useFilmTime, useKit, useType} from './context';
import {inMotion, motionHint} from './paper';

type Common = {
  /** film seconds the first unit starts moving (use startFor(word(…), …) to land it on a word) */
  t0: number;
  /** film seconds the units leave together on the brand's exit ease (optional) */
  out?: number;
  /** film time override (default: the frame being drawn) */
  t?: number;
  /** a type role (hero, h1, body …) whose brand style the text takes */
  role?: string;
  color?: string;
  style?: React.CSSProperties;
};

/** Text in a brand type role. */
export function Text({role = 'body', color, style, children}: {role?: string; color?: string; style?: React.CSSProperties; children?: React.ReactNode}) {
  const s = useType(role, color);
  return <span style={{...s, ...style}}>{children}</span>;
}

/** Units leaving together on the brand's exit ease, over its exit share of the base entrance. */
function useExit(t: number, out: number | undefined, riseFrac: number, short: number): {op: number; dy: number} {
  if (out === undefined) return {op: 1, dy: 0};
  const u = clamp01(easeNamed(roleEase('exit'))((t - out) / exitDur(baseDur())));
  return {op: 1 - u, dy: -riseFrac * short * u};
}

/** The base entrance duration: the brand's third-shortest step (its "base"), in seconds. */
function baseDur(): number {
  const L = law();
  const sorted = Object.values(L.durations).sort((a, b) => a - b);
  return sorted[Math.min(2, sorted.length - 1)] ?? 0.5;
}

/**
 * Per-character write-on (hero lines): each character rises and sharpens into place on the entrance ease, staggered by
 * the brand's character unit and capped so the whole line spreads no more than the brand allows.
 */
export function PerChar({text, t0, out, t: tOverride, role = 'hero', color, style, duration}: Common & {text: string; duration?: number}) {
  const clock = useFilmTime();
  const {short} = useKit();
  const s = useType(role, color);
  const t = tOverride ?? clock;
  const d = duration ?? baseDur();
  const chars = Array.from(text);
  const n = chars.filter((c) => c.trim()).length;
  const step = staggerStep('char', n, d);
  const e = easeNamed(roleEase('entrance'));
  const ex = useExit(t, out, 0.015, short);
  const rise = 0.022 * short;
  const blur = 0.0056 * short;
  let k = 0;
  return (
    <span style={{...s, display: 'inline-block', whiteSpace: 'pre', ...style}}>
      {chars.map((ch, i) => {
        if (!ch.trim()) return <span key={i}>{ch}</span>;
        const u = e((t - (t0 + k++ * step)) / d);
        return (
          <span
            key={i}
            style={{
              display: 'inline-block',
              opacity: clamp01(u * 1.4) * ex.op,
              transform: `translateY(${((1 - u) * rise + ex.dy).toFixed(3)}px)`,
              filter: u < 0.999 ? `blur(${((1 - u) * blur).toFixed(3)}px)` : undefined,
              ...motionHint(inMotion(u) || (ex.dy < -0.0001 && ex.op > 0.001)),
            }}
          >
            {ch}
          </span>
        );
      })}
    </span>
  );
}

/** Per-word build (lines): each word rises on the entrance ease, by the brand's word unit or at given times. */
export function PerWord({text, t0, out, t: tOverride, role = 'h2', color, style, duration, wordTimes}: Common & {text: string; duration?: number; wordTimes?: number[]}) {
  const clock = useFilmTime();
  const {short} = useKit();
  const s = useType(role, color);
  const t = tOverride ?? clock;
  const d = duration ?? baseDur();
  const words = text.split(/\s+/).filter(Boolean);
  const step = staggerStep('word', words.length, d);
  const e = easeNamed(roleEase('entrance'));
  const ex = useExit(t, out, 0.015, short);
  const rise = 0.0185 * short;
  const blur = 0.0037 * short;
  return (
    <span style={{...s, display: 'inline', ...style}}>
      {words.map((w, i) => {
        const start = wordTimes?.[i] ?? t0 + i * step;
        const u = e((t - start) / d);
        return (
          <React.Fragment key={i}>
            <span
              style={{
                display: 'inline-block',
                opacity: clamp01(u * 1.3) * ex.op,
                transform: `translateY(${((1 - u) * rise + ex.dy).toFixed(3)}px)`,
                filter: u < 0.999 ? `blur(${((1 - u) * blur).toFixed(3)}px)` : undefined,
                ...motionHint(inMotion(u) || (ex.dy < -0.0001 && ex.op > 0.001)),
              }}
            >
              {w}
            </span>
            {i < words.length - 1 ? ' ' : null}
          </React.Fragment>
        );
      })}
    </span>
  );
}

/** A block that arrives as one piece (a line, a row, a card's content): rise and opacity on an ease. */
export function Arrive({t0, out, t: tOverride, duration, rise = 0.0185, ease, children, style}: Common & {duration?: number; rise?: number; ease?: string; children?: React.ReactNode}) {
  const clock = useFilmTime();
  const {short} = useKit();
  const t = tOverride ?? clock;
  const d = duration ?? baseDur();
  const u = easeNamed(ease ?? roleEase('entrance'))((t - t0) / d);
  const ex = useExit(t, out, 0.015, short);
  return <div style={{opacity: clamp01(u * 1.3) * ex.op, transform: `translateY(${((1 - u) * rise * short + ex.dy).toFixed(3)}px)`, ...motionHint(inMotion(u) || (ex.dy < -0.0001 && ex.op > 0.001)), ...style}}>{children}</div>;
}

/**
 * A number counting to its value on the entrance ease, tabular figures. Each shown figure holds for `hold` whole
 * frames so digits tick rather than flicker; count once (a second count of a number already on screen reads as a
 * glitch).
 *
 * The final value, unseen, sets the number's box, and the count is drawn over it from the box's left edge. With a face
 * that has no tabular figures (`tabular-nums` then changes nothing) a centred count re-centred a pixel at a time as its
 * digits changed width: +1 px, then -1 px a frame later (the films' lesson L-kit-S006). Now the leading digits hold
 * still, only the changing ones change, and the final value sits exactly where it would on its own.
 */
export function CountUp({t0, from, to, duration, hold = 3, format = (v: number) => Math.round(v).toString(), t: tOverride, role = 'numeral', color, style}: {t0: number; from: number; to: number; duration?: number; hold?: number; format?: (v: number) => string; t?: number; role?: string; color?: string; style?: React.CSSProperties}) {
  const clock = useFilmTime();
  const s = useType(role, color);
  const t = tOverride ?? clock;
  // count-ups take the broad step of the brand's scale (its fourth-shortest) unless told otherwise
  const d = duration ?? durRank(3);
  const rate = fps();
  // hold each figure for `hold` frames: sample the ease at the start of each hold
  const heldT = t0 + Math.floor(((t - t0) * rate) / Math.max(1, hold)) * (Math.max(1, hold) / rate);
  const u = t >= t0 + d ? 1 : easeNamed(roleEase('entrance'))((heldT - t0) / d);
  return (
    <span style={{...s, fontVariantNumeric: 'tabular-nums', ...style}}>
      <span style={{position: 'relative', display: 'inline-block'}}>
        <span style={{visibility: 'hidden'}}>{format(to)}</span>
        <span style={{position: 'absolute', left: 0, top: 0, whiteSpace: 'nowrap'}}>{format(from + (to - from) * u)}</span>
      </span>
    </span>
  );
}

let measureCtx: CanvasRenderingContext2D | null = null;

/** Width of a string in a type role at this frame's size, measured on a canvas once fonts are loaded. */
export function measureText(text: string, font: string): number {
  if (typeof document === 'undefined') {
    const m = font.match(/(\d+(?:\.\d+)?)px/);
    return text.length * (m ? Number(m[1]) : 16) * 0.5;
  }
  if (!measureCtx) measureCtx = document.createElement('canvas').getContext('2d');
  if (!measureCtx) return 0;
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
}
