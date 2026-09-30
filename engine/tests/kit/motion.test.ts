// The motion law bound to a brand, and the motion registry the engine samples into timing/motion/actN.json.
import {afterEach, beforeEach, describe, expect, it} from 'vitest';
import {
  clearMotions,
  defineMotion,
  dur,
  durFrames,
  durRank,
  durationToken,
  ease,
  exitDur,
  landOn,
  law,
  motionLawFrom,
  motionProblems,
  motions,
  roleEase,
  setBrand,
  setFilmData,
  staggerAt,
  staggerStep,
  startFor,
  t90Of,
  traceMotions,
  tw,
} from '@slate/kit/core';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
  clearMotions();
});
afterEach(() => {
  setBrand(null);
  clearMotions();
});

describe("the brand's law", () => {
  it("reads the pack's named eases, durations, staggers and roles", () => {
    const L = law();
    expect(L.neutral).toBe(false);
    expect(L.names).toEqual(['enter', 'move', 'state', 'leave', 'linear']);
    expect(L.durations).toMatchObject({tick: 0.2, base: 0.52, camera: 1.2});
    // staggers written in frames are read at the film's rate (25 fps here)
    expect(L.staggers.char).toBeCloseTo(2 / 25, 12);
    expect(L.staggers.block).toBeCloseTo(6 / 25, 12);
    expect(L.roles).toMatchObject({entrance: 'enter', move: 'move', state: 'state', exit: 'leave'});
    expect(L.problems).toEqual([]);
  });

  it("stands in the kit's neutral law, and says so, for a brand with none", () => {
    const L = motionLawFrom(undefined, 25);
    expect(L.neutral).toBe(true);
    expect(L.problems.join(' ')).toMatch(/no motion law/);
    expect(L.names).toContain('enter');
  });

  it('guesses roles from names when the pack gives none', () => {
    const L = motionLawFrom({eases: {arrive: [0.2, 0.7, 0.2, 1], glide: 'sine', snap: [0.8, 0, 0.2, 1], exit: [0.5, 0, 1, 1]}}, 30);
    expect(L.roles).toMatchObject({entrance: 'arrive', move: 'glide', state: 'snap', exit: 'exit'});
  });

  it('records an ease the pack spells wrongly instead of throwing', () => {
    const L = motionLawFrom({eases: {good: 'sine', bad: 'bouncy'}}, 30);
    expect(L.names).toEqual(['good']);
    expect(L.problems.join(' ')).toMatch(/"bad"/);
  });

  it('records an unknown ease name used in a scene, and still draws', () => {
    expect(ease('wobble', 0.5)).toBeCloseTo(0.5, 12); // the sine fallback
    expect(motionProblems().join(' ')).toMatch(/"wobble" isn't one of the brand's named eases/);
  });

  it('gives exact endpoints through tw()', () => {
    expect(tw(0.99, 1, 0.5, 'enter')).toBe(0);
    expect(tw(1, 1, 0.5, 'enter')).toBe(0);
    expect(tw(1.5, 1, 0.5, 'enter')).toBe(1);
    expect(tw(9, 1, 0.5, 'enter')).toBe(1);
    expect(tw(1.25, 1, 0.5, 'move')).toBeCloseTo(0.5, 12);
  });
});

describe('durations and staggers', () => {
  it("reads durations from the brand's scale, and exits at the brand's factor", () => {
    expect(dur('base')).toBe(0.52);
    expect(dur(0.7)).toBe(0.7);
    expect(durFrames('base')).toBe(13);
    expect(exitDur('base')).toBeCloseTo(0.39, 12);
    expect(durRank(0)).toBe(0.2);
    expect(durRank(4)).toBe(1.2);
    expect(durRank(99)).toBe(1.8);
    expect(durationToken(0.8)).toBe('broad');
    expect(durationToken(0.77)).toBeNull();
  });

  it('records a duration that is not on the scale', () => {
    dur('forever');
    expect(motionProblems().join(' ')).toMatch(/"forever" isn't on the brand's scale/);
  });

  it('caps a group at 0.6 s and at half its entrance', () => {
    // 20 characters at 2 frames (0.08 s) would spread 1.52 s: capped to 0.6 s → 0.6 / 19 each
    expect(staggerStep('char', 20)).toBeCloseTo(0.6 / 19, 12);
    // and to half a 0.52 s entrance: 0.26 / 19
    expect(staggerStep('char', 20, 0.52)).toBeCloseTo(0.26 / 19, 12);
    // few items keep the unit
    expect(staggerStep('char', 3, 0.52)).toBeCloseTo(0.08, 12);
    expect(staggerAt(0, 5, 'word')).toBe(0);
    expect(staggerStep('word', 1)).toBe(0);
  });
});

