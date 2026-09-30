// Captions, as data: the film's timing/captions.json (written by `slate voice captions` from the same word timings the
// picture and sound are cut to) read into cues the burn-in component draws. Accepts the films' shape, a list of
// {shot, text, lines, start, end} in film seconds, and a wrapped {cues: […]}; frames are converted with the film's rate.
import {filmData, secOf} from './film';

export type Cue = {start: number; end: number; text: string; lines: string[]; shot: string | null};

const pick = (o: Record<string, unknown>, keys: string[]): unknown => {
  for (const k of keys) if (o[k] !== undefined) return o[k];
  return undefined;
};

/** Break text into at most `maxLines` lines of at most `maxChars` characters, at spaces, balancing the lines. */
export function breakLines(text: string, maxChars = 42, maxLines = 2): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  if (text.length <= maxChars) return [words.join(' ')];
  // balanced: try every split point for two lines; greedy beyond
  if (maxLines === 2) {
    let best: string[] | null = null;
    let bestScore = Infinity;
    for (let k = 1; k < words.length; k++) {
      const a = words.slice(0, k).join(' ');
      const b = words.slice(k).join(' ');
      if (a.length > maxChars || b.length > maxChars) continue;
      const score = Math.abs(a.length - b.length);
      if (score < bestScore) {
        best = [a, b];
        bestScore = score;
      }
    }
    if (best) return best;
  }
  const lines: string[] = [];
  let cur = '';
  for (const w of words) {
    if (!cur) cur = w;
    else if ((cur + ' ' + w).length <= maxChars) cur += ' ' + w;
    else {
      lines.push(cur);
      cur = w;
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

/** Read captions from any of the accepted shapes. Problems are listed, never thrown. */
export function normalizeCaptions(raw: unknown, opts: {maxChars?: number; maxLines?: number} = {}): {cues: Cue[]; problems: string[]} {
  const problems: string[] = [];
  let list: unknown[] = [];
  if (Array.isArray(raw)) list = raw;
  else if (raw && typeof raw === 'object') {
    const o = raw as Record<string, unknown>;
    const inner = pick(o, ['cues', 'captions', 'segments', 'items']);
    if (Array.isArray(inner)) list = inner;
    else problems.push('captions.json has no list of cues');
  } else if (raw !== undefined && raw !== null) problems.push('captions.json isn’t a list or an object');
  const cues: Cue[] = [];
  list.forEach((item, i) => {
    if (!item || typeof item !== 'object') return;
    const o = item as Record<string, unknown>;
    let start = Number(pick(o, ['start', 'from', 't0', 'in', 'sec_in']));
    let end = Number(pick(o, ['end', 'to', 't1', 'out', 'sec_out']));
    const fin = pick(o, ['frame_in', 'start_frame']);
    const fout = pick(o, ['frame_out', 'end_frame']);
    if (!Number.isFinite(start) && fin !== undefined) start = secOf(Number(fin));
    if (!Number.isFinite(end) && fout !== undefined) end = secOf(Number(fout));
    const linesRaw = pick(o, ['lines']);
    const textRaw = pick(o, ['text', 'caption', 'line']);
    const text = typeof textRaw === 'string' ? textRaw : Array.isArray(linesRaw) ? linesRaw.join(' ') : '';
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || !text.trim()) {
      problems.push(`cue ${i + 1} is missing a start, an end or its text`);
      return;
    }
    const lines = Array.isArray(linesRaw) && linesRaw.length ? linesRaw.map(String) : breakLines(text, opts.maxChars ?? 42, opts.maxLines ?? 2);
    cues.push({start, end, text, lines, shot: typeof o.shot === 'string' ? o.shot : null});
  });
  cues.sort((a, b) => a.start - b.start);
  return {cues, problems};
}

let cached: {src: unknown; cues: Cue[]; problems: string[]} | null = null;

/** The film's captions (from `@film/data`). */
export function captions(): {cues: Cue[]; problems: string[]} {
  const src = filmData()?.captions;
  if (!cached || cached.src !== src) cached = {src, ...normalizeCaptions(src)};
  return {cues: cached.cues, problems: cached.problems};
}

/** The cue on screen at film time t (null between cues). */
export function cueAt(cues: Cue[], t: number): Cue | null {
  for (const c of cues) if (t >= c.start && t < c.end) return c;
  return null;
}

/** Characters per second of a cue (the accessibility gate reads ≤ 17). */
export const cueCps = (c: Cue): number => c.text.replace(/\s+/g, ' ').length / Math.max(1e-6, c.end - c.start);
