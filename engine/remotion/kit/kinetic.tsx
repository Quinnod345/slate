// Kinetic type: words that land on the words the voice says. Each word's entrance is started so 90% of its travel
// lands on its own VO word (the same word timings the sound is cut to), rising and growing from 85% (nothing scales
// from zero), on the brand's entrance ease. For "kinetic type" films, statement beats, and social reels.
import React from 'react';
import {norm, word as wordAt} from './core/film';
import {durRank, easeNamed, exitDur, roleEase, t90Of} from './core/motion';
import {clamp01} from './core/spline';
import {useFilmTime, useFrameSize, useTokens, useType} from './context';
import {inMotion, motionHint, TiltSpan, useRasterMode} from './paper';

/** Film seconds each word of `text` is said in a shot's read (repeats counted), or the given times. */
export function wordLandings(shotId: string, text: string, times?: number[]): number[] {
  const seen = new Map<string, number>();
  return text
    .split(/\s+/)
    .filter(Boolean)
    .map((w, i) => {
      if (times?.[i] !== undefined) return times[i];
      const key = norm(w);
      const occ = (seen.get(key) ?? 0) + 1;
      seen.set(key, occ);
      return wordAt(shotId, w, occ);
    });
}

export type KineticWordsProps = {
  shot: string;
  text: string;
  /** film seconds per word, to override the VO (for type with no voice under it) */
  times?: number[];
  role?: string;
  /** words to set in the accent */
  highlight?: string[];
  align?: 'left' | 'center' | 'right';
  /** the block's width as a fraction of the frame's width */
  width?: number;
  /** film seconds the whole block leaves (on the exit ease) */
  out?: number;
  t?: number;
  style?: React.CSSProperties;
};

/** A block of words, each landing on its VO word. In tilt mode each word's tilt sits inside its own fading box. */
export function KineticWords({shot: shotId, text, times, role = 'hero', highlight = [], align = 'center', width = 0.8, out, t: tOverride, style}: KineticWordsProps) {
  const clock = useFilmTime();
  const tilt = useRasterMode() === 'tilt';
  const {width: W, short} = useFrameSize();
  const tk = useTokens();
  const s = useType(role);
  const t = tOverride ?? clock;
  const words = text.split(/\s+/).filter(Boolean);
  const lands = wordLandings(shotId, text, times);
  const d = durRank(2);
  const e = easeNamed(roleEase('entrance'));
  const lead = t90Of(roleEase('entrance')) * d;
  const hi = new Set(highlight.map((w) => norm(w)));
  const exitU = out === undefined ? 0 : easeNamed(roleEase('exit'))((t - out) / exitDur(d));
  return (
    <div style={{...s, width: W * width, display: 'flex', flexWrap: 'wrap', justifyContent: align === 'center' ? 'center' : align === 'right' ? 'flex-end' : 'flex-start', columnGap: '0.28em', rowGap: '0.05em', opacity: 1 - exitU, transform: exitU > 0 ? `translateY(${(-0.015 * short * exitU).toFixed(3)}px)` : undefined, ...style}}>
      {words.map((w, i) => {
        const u = e((t - (lands[i] - lead)) / d);
        return (
          <span
            key={i}
            style={{
              display: 'inline-block',
              color: hi.has(norm(w)) ? tk.color.accent : undefined,
              opacity: clamp01(u * 1.4),
              transform: `translateY(${((1 - u) * 0.03 * short).toFixed(3)}px) scale(${(0.85 + 0.15 * u).toFixed(5)})`,
              transformOrigin: '50% 80%',
              ...(tilt ? {} : motionHint(inMotion(u))),
            }}
          >
            <TiltSpan>{w}</TiltSpan>
          </span>
        );
      })}
    </div>
  );
}

/**
 * Lines that replace one another, each landing with its first word and leaving (on the exit ease) as the next one
 * arrives: a lyric-style stack for reels, one or two lines on screen at a time.
 */
export function KineticStack({shot: shotId, lines, role = 'hero', highlight = [], t: tOverride}: {shot: string; lines: string[]; role?: string; highlight?: string[]; t?: number}) {
  const clock = useFilmTime();
  const t = tOverride ?? clock;
  const d = durRank(2);
  const lead = t90Of(roleEase('entrance')) * d;
  // every word of every line, found in the read in order (repeats counted across lines)
  const lands = wordLandings(shotId, lines.join(' '));
  const counts = lines.map((l) => l.split(/\s+/).filter(Boolean).length);
  const offsets = counts.map((_, i) => counts.slice(0, i).reduce((a, b) => a + b, 0));
  const starts = offsets.map((o) => lands[o] ?? 0);
  return (
    <div style={{position: 'relative', width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center'}}>
      {lines.map((line, i) => {
        const enter = starts[i] - lead;
        const next = i + 1 < lines.length ? starts[i + 1] - lead : Infinity;
        if (t < enter - 0.001 || t > next + exitDur(d)) return null;
        const times = lands.slice(offsets[i], offsets[i] + counts[i]);
        return <KineticWords key={i} shot={shotId} text={line} times={times} role={role} highlight={highlight} out={Number.isFinite(next) ? next - exitDur(d) : undefined} t={t} />;
      })}
    </div>
  );
}
