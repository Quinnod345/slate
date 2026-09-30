// The film clock: every number from @film/data, lookups that never throw, misses that are recorded.
import {afterEach, beforeEach, describe, expect, it} from 'vitest';
import {
  actShots,
  actSpan,
  acts,
  clearMisses,
  contactAt,
  findOccurrences,
  fps,
  fpsRational,
  frameOf,
  frameSize,
  missLabels,
  misses,
  norm,
  roundHalfEven,
  secOf,
  setFilmData,
  shot,
  shotAt,
  spanFrames,
  timecode,
  voLead,
  voSpan,
  word,
  wordTime,
} from '@slate/kit/core';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
});
afterEach(() => {
  setFilmData(FILM as never);
});

describe('rate and size come from the film data', () => {
  it('reads fps and size from film.json', () => {
    expect(fpsRational()).toEqual({num: 25, den: 1});
    expect(fps()).toBe(25);
    expect(frameSize()).toEqual({width: 960, height: 540, short: 540, long: 960});
  });

  it('follows the data when it changes (a vertical film at 30000/1001)', () => {
    setFilmData({...FILM, film: {...FILM.film, fps_num: 30000, fps_den: 1001, width: 1080, height: 1920}} as never);
    expect(fps()).toBeCloseTo(29.97002997, 8);
    expect(frameSize().short).toBe(1080);
    expect(frameSize().long).toBe(1920);
    // a shot starts at frame_in × den ÷ num, exactly as slate.clock.Clock.secs_of
    expect(shot('K02').start).toBe((100 * 1001) / 30000);
  });

  it('refuses clearly when the data has no rate', () => {
    setFilmData({film: {id: 'x'}, shots: {shots: []}} as never);
    expect(() => fps()).toThrow(/no frame rate/);
    expect(() => frameSize()).toThrow(/no frame size/);
  });
});

describe('frame maths matches Python', () => {
  it('rounds halves to even like round()', () => {
    expect([0.5, 1.5, 2.5, 3.5, -0.5, -1.5, -2.5, 2.4999, 2.5001].map(roundHalfEven)).toEqual([0, 2, 2, 4, -0, -2, -2, 2, 3]);
  });
  it('frameOf and secOf are inverses on whole frames', () => {
    for (const f of [0, 1, 99, 100, 399]) expect(frameOf(secOf(f))).toBe(f);
    // 0.1 s × 25 = 2.5 frames: Python's round gives 2
    expect(frameOf(0.1)).toBe(2);
    expect(frameOf(0.14)).toBe(4); // 3.5 → 4
  });
  it('timecode counts on the nominal rate', () => {
    expect(timecode(0)).toBe('00:00:00:00');
    expect(timecode(25 * 61 + 7)).toBe('00:01:01:07');
  });
});

describe('shots and acts', () => {
  it('gives a shot its span with frame_out exclusive', () => {
    const s = shot('K02');
    expect(s).toMatchObject({frameIn: 100, frameOut: 225, frames: 125, start: 4, end: 9, dur: 5, act: 1, missing: false});
    expect(spanFrames('K01', 'K02')).toBe(225);
  });
  it('knows its acts', () => {
    expect(acts()).toEqual([1, 2]);
    expect(actShots(2).map((s) => s.id)).toEqual(['K03', 'K04']);
    expect(actSpan(2)).toMatchObject({first: 'K03', last: 'K04', frameIn: 225, frameOut: 400, start: 9, end: 16});
    expect(actSpan(9)).toBeNull();
  });
  it('finds the shot a time falls in', () => {
    expect(shotAt(0)?.id).toBe('K01');
    expect(shotAt(4)?.id).toBe('K02');
    expect(shotAt(8.99)?.id).toBe('K02');
    expect(shotAt(99)?.id).toBe('K04');
  });
  it('never throws on an unknown shot, and records it', () => {
    clearMisses();
    const s = shot('ZZ9');
    expect(s.missing).toBe(true);
    expect(s.start).toBe(0);
    expect(missLabels()).toContain('ZZ9 (no such shot)');
  });
});

