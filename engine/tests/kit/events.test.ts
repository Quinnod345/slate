// Visual events: the kit side of `slate emit-events`.
import {beforeEach, describe, expect, it} from 'vitest';
import {EVENT_KINDS, actEvents, clearEvents, defineEvent, eventProblems, events, setFilmData, spanProblems, validateEvents, word} from '@slate/kit/core';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  clearEvents();
});

describe('defineEvent', () => {
  it('records a beat at its landing and returns the time', () => {
    const t = defineEvent({shot: 'K01', id: 'title_lands', t: word('K01', 'lands'), kind: 'type', size: 'l', note: 'the title lands on its word'});
    expect(t).toBeCloseTo(1.04, 12);
    expect(events()).toEqual([{shot: 'K01', id: 'title_lands', t, kind: 'type', size: 'l', note: 'the title lands on its word'}]);
  });

  it('takes a frame instead of a time', () => {
    const t = defineEvent({shot: 'K02', id: 'tick', frame: 150, kind: 'tap', size: 's'});
    expect(t).toBe(6);
  });

  it('keeps extra fields (from, to, pan…) for the sound engine', () => {
    defineEvent({shot: 'K02', id: 'carry', t: 5, kind: 'whoosh', size: 'm', note: '', from: 'left', to: 'right'});
    expect(events()[0]).toMatchObject({from: 'left', to: 'right'});
  });

  it('records a clashing redefinition instead of throwing', () => {
    defineEvent({shot: 'K01', id: 'x', t: 1, kind: 'tap'});
    defineEvent({shot: 'K01', id: 'x', t: 2, kind: 'tap'});
    expect(events().length).toBe(1);
    expect(eventProblems().join(' ')).toMatch(/defined twice/);
  });
});

describe('the rules for an act file', () => {
  it('passes a clean act', () => {
    defineEvent({shot: 'K01', id: 'a', t: 1, kind: 'type', size: 'l'});
    defineEvent({shot: 'K02', id: 'b', t: 5, kind: 'impact', size: 'l'});
    const list = actEvents(1) as never[];
    expect(list.length).toBe(2);
    expect(validateEvents(list, 1)).toEqual([]);
  });

  it('catches unknown kinds and sizes, duplicates, out-of-span times and a second impact', () => {
    const bad = [
      {shot: 'K01', id: 'a', t: 1, kind: 'boom', size: 'xl', note: ''},
      {shot: 'K01', id: 'a', t: 2, kind: 'impact', size: 'l', note: ''},
      {shot: 'K02', id: 'c', t: 42, kind: 'impact', size: 'l', note: ''},
      {shot: 'K03', id: 'd', t: 10, kind: 'tap', size: 's', note: ''},
    ] as never;
    const problems = validateEvents(bad, 1).join('\n');
    expect(problems).toMatch(/kind "boom"/);
    expect(problems).toMatch(/size "xl"/);
    expect(problems).toMatch(/a: the id is used twice/);
    expect(problems).toMatch(/c: t 42\.000 s is outside act 1/);
    expect(problems).toMatch(/d: shot K03 isn't in act 1/);
    expect(problems).toMatch(/2 impacts/);
  });

  it('says silent with a reason when an act has no beats on purpose', () => {
    expect(actEvents(2, 'a held title card: no sound on purpose')).toEqual({silent: true, reason: 'a held title card: no sound on purpose'});
    expect(actEvents(2)).toEqual([]);
  });

  it('knows the thirteen kinds', () => {
    expect(EVENT_KINDS.length).toBe(13);
    expect(EVENT_KINDS).toContain('character');
  });
});

describe('spans: t is always the beat (slate.timing.beats)', () => {
  it('works out a draw\'s beat from its span: it ends on t', () => {
    const t = defineEvent({shot: 'K01', id: 'rule', start: 1, dur: 0.4, kind: 'draw', size: 's', note: 'a rule draws'});
    expect(t).toBeCloseTo(1.4, 12);
    expect(events()[0]).toMatchObject({start: 1, dur: 0.4, t});
    expect(eventProblems()).toEqual([]);
    expect(validateEvents(actEvents(1) as never, 1)).toEqual([]);
  });

  it('holds draws, counts and peaks to their spans, word for word as the Python rules do', () => {
    const list = [
      {shot: 'K01', id: 'no_span', t: 1, kind: 'draw', size: 's', note: ''},
      {shot: 'K01', id: 'starts_on_t', t: 1, start: 1, dur: 0.5, kind: 'count', size: 's', note: ''},
      {shot: 'K01', id: 'half', t: 1, dur: 0.5, kind: 'tap', size: 's', note: ''},
      {shot: 'K01', id: 'late', t: 2, start: 1, dur: 0.5, kind: 'whoosh', size: 's', note: ''},
      {shot: 'K01', id: 'fine', t: 1.5, start: 1, dur: 0.5, kind: 'draw', size: 's', note: ''},
      {shot: 'K01', id: 'inside', t: 1.2, start: 1, dur: 0.5, kind: 'swell', size: 's', note: ''},
    ];
    const problems = spanProblems(list, 25);
    expect(problems).toEqual([
      'beat 1 (no_span): a draw runs over time, so it needs start and dur (it ends on t)',
      'beat 2 (starts_on_t): a count ends on its beat, but start + dur is 1.500 s and t is 1.000 s (+500 ms)',
      'beat 3 (half): start and dur go together (the span a sustained motion runs over)',
      'beat 4 (late): a whoosh peaks inside its span, but t 2.000 s is outside 1.000–1.500 s',
    ]);
    // half a frame of slack at the film's rate, two milliseconds without one
    expect(spanProblems([{id: 'x', kind: 'draw', t: 1.519, start: 1, dur: 0.5}], 25)).toEqual([]);
    expect(spanProblems([{id: 'x', kind: 'draw', t: 1.519, start: 1, dur: 0.5}])).toHaveLength(1);
  });
});
