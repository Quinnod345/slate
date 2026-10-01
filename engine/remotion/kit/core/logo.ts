// The logo build, planned from a spec: a brand's SVG plus how it is built (construction lines, vertex marks, fill
// order, an optional keystone that lands last, and timing). The plan is pure data (seconds from the build's start), so
// the picture, the events file and the review sheet all read the same moments. logo.tsx draws it.
//
// The technique is the films' keystone build, generalised: guides survey the mark along its own edges, vertex marks
// arrive where its outline turns, the outlines draw, the parts fill in order, and one part (the keystone) hangs, drops
// and lands with an echo. Geometry comes from the artwork itself, never from a hand-measured table.
import {durRank, exitDur, staggerStep} from './motion';
import {parseSvg, unionBox, type Box, type ParsedSvg, type Pt, type SvgShape} from './svg';

export type LogoOrder = 'left-to-right' | 'right-to-left' | 'top-to-bottom' | 'bottom-to-top' | 'as-drawn';

export type LogoBuildSpec = {
  /** construction lines along the parts' edges, none, or explicit positions in SVG units */
  guides?: 'edges' | 'none' | {h?: number[]; v?: number[]};
  vertices?: 'corners' | 'all' | 'none';
  order?: LogoOrder | (number | string)[];
  /** the part that lands last (shape index or id), or null for none */
  keystone?: number | string | null;
  keystone_from?: 'above' | 'below' | 'left' | 'right';
  outline?: boolean;
  impact?: boolean;
  /** seconds, or duration tokens of the brand's scale */
  timing?: {construct?: number | string; outline?: number | string; fill?: number | string; stagger?: number | string; drop?: number | string; out?: number | string};
  max_guides?: number;
  max_marks?: number;
};

export type PlannedShape = SvgShape & {
  /** position in the fill order (-1 for the keystone) */
  order: number;
  keystone: boolean;
  fillStart: number;
  fillDur: number;
};

export type LogoTimes = {
  constructDur: number;
  /** when the survey is done: the last guide drawn out, the last vertex mark arrived (seconds from the build's start) */
  constructEnd: number;
  outlineStart: number;
  outlineDur: number;
  fillStart: number;
  fillEnd: number;
  hangStart: number | null;
  dropStart: number | null;
  dropDur: number | null;
  land: number | null;
  echoDur: number;
  outStart: number;
  outDur: number;
  end: number;
};

export type LogoPlan = {
  viewBox: [number, number, number, number];
  box: Box;
  shapes: PlannedShape[];
  guides: {h: number[]; v: number[]};
  marks: Pt[];
  times: LogoTimes;
  keystone: number | null;
  keystoneFrom: 'above' | 'below' | 'left' | 'right';
  wipe: 'x+' | 'x-' | 'y+' | 'y-';
  outline: boolean;
  impact: boolean;
  problems: string[];
};

/** Seconds between one construction guide and the next as they draw out (logo.tsx draws them; the plan times them). */
export const GUIDE_STAGGER_S = 0.05;
/** The vertex marks arrive one after another across this fraction of the construction time… */
export const MARK_SPREAD = 0.6;
/** …each over this fraction of it. */
export const MARK_DUR = 0.5;

/**
 * When the survey is done, in seconds from the build's start: the last guide's draw (each takes the construction time,
 * staggered by GUIDE_STAGGER_S) or the last mark's arrival, whichever ends later. 0 when there's nothing to survey.
 */
export function surveyEnd(guides: number, marks: number, constructDur: number): number {
  const lastGuide = guides > 0 ? (guides - 1) * GUIDE_STAGGER_S + constructDur : 0;
  const lastMark = marks > 0 ? (marks > 1 ? MARK_SPREAD * constructDur : 0) + MARK_DUR * constructDur : 0;
  return Math.max(lastGuide, lastMark);
}

function asSeconds(v: number | string | undefined, fallback: number, dur: (s: string | number) => number): number {
  if (v === undefined) return fallback;
  return typeof v === 'number' ? v : dur(v);
}

function dedupe(values: number[], tol: number): number[] {
  const out: number[] = [];
  for (const v of values.slice().sort((a, b) => a - b)) if (!out.length || Math.abs(v - out[out.length - 1]) > tol) out.push(v);
  return out;
}

/** Keep at most n values, always the two ends, evenly spread between. */
function thin<T>(list: T[], n: number): T[] {
  if (list.length <= n || n < 2) return list;
  const out: T[] = [];
  for (let k = 0; k < n; k++) out.push(list[Math.round((k * (list.length - 1)) / (n - 1))]);
  return out;
}

