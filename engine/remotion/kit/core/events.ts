// Visual events: every beat that wants a sound, defined once next to the picture that makes it.
//
// An act's beats module defines its events at module level (defineEvent), from the same word timings the picture reads,
// so a chip's flip, its sound and its word land on one frame. The engine's `slate emit-events` bundles the beats module,
// collects the registry and writes timing/visual_events/actN.json (docs/ARCHITECTURE.md §5): one command instead of
// the films' 22 hand-written emitters. validateEvents() holds the rules every act's file must meet.
import {actShots, actSpan, fps, secOf, shot} from './film';

/** The kinds a sound designer can map (docs/ARCHITECTURE.md §5). */
export const EVENT_KINDS = ['tap', 'type', 'land', 'snap', 'count', 'draw', 'data', 'page', 'stamp', 'whoosh', 'swell', 'character', 'impact'] as const;
export type EventKind = (typeof EVENT_KINDS)[number];
export const EVENT_SIZES = ['s', 'm', 'l'] as const;
export type EventSize = (typeof EVENT_SIZES)[number];

export type VisualEvent = {shot: string; id: string; t: number; kind: EventKind; size: EventSize; note: string; [extra: string]: unknown};

export type EventInput = {
  shot: string;
  id: string;
  /** film seconds of the landing (the moment the sound belongs to) */
  t?: number;
  /** or a film frame */
  frame?: number;
  kind: EventKind | string;
  size?: EventSize | string;
  note?: string;
  [extra: string]: unknown;
};

const registry = new Map<string, VisualEvent>();
const problems = new Set<string>();

/**
 * Define a visual event at module level. Returns its film time, so the picture can use the same number. Never throws:
 * a bad kind, size or duplicate id is recorded (eventProblems()) for the hand-in check.
 */
export function defineEvent(input: EventInput): number {
  const {shot: shotId, id, t, frame, kind, size = 'm', note = '', ...extra} = input;
  let time = typeof t === 'number' ? t : typeof frame === 'number' ? secOf(frame) : NaN;
  if (!Number.isFinite(time)) {
    problems.add(`event "${id}" in ${shotId} has no time (give t in film seconds or a frame)`);
    time = shot(shotId).start;
  }
  const ev: VisualEvent = {shot: shotId, id, t: time, kind: kind as EventKind, size: size as EventSize, note, ...extra};
  const key = `${shotId}/${id}`;
  const prev = registry.get(key);
  if (prev && JSON.stringify(prev) !== JSON.stringify(ev)) problems.add(`event "${id}" in ${shotId} is defined twice with different values (the first stands)`);
  if (!prev) registry.set(key, ev);
  return time;
}

/** Every defined event, in definition order. */
export function events(): VisualEvent[] {
  return [...registry.values()];
}

/** Forget every event (tests, collectors). */
export function clearEvents(): void {
  registry.clear();
  problems.clear();
}

/** What defineEvent had to record. */
export function eventProblems(): string[] {
  return [...problems];
}

/**
 * The rules an act's events file meets: known kinds and sizes, unique ids, every event inside the act's span (to the
 * frame), on a shot of that act, and at most one impact (the act's one signature landing).
 */
export function validateEvents(list: readonly VisualEvent[], act: number | string): string[] {
  const out: string[] = [];
  const span = actSpan(act);
  const shots = new Set(actShots(act).map((s) => s.id));
  const seen = new Set<string>();
  const half = 0.5 / fps();
  let impacts = 0;
  for (const e of list) {
    if (!EVENT_KINDS.includes(e.kind)) out.push(`${e.id}: kind "${e.kind}" isn't one of ${EVENT_KINDS.join(', ')}`);
    if (!EVENT_SIZES.includes(e.size)) out.push(`${e.id}: size "${e.size}" isn't s, m or l`);
    if (seen.has(e.id)) out.push(`${e.id}: the id is used twice in act ${act}`);
    seen.add(e.id);
    if (!shots.has(e.shot)) out.push(`${e.id}: shot ${e.shot} isn't in act ${act}`);
    if (span && (e.t < span.start - half || e.t > span.end + half)) out.push(`${e.id}: t ${e.t.toFixed(3)} s is outside act ${act} (${span.start.toFixed(3)}–${span.end.toFixed(3)} s)`);
    if (!Number.isFinite(e.t)) out.push(`${e.id}: t isn't a number`);
    if (e.kind === 'impact') impacts++;
  }
  if (impacts > 1) out.push(`act ${act} has ${impacts} impacts; keep one (the act's signature landing)`);
  return out;
}

/**
 * An act's events file: its events sorted by time, or `{silent: true, reason}` when the act has none on purpose. An act
 * with no events and no reason is an error the caller reports (`slate qa` fails "events complete" on it).
 */
export function actEvents(act: number | string, silentReason?: string): VisualEvent[] | {silent: true; reason: string} {
  const shots = new Set(actShots(act).map((s) => s.id));
  const list = events()
    .filter((e) => shots.has(e.shot))
    .sort((a, b) => a.t - b.t || a.id.localeCompare(b.id));
  if (!list.length && silentReason) return {silent: true, reason: silentReason};
  return list;
}
