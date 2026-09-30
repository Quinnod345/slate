// Visual events: the kit side of `slate emit-events`.
import {beforeEach, describe, expect, it} from 'vitest';
import {EVENT_KINDS, actEvents, clearEvents, defineEvent, eventProblems, events, setFilmData, validateEvents, word} from '@slate/kit/core';
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