/**
 * Plan a build. `svg` is the brand's SVG text (or an already parsed one); `dur` resolves duration tokens (the motion
 * law's dur(), passed in so this stays testable with any scale).
 */
export function planLogoBuild(svg: string | ParsedSvg, spec: LogoBuildSpec = {}, dur?: (s: string | number) => number): LogoPlan {
  const parsed = typeof svg === 'string' ? parseSvg(svg) : svg;
  const problems = [...parsed.problems];
  const d = dur ?? ((s: string | number) => (typeof s === 'number' ? s : durRank(3)));
  const [, , vw, vh] = parsed.viewBox;
  const tol = 0.01 * Math.max(vw, vh, 1);

  // the keystone
  let keystone: number | null = null;
  if (spec.keystone !== undefined && spec.keystone !== null) {
    const k = typeof spec.keystone === 'number' ? parsed.shapes.find((s) => s.index === spec.keystone) : parsed.shapes.find((s) => s.id === spec.keystone);
    if (k) keystone = k.index;
    else problems.push(`keystone "${spec.keystone}" isn't a shape of the logo`);
  }

  // fill order
  const rest = parsed.shapes.filter((s) => s.index !== keystone);
  let ordered: SvgShape[];
  let wipe: LogoPlan['wipe'] = 'x+';
  const order = spec.order ?? 'left-to-right';
  if (Array.isArray(order)) {
    const pick = order.map((o) => (typeof o === 'number' ? rest.find((s) => s.index === o) : rest.find((s) => s.id === o))).filter((s): s is SvgShape => !!s);
    const missing = rest.filter((s) => !pick.includes(s));
    if (pick.length < order.length) problems.push('the fill order names shapes the logo doesn’t have');
    ordered = [...pick, ...missing];
  } else {
    const cx = (s: SvgShape) => (s.bbox.x0 + s.bbox.x1) / 2;
    const cy = (s: SvgShape) => (s.bbox.y0 + s.bbox.y1) / 2;
    const by: Record<LogoOrder, (a: SvgShape, b: SvgShape) => number> = {
      'left-to-right': (a, b) => a.bbox.x0 - b.bbox.x0 || cy(a) - cy(b),
      'right-to-left': (a, b) => b.bbox.x1 - a.bbox.x1 || cy(a) - cy(b),
      'top-to-bottom': (a, b) => a.bbox.y0 - b.bbox.y0 || cx(a) - cx(b),
      'bottom-to-top': (a, b) => b.bbox.y1 - a.bbox.y1 || cx(a) - cx(b),
      'as-drawn': (a, b) => a.index - b.index,
    };
    ordered = rest.slice().sort(by[order] ?? by['left-to-right']);
    wipe = order === 'right-to-left' ? 'x-' : order === 'top-to-bottom' ? 'y+' : order === 'bottom-to-top' ? 'y-' : 'x+';
  }

  // timing (seconds from the build's start), from the brand's scale by rank unless the spec names tokens
  const t = spec.timing ?? {};
  const broad = durRankSafe(3);
  const base = durRankSafe(2);
  const quick = durRankSafe(1);
  const tick = durRankSafe(0);
  const camera = durRankSafe(4);
  const constructDur = asSeconds(t.construct, broad, d);
  const outlineOn = spec.outline !== false;
  const outlineStart = 0.35 * constructDur;
  const outlineDur = outlineOn ? asSeconds(t.outline, broad, d) : 0;
  const fillStart = outlineOn ? outlineStart + 0.6 * outlineDur : 0.5 * constructDur;
  const fillDur = asSeconds(t.fill, base, d);
  const step = t.stagger !== undefined ? (typeof t.stagger === 'number' ? t.stagger : staggerStepSafe(t.stagger, ordered.length, fillDur)) : staggerStepSafe('block', ordered.length, fillDur);
  const shapesPlanned: PlannedShape[] = parsed.shapes.map((s) => {
    const k = ordered.indexOf(s);
    return {...s, order: k, keystone: s.index === keystone, fillStart: k >= 0 ? fillStart + k * step : 0, fillDur};
  });
  const fillEnd = ordered.length ? fillStart + (ordered.length - 1) * step + fillDur : fillStart;
  let hangStart: number | null = null;
  let dropStart: number | null = null;
  let dropDur: number | null = null;
  let land: number | null = null;
  if (keystone !== null) {
    hangStart = fillStart;
    dropDur = asSeconds(t.drop, quick, d);
    dropStart = fillEnd + 0.5 * quick;
    land = dropStart + dropDur;
  }
  const echoDur = camera;
  const outStart = (land ?? fillEnd) + tick;
  const outDur = t.out !== undefined ? asSeconds(t.out, broad, d) : exitDurSafe(broad);
  const end = Math.max(outStart + outDur, land !== null && spec.impact !== false ? land + echoDur : 0, fillEnd);

  // construction guides and vertex marks
  let guides = {h: [] as number[], v: [] as number[]};
  const g = spec.guides ?? 'edges';
  const maxG = spec.max_guides ?? 8;
  if (g === 'edges') {
    guides = {
      h: thin(dedupe(parsed.shapes.flatMap((s) => [s.bbox.y0, s.bbox.y1]), tol), maxG),
      v: thin(dedupe(parsed.shapes.flatMap((s) => [s.bbox.x0, s.bbox.x1]), tol), maxG),
    };
  } else if (g !== 'none' && typeof g === 'object') guides = {h: (g.h ?? []).slice(), v: (g.v ?? []).slice()};
  let marks: Pt[] = [];
  const vmode = spec.vertices ?? 'corners';
  if (vmode !== 'none') {
    const pts = parsed.shapes.filter((s) => s.index !== keystone).flatMap((s) => (vmode === 'all' ? s.vertices : s.corners));
    const kept: Pt[] = [];
    for (const p of pts) if (!kept.some((q) => Math.abs(q.x - p.x) <= tol && Math.abs(q.y - p.y) <= tol)) kept.push(p);
    marks = thin(kept, spec.max_marks ?? 40);
  }

  const constructEnd = surveyEnd(guides.h.length + guides.v.length, marks.length, constructDur);

  return {
    viewBox: parsed.viewBox,
    box: unionBox(parsed.shapes.map((s) => s.bbox)),
    shapes: shapesPlanned,
    guides,
    marks,
    times: {constructDur, constructEnd, outlineStart, outlineDur, fillStart, fillEnd, hangStart, dropStart, dropDur, land, echoDur, outStart, outDur, end},
    keystone,
    keystoneFrom: spec.keystone_from ?? 'above',
    wipe,
    outline: outlineOn,
    impact: spec.impact !== false,
    problems,
  };
}

