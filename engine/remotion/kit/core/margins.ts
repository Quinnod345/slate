// Timing margins: the slack, in seconds, between two beats the picture depends on ("the card has landed before the
// word that names it", "the camera has settled before the chat goes"). Every beat is keyed to a word, so after a
// re-voice a margin can go negative without anything else breaking: the hand-in check lists every margin and fails
// while one is negative, and emit-events refuses to write sound events in that state (the films' L-timing-D039).
//
//   // beats.ts, at module level: the card lands before "report" is said, with a tenth of a second to spare
//   margin('the card has landed before "report"', CARD_LAND, word('S03', 'report'), 0.1);
//
// React-free, so the engine reads the registry when it loads an act in Node.

export type Margin = {
  /** what the margin protects, in words a person can act on */
  name: string;
  /** seconds of slack: later − earlier − min; negative is broken */
  slack: number;
  /** the beats it sits between (film seconds), when given */
  earlier?: number;
  later?: number;
  /** the least room asked for between them (seconds) */
  min?: number;
  shot?: string;
};

const registry: Margin[] = [];

/**
 * Record a margin: `later` must come at least `min` seconds after `earlier` (film seconds, both from the act's own
 * beats). Returns the slack (later − earlier − min), so a scene can also shorten a span that would overrun.
 */
export function margin(name: string, earlier: number, later: number, min = 0, where: {shot?: string} = {}): number {
  const slack = later - earlier - min;
  registry.push({name, slack, earlier, later, min, ...(where.shot ? {shot: where.shot} : {})});
  return slack;
}

/** Record a margin whose slack the act worked out itself (seconds; negative is broken). */
export function defineMargin(m: Margin): void {
  registry.push({...m});
}

/** Every margin recorded so far, in the order they were recorded. */
export function margins(): Margin[] {
  return registry.map((m) => ({...m}));
}

/** The margins an act recorded (the engine loads one act's modules at a time, so that's all of them). */
export function actMargins(_act?: number | string): Margin[] {
  return margins();
}

/** The margins that are broken: negative, or not a number at all. */
export function brokenMargins(list: Margin[] = margins()): Margin[] {
  return list.filter((m) => !(typeof m.slack === 'number' && Number.isFinite(m.slack) && m.slack >= 0));
}

export function clearMargins(): void {
  registry.length = 0;
}