describe('landing a move on a beat', () => {
  it('starts a move so 90% of its travel lands on the beat', () => {
    const land = 5;
    const s = startFor(land, 'base', 'enter');
    expect(s).toBeCloseTo(land - t90Of('enter') * 0.52, 12);
    expect(tw(land, s, 0.52, 'enter')).toBeCloseTo(0.9, 9);
  });
  it('gives whole frames for defineMotion', () => {
    const f = landOn(5, 'base', 'enter');
    expect(Number.isInteger(f.frame_in)).toBe(true);
    expect(f.frame_out - f.frame_in).toBe(13);
    expect(f.ease).toBe('enter');
    expect(roleEase('exit')).toBe('leave');
  });
});

describe('defineMotion', () => {
  it('returns a pure frame → value function with exact endpoints', () => {
    const m = defineMotion({id: 'card-up', shot: 'K01', prop: 'y', ease: 'enter', frame_in: 10, frame_out: 23, from: 40, to: 0, entrance: true});
    expect(m(0)).toBe(40);
    expect(m(10)).toBe(40);
    expect(m(23)).toBe(0);
    expect(m(99)).toBe(0);
    expect(m(16.5)).toBeGreaterThan(0);
    expect(m(16.5)).toBeLessThan(40);
    // the same frame gives the same value however it's reached (sub-frame samples included)
    expect(m(16.5)).toBe(m(16.5));
    expect(m.atTime(23 / 25)).toBe(0);
    expect(m.spec).toMatchObject({id: 'card-up', shot: 'K01', prop: 'y', ease: 'enter', frame_in: 10, frame_out: 23, entrance: true, loop_period: null, rig: null});
  });

  it('moves lists of values too', () => {
    const m = defineMotion({id: 'pos', shot: 'K02', prop: 'position', ease: 'move', frame_in: 100, frame_out: 130, from: [0, 0], to: [100, -50]});
    expect(m(100)).toEqual([0, 0]);
    const mid = m(115);
    expect(mid[0]).toBeCloseTo(50, 9);
    expect(mid[1]).toBeCloseTo(-25, 9);
    expect(m(130)).toEqual([100, -50]);
  });

  it('registers every move once, and records a clashing redefinition instead of throwing', () => {
    defineMotion({id: 'a', shot: 'K01', prop: 'x', frame_in: 0, frame_out: 10, from: 0, to: 1});
    defineMotion({id: 'a', shot: 'K01', prop: 'x', frame_in: 0, frame_out: 10, from: 0, to: 1});
    expect(motions().length).toBe(1);
    expect(motionProblems()).toEqual([]);
    defineMotion({id: 'a', shot: 'K01', prop: 'x', frame_in: 0, frame_out: 12, from: 0, to: 1});
    expect(motions().length).toBe(1);
    expect(motionProblems().join(' ')).toMatch(/defined twice/);
  });

  it('records reversed frames, unknown shots and mismatched values, and still draws', () => {
    const m = defineMotion({id: 'bad', shot: 'K99', prop: 'x', ease: 'enter', frame_in: 20, frame_out: 10, from: [0, 1], to: [1] as unknown as number[]});
    expect(() => m(15)).not.toThrow();
    const p = motionProblems().join(' ');
    expect(p).toMatch(/swapped/);
    expect(p).toMatch(/K99/);
    expect(p).toMatch(/same length/);
  });

  it('loops a spin between frame_in and frame_out', () => {
    const spin = defineMotion({id: 'spin', shot: 'K01', prop: 'rotate', ease: 'linear', frame_in: 0, frame_out: 64, from: 0, to: 360, loop_period: 32});
    expect(spin(0)).toBe(0);
    expect(spin(16)).toBeCloseTo(180, 9);
    expect(spin(48)).toBeCloseTo(180, 9);
    expect(spin(70)).toBe(0);
  });

  it('samples every whole frame of an act into the motion trace', () => {
    defineMotion({id: 'in1', shot: 'K01', prop: 'opacity', ease: 'enter', frame_in: 5, frame_out: 18, from: 0, to: 1, entrance: true});
    defineMotion({id: 'in3', shot: 'K03', prop: 'x', ease: 'move', frame_in: 230, frame_out: 260, from: 0, to: 300});
    const act1 = traceMotions({act: 1});
    expect(act1.map((e) => e.id)).toEqual(['in1']);
    const e = act1[0];
    expect(e.samples.length).toBe(14);
    expect(e.samples[0]).toEqual([5, 0]);
    expect(e.samples[13]).toEqual([18, 1]);
    // the contract's keys, exactly (docs/ARCHITECTURE.md §12)
    expect(Object.keys(e).sort()).toEqual(['ease', 'entrance', 'frame_in', 'frame_out', 'from', 'id', 'loop_period', 'prop', 'rig', 'samples', 'shot', 'to'].sort());
    expect(traceMotions({act: 2}).map((x) => x.id)).toEqual(['in3']);
    expect(traceMotions().length).toBe(2);
  });
});