// the law is optional here, so a plan can be made (and tested) with no film loaded
function durRankSafe(i: number): number {
  try {
    return durRank(i);
  } catch {
    return [0.2, 0.33, 0.5, 0.8, 1.2, 1.8][Math.max(0, Math.min(5, i))];
  }
}
function staggerStepSafe(unit: string | number, n: number, entrance: number): number {
  try {
    return staggerStep(unit, n, entrance);
  } catch {
    return n > 1 ? Math.min(0.2, 0.6 / (n - 1), (0.5 * entrance) / (n - 1)) : 0;
  }
}
function exitDurSafe(s: number): number {
  try {
    return exitDur(s);
  } catch {
    return 0.75 * s;
  }
}

export type LogoEvent = {shot: string; id: string; t: number; kind: string; size: string; note: string; start?: number; dur?: number};

/**
 * The events a build makes, for the act's events file (draw, land, impact), relative to its start t0. Each draw gives
 * its span (start and dur, film seconds) and its beat t at the span's end, where the stroke makes contact: a draw ends
 * on its beat everywhere in Slate (slate.timing.beats), so the sound engine never has to guess which end was meant.
 */
export function logoEvents(plan: LogoPlan, t0: number, shotId: string, idPrefix = 'logo'): LogoEvent[] {
  const T = plan.times;
  const out: LogoEvent[] = [];
  const draw = (id: string, start: number, dur: number, note: string) => out.push({shot: shotId, id, t: start + dur, kind: 'draw', size: 's', note, start, dur});
  if (T.constructEnd > 0) draw(`${idPrefix}_construct`, t0, T.constructEnd, 'construction lines and vertex marks survey the mark');
  if (plan.outline && T.outlineDur > 0) draw(`${idPrefix}_outline`, t0 + T.outlineStart, T.outlineDur, 'the outlines draw');
  out.push({shot: shotId, id: `${idPrefix}_fill`, t: t0 + T.fillEnd, kind: 'land', size: 'm', note: 'the last part fills'});
  if (T.land !== null) out.push({shot: shotId, id: `${idPrefix}_keystone`, t: t0 + T.land, kind: plan.impact ? 'impact' : 'snap', size: 'l', note: 'the keystone lands'});
  return out;
}
