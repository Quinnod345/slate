// The looks library and captions.
import {beforeEach, describe, expect, it} from 'vitest';
import {AE_LOOKS, DEFAULT_LOOK, LOOKS, breakLines, captions, cueAt, cueCps, listLooks, look, lookOf, normalizeCaptions, sceneEngine, setBrand, setFilmData} from '@slate/kit/core';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

describe('looks', () => {
  it('has paper 2.5D, flat and more, each a full bundle of defaults', () => {
    expect(Object.keys(LOOKS)).toEqual(expect.arrayContaining(['paper-2.5d', 'flat', 'editorial', 'blueprint']));
    expect(LOOKS['paper-2.5d'].papery).toBe(true);
    expect(LOOKS.flat.papery).toBe(false);
    for (const l of Object.values(LOOKS)) {
      expect(l.engine).toBe('remotion');
      expect(l.card.layers).toBeGreaterThanOrEqual(0);
      expect(l.summary.length).toBeGreaterThan(20);
    }
  });

  it("lists the AE bridge's thirteen looks by name only", () => {
    expect(AE_LOOKS.length).toBe(13);
    expect(AE_LOOKS.map((l) => l.id)).toEqual(expect.arrayContaining(['ae-depth-space', 'ae-transition-kit', 'ae-ui-mastery', 'ae-matte-painting']));
    expect(AE_LOOKS.every((l) => l.engine === 'ae' && l.builtBy === 'ae')).toBe(true);
    expect(listLooks().remotion.length).toBe(Object.keys(LOOKS).length);
  });

  it('gives a brand the look it chose, with its overrides', () => {
    expect(look().id).toBe('flat');
    const chosen = lookOf({look: {id: 'paper-2.5d', overrides: {card: {layers: 3}}}});
    expect(chosen.look.id).toBe('paper-2.5d');
    expect(chosen.look.card.layers).toBe(3);
    expect(chosen.look.card.radius).toBe(LOOKS['paper-2.5d'].card.radius);
  });

  it('defaults to flat (paper is only for papery brands) and says why', () => {
    expect(DEFAULT_LOOK).toBe('flat');
    const none = lookOf({});
    expect(none.look.id).toBe('flat');
    expect(none.problems.join(' ')).toMatch(/hasn't chosen a look/);
    const unknown = lookOf({look: 'neon'});
    expect(unknown.problems.join(' ')).toMatch(/isn't in the library/);
    const ae = lookOf({look: 'ae-depth-space'});
    expect(ae.ae?.id).toBe('ae-depth-space');
    expect(ae.look.id).toBe('flat');
  });

  it('lets a brand build its scenes in the open web renderer (look.engine), remotion by default', () => {
    expect(lookOf({look: {id: 'flat'}}).engine).toBe('remotion');
    const web = lookOf({look: {id: 'editorial', engine: 'web'}});
    expect(web.engine).toBe('web');
    expect(web.look.id).toBe('editorial');
    expect(web.problems).toEqual([]);
    expect(lookOf({look: 'ae-depth-space'}).engine).toBe('ae');
    const odd = lookOf({look: {id: 'flat', engine: 'blender'}});
    expect(odd.engine).toBe('remotion');
    expect(odd.problems.join(' ')).toMatch(/look.engine "blender" isn't an engine/);
    expect(lookOf({look: {id: 'flat', engine: 'ae'}}).problems.join(' ')).toMatch(/kit look/);
    expect(sceneEngine({look: {id: 'blueprint', engine: 'web'}})).toBe('web');
  });
});

describe('captions', () => {
  it("reads the films' list shape and keeps its lines", () => {
    const {cues, problems} = captions();
    expect(problems).toEqual([]);
    expect(cues.length).toBe(3);
    expect(cues[1].lines).toEqual(['Sixty-four parts move', 'on one clock.']);
    // a cue with no lines gets balanced lines of at most 42 characters
    expect(cues[2].lines.length).toBe(2);
    expect(cues[2].lines.every((l) => l.length <= 42)).toBe(true);
  });

  it('reads a wrapped shape and frames', () => {
    const {cues} = normalizeCaptions({cues: [{frame_in: 25, frame_out: 50, text: 'One second'}]});
    expect(cues[0]).toMatchObject({start: 1, end: 2, text: 'One second'});
  });

  it('lists broken cues instead of throwing', () => {
    const {cues, problems} = normalizeCaptions([{start: 2, end: 1, text: 'x'}, {text: 'no times'}]);
    expect(cues).toEqual([]);
    expect(problems.length).toBe(2);
  });

  it('finds the cue on screen and its reading speed', () => {
    const {cues} = captions();
    expect(cueAt(cues, 1)?.shot).toBe('K01');
    expect(cueAt(cues, 3)).toBeNull();
    expect(cueCps(cues[0])).toBeCloseTo(29 / 2, 6);
  });

  it('breaks lines at spaces, balanced, at most two of 42', () => {
    const lines = breakLines('Every move lands on its word and every sound lands with it too.');
    expect(lines.length).toBe(2);
    expect(Math.abs(lines[0].length - lines[1].length)).toBeLessThan(12);
    expect(breakLines('short')).toEqual(['short']);
  });
});