describe('word lookups never throw', () => {
  it('finds a word: shot start + cue + word start', () => {
    clearMisses();
    // K01 starts at 0 s, its read at 0.4 s, "lands" at 0.64 s into the take
    expect(word('K01', 'lands')).toBeCloseTo(1.04, 12);
    expect(word('K01', 'lands', 1, 'end')).toBeCloseTo(1.4, 12);
    expect(wordTime('K01', 'word')).toEqual([0.4 + 1.34, true]); // punctuation in the transcript doesn't matter
    expect(misses()).toEqual([]);
  });

  it('matches a number said as two words through the spoken forms in the data', () => {
    clearMisses();
    // K02 at 4 s, cue 0.3: "Sixty" starts at 0, "four" ends at 0.6
    expect(word('K02', '64')).toBeCloseTo(4.3, 12);
    expect(word('K02', '64', 1, 'end')).toBeCloseTo(4.9, 12);
    expect(misses()).toEqual([]);
  });

  it('counts repeats, case and punctuation folded', () => {
    clearMisses();
    const base = 9 + 0.3;
    expect(word('K03', 'nothing', 1)).toBeCloseTo(base + 0.0, 12);
    expect(word('K03', 'Nothing', 2)).toBeCloseTo(base + 1.46, 12);
    expect(word('K03', 'nothing', 3)).toBeCloseTo(base + 3.2, 12);
    expect(misses()).toEqual([]);
  });

  it('estimates and records a miss instead of throwing', () => {
    clearMisses();
    const [t, exact] = wordTime('K01', 'nowhere');
    expect(exact).toBe(false);
    const span = voSpan('K01');
    expect(t).toBeGreaterThanOrEqual(span.start);
    expect(t).toBeLessThanOrEqual(span.end);
    expect(missLabels()).toEqual(['K01:nowhere']);
    // a fourth "nothing" doesn't exist: estimated, labelled with its occurrence
    word('K03', 'nothing', 4);
    expect(missLabels()).toContain('K03:nothing#4');
  });

  it('estimates a word from its place in the line when the read has no timings yet', () => {
    clearMisses();
    // K04 has no cue and no words: the lookup still answers, inside the shot, and both misses are recorded
    const t = word('K04', 'card');
    const s = shot('K04');
    expect(t).toBeGreaterThanOrEqual(s.start);
    expect(t).toBeLessThanOrEqual(s.end);
    const kinds = misses().map((m) => m.kind);
    expect(kinds).toContain('cue');
    expect(kinds).toContain('word');
    expect(kinds).toContain('vo');
    // with no cue, the cue sheet's lead stands in
    expect(voLead('K04')).toBe(0.3);
  });

  it('never throws for an unknown shot either', () => {
    clearMisses();
    expect(() => word('NOPE', 'x')).not.toThrow();
    expect(missLabels()).toContain('NOPE (no such shot)');
  });

  it('normalises any script', () => {
    expect(norm('Café,')).toBe('café');
    expect(norm('ÜBER!')).toBe('über');
    expect(norm('54%')).toBe('54%');
    expect(norm('日本語。')).toBe('日本語');
  });

  it('matches a split or merged form both ways', () => {
    const words = [{w: 'sixty-four'}, {w: 'parts'}];
    expect(findOccurrences(words, '64')).toEqual([[0, 0]]);
    const split = [{w: 'Sixty'}, {w: 'four'}, {w: 'parts'}];
    expect(findOccurrences(split, '64')).toEqual([[0, 1]]);
  });

  it('puts a contact frame on a whole frame, two frames after the stress', () => {
    const c = contactAt(1.03);
    expect(c * 25).toBeCloseTo(Math.round(1.03 * 25) + 2, 9);
  });
});
