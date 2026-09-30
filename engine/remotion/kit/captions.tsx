// Captions burned into the picture by the renderer, in the brand's font: the cue on screen comes from the film's
// timing/captions.json (the same word timings the picture and sound are cut to). Two lines at most, inside the
// bottom safe zone the film's format sets, on a quiet plate so they read over anything. Each cue settles in and out
// over the brand's shortest step: captions never animate for show.
import {captions as filmCaptions, cueAt, type Cue} from './core/captions';
import {alpha} from './core/color';
import {durRank, easeNamed, roleEase} from './core/motion';
import {clamp01} from './core/spline';
import {useFilmTime, useFrameSize, useTokens, useType} from './context';

export type CaptionsProps = {
  /** safe zone as fractions of the frame: bottom inset and side insets (the format's platform safe zone) */
  safe?: {bottom?: number; side?: number};
  role?: string;
  /** a quiet plate behind the lines (on by default); off draws ink with a soft halo */
  plate?: boolean;
  maxLines?: number;
  /** cues to draw instead of the film's */
  cues?: Cue[];
  t?: number;
};

export function Captions({safe = {}, role = 'caption', plate = true, maxLines = 2, cues, t: tOverride}: CaptionsProps) {
  const clock = useFilmTime();
  const {width, height, short} = useFrameSize();
  const tk = useTokens();
  const t = tOverride ?? clock;
  const list = cues ?? filmCaptions().cues;
  const cue = cueAt(list, t);
  const fg = plate ? tk.color.onInverse : tk.color.ink;
  const s = useType(role, fg);
  if (!cue) return null;
  const d = durRank(0);
  const e = easeNamed(roleEase('state'));
  const op = clamp01(e((t - cue.start) / d)) * (1 - clamp01(e((t - (cue.end - d)) / d)));
  const bottom = (safe.bottom ?? 0.08) * height;
  const side = (safe.side ?? 0.1) * width;
  const pad = 0.012 * short;
  return (
    <div style={{position: 'absolute', left: side, right: side, bottom, display: 'flex', justifyContent: 'center', opacity: op, pointerEvents: 'none'}}>
      <div
        style={{
          ...s,
          textAlign: 'center',
          lineHeight: s.lineHeight,
          padding: plate ? `${pad * 0.6}px ${pad * 1.4}px` : 0,
          borderRadius: plate ? pad : 0,
          background: plate ? alpha(tk.color.inverse, 0.78) : undefined,
          textShadow: plate ? undefined : `0 0 ${pad}px ${alpha(tk.color.page, 0.9)}`,
        }}
      >
        {cue.lines.slice(0, maxLines).map((line, i) => (
          <div key={i}>{line}</div>
        ))}
      </div>
    </div>
  );
}
