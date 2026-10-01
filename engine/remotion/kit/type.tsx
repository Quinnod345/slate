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

/** The film time a count reads at `t`: its master frame's, so every motion-blur sub-sample of a frame (they sit within
 * a quarter frame of it) reads the same value. */
export function masterTime(t: number, rate = fps()): number {
  return Math.round(t * rate) / rate;
}

/**
 * The value a count shows at film time `t`: the master frame's time (masterTime), held for `hold` whole frames from
 * `t0` so figures tick rather than flicker, on the entrance ease over `d` seconds.
 */
export function countValue(t: number, {t0, from, to, d, hold = 3, rate = fps()}: {t0: number; from: number; to: number; d: number; hold?: number; rate?: number}): number {
  const step = Math.max(1, hold);
  const tm = masterTime(t, rate);
  // hold each figure for `step` frames: sample the ease at the start of each hold (1e-6 absorbs float error at a hold's
  // first frame, so a count that starts on a whole frame holds exactly `step` frames)
  const held = t0 + Math.floor(((tm - t0) * rate) / step + 1e-6) * (step / rate);
  const u = tm >= t0 + d ? 1 : easeNamed(roleEase('entrance'))((held - t0) / d);
  return from + (to - from) * u;
}

/** A count's text with every figure written as a 9: the shape of the box it's drawn over until it has its final figures. */
export const countShape = (text: string): string => text.replace(/[0-9]/g, '9');

/**
 * A number counting to its value on the entrance ease, tabular figures. Each shown figure holds for `hold` whole
 * frames so digits tick rather than flicker; count once (a second count of a number already on screen reads as a
 * glitch).
 *
 * One value per frame: the count reads its master frame's time, so every motion-blur sub-sample of a frame shows the
 * same figures while the numeral's own movement still blurs. Counting per sub-sample blended the values either side of
 * each change into double-exposed digits (the films' lesson L-kit-D152).
 *
 * It never re-centres. With a face that has no tabular figures (`tabular-nums` then changes nothing) a count re-flowed
 * as its figures changed width: a centred count stepped +1 px, then −1 px a frame later, and anything after it was
 * pushed back and forth (L-kit-S006). So the count is drawn over an unseen box that holds still while its figures change
 * (the films' refined rule, 2026-10-01):
 * - once the count has the final value's figures (as many digits, the same commas and signs), the box is the final
 *   value, so the final value rests exactly where it would on its own;
 * - before that, the box is the count's own shape in nines (99 while it passes 10 to 99). 9 is a figure of typical width
 *   in most faces, so a centred count from 0 sits within a pixel or two of its true centre. A final-value box from the
 *   first frame put the films' counts from 0 half a figure left of centre (37 px);
 * - the count is drawn from its box's left edge, so within each stretch the leading figures hold still and only the
 *   changing ones change;
 * - a suffix in the format ("2,500+") holds still at the box's right end, and the last figure is centred in the room
 *   between it and the figures before it. Drawn after the figures, the "+" stepped with the last figure's width;
 * - `followed`: a word comes right after the count ("%", a label). The word never moves: the final value is the box for
 *   the whole count, and the count is drawn against its right edge, its last figure centred in the room before it. A
 *   count that stands alone and is centred keeps its stretch boxes.
 * At the final value the line is drawn whole, as it rests.
 */
export function CountUp({t0, from, to, duration, hold = 3, format = (v: number) => Math.round(v).toString(), t: tOverride, role = 'numeral', color, style, followed = false}: {t0: number; from: number; to: number; duration?: number; hold?: number; format?: (v: number) => string; t?: number; role?: string; color?: string; style?: React.CSSProperties; followed?: boolean}) {
  const clock = useFilmTime();
  const s = useType(role, color);
  const t = tOverride ?? clock;
  // count-ups take the broad step of the brand's scale (its fourth-shortest) unless told otherwise
  const d = duration ?? durRank(3);
  const text = format(countValue(t, {t0, from, to, d, hold}));
  const final = format(to);
  // the box of the stretch the count is in: the final value once the count has its figures, its own nines before
  const run = countShape(text) === countShape(final) ? final : countShape(text);
  // [, the figures before the last, the last figure, a suffix]
  const parts = text === final ? null : /^(.*)([0-9])([^0-9]*)$/.exec(text);
  const boxed = (box: string, drawn: React.ReactNode) => (
    <span style={{position: 'relative', display: 'inline-block'}}>
      <span style={{visibility: 'hidden'}}>{box}</span>
      {drawn}
    </span>
  );
  const plain = <span style={{position: 'absolute', left: 0, top: 0, whiteSpace: 'nowrap'}}>{text}</span>;
  // the figures before the last at the left, the suffix at the right, and the last figure centred in the room between
  // (it may be a little narrower than the figure: the room shrinks and the figure overhangs it evenly on both sides)
  const slot = (m: RegExpExecArray) => (
    <span style={{position: 'absolute', left: 0, right: 0, top: 0, display: 'flex', whiteSpace: 'nowrap'}}>
      <span style={{flexShrink: 0}}>{m[1]}</span>
      <span style={{flex: '1 1 0', minWidth: 0, display: 'flex', justifyContent: 'center'}}>{m[2]}</span>
      <span style={{flexShrink: 0}}>{m[3]}</span>
    </span>
  );
  let drawn: React.ReactNode;
  if (followed && parts) drawn = boxed(final, <span style={{position: 'absolute', right: 0, top: 0, whiteSpace: 'nowrap'}}>{boxed(run, slot(parts))}</span>);
  else if (followed) drawn = boxed(final, plain);
  else if (parts && parts[3]) drawn = boxed(run, slot(parts));
  else drawn = boxed(run, plain);
  return <span style={{...s, fontVariantNumeric: 'tabular-nums', ...style}}>{drawn}</span>;
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
