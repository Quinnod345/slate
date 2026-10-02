// The motion law bound to a brand, and the motion registry the engine samples into timing/motion/actN.json.
import {afterEach, beforeEach, describe, expect, it} from 'vitest';
import {
  SPEED_LIMITS,
  clearMotions,
  defineMotion,
  dur,
  durFit,
  durFrames,
  durRank,
  durationToken,
  ease,
  exitDur,
  landOn,
  law,
  maxTravel,
  motionLawFrom,
  motionProblems,
  motions,
  roleEase,
  setBrand,
  setFilmData,
  speedLimits,
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

  it('fits a move to the room it has with the longest step of the scale that fits, never a step off the scale', () => {
    // the scale at 25 fps: tick 5 frames, quick 8, base 13, broad 20, camera 30, signature 45
    expect(durFit(5)).toBe(1.8); // room for every step: the longest
    expect(durFit(0.4)).toBe(0.32); // 10 frames: quick fits, base doesn't
    expect(durFit(0.5)).toBe(0.32); // 12.5 frames round to 12 (Python's rounding), still short of base's 13
    expect(durFit(0.2)).toBe(0.2); // exactly a tick
    expect(durFit(0.1)).toBe(0.2); // no room for any step: the shortest, so the move stays on the scale
    // a step that fits comes back exactly as it went in, so an act with room draws what it drew before
    for (let i = 0; i < 6; i++) expect(durFit(durRank(i))).toBe(durRank(i));
    expect(motionProblems()).toEqual([]);
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

/** A move's peak speed and its peak change in speed (px a frame, px a frame²), measured the way `slate qa motion`
 * measures a trace: its samples on whole frames, held at rest the frame before and the frame after. */
function peaks(id: string): {speed: number; accel: number} {
  const row = traceMotions().find((r) => r.id === id)!;
  const at = [row.samples[0][1] as number, ...row.samples.map(([, v]) => v as number), row.samples.at(-1)![1] as number];
  const steps = at.slice(1).map((v, k) => v - at[k]);
  return {
    speed: Math.max(...steps.map(Math.abs)),
    accel: Math.max(...steps.slice(1).map((v, k) => Math.abs(v - steps[k]))),
  };
}

describe('the speed limits a move keeps', () => {
  const sized = (width: number, height: number) =>
    setFilmData({...FILM, film: {...FILM.film, width, height}, shots: {...FILM.shots, width, height}} as never);

  it("holds each class to the motion gate's limits, scaled by the frame's short side", () => {
    // 960 x 540: half of 1080p's rig 30 and 8, UI 60 and 16, carry 170 and 45
    expect(speedLimits('rig').speed).toBeCloseTo(15, 9);
    expect(speedLimits('rig').accel).toBeCloseTo(4, 9);
    expect(speedLimits().speed).toBeCloseTo(30, 9); // a move that doesn't say is a UI element
    expect(speedLimits('ui').accel).toBeCloseTo(8, 9);
    expect(speedLimits('carry').speed).toBeCloseTo(85, 9);
    expect(speedLimits('page')).toEqual(speedLimits('carry')); // the gate's other names for the classes
    expect(speedLimits('whip')).toEqual(speedLimits('carry')); // past a carry's limits, a person decides
    sized(1920, 1080);
    expect(speedLimits('ui').speed).toBeCloseTo(60, 9);
    expect(speedLimits('ui').accel).toBeCloseTo(16, 9);
    sized(1080, 1920); // a 9:16 reel keeps 1080p's numbers: its short side is 1080
    expect(speedLimits('ui').speed).toBeCloseTo(60, 9);
    expect(SPEED_LIMITS.carry.accel).toBeCloseTo(17 / 405, 15);
    expect(motionProblems()).toEqual([]);
  });

  it("takes the brand's own limits where its motion data sets them, as fractions or as px on the short side it names", () => {
    const motion = {...(FILM.brand as {motion: object}).motion, speed_limits: {ui: {speed: 40, accel: 10, short_side: 1080}, carry: {speed: 0.1}, whip: {speed: 0.5}, rig: {speed: 30}}};
    setBrand({...(FILM.brand as object), motion});
    expect(law().speedLimits).toEqual({ui: {speed: 40 / 1080, accel: 10 / 1080}, carry: {speed: 0.1}}); // a whip has none; px need a short side
    expect(speedLimits('ui').speed).toBeCloseTo(20, 9); // 40 px at 1080, at 540
    expect(speedLimits('ui').accel).toBeCloseTo(5, 9);
    expect(speedLimits('carry').speed).toBeCloseTo(54, 9);
    expect(speedLimits('carry').accel).toBeCloseTo(540 * (17 / 405), 9); // what the brand didn't set stays Slate's
    expect(speedLimits('rig').speed).toBeCloseTo(15, 9);
  });

  it('records a class the gate would not know, and holds it to a UI element', () => {
    expect(speedLimits('rocket')).toEqual(speedLimits('ui'));
    expect(motionProblems().join(' ')).toMatch(/speed limits for class "rocket": it isn't one of rig, ui, carry, whip/);
  });

  it('gives the longest travel a move can make and keep the limits, measured as the gate measures a trace', () => {
    for (const [token, name, cls] of [['tick', 'enter', 'ui'], ['base', 'enter', 'ui'], ['quick', 'move', 'ui'], ['tick', 'move', 'carry'], ['broad', 'state', 'rig']] as const) {
      clearMotions();
      const most = maxTravel(token, name, cls);
      const {speed, accel} = speedLimits(cls);
      defineMotion({id: 'at', shot: 'K01', prop: 'y', ease: name, frame_in: 0, frame_out: durFrames(token), from: most, to: 0, ...(cls === 'ui' ? {} : {class: cls})});
      defineMotion({id: 'past', shot: 'K01', prop: 'y', ease: name, frame_in: 0, frame_out: durFrames(token), from: most * 1.01, to: 0});
      const at = peaks('at');
      const past = peaks('past');
      expect(at.speed).toBeLessThanOrEqual(speed);
      expect(at.accel).toBeLessThanOrEqual(accel);
      expect(Math.max(at.speed / speed, at.accel / accel)).toBeGreaterThan(0.999); // right up against one of them
      expect(past.speed > speed || past.accel > accel).toBe(true);
    }
    expect(motionProblems()).toEqual([]);
  });

  it("starts from the brand's entrance ease, grows with the move's length and the frame, and is generous to a carry", () => {
    expect(maxTravel('tick')).toBe(maxTravel('tick', roleEase('entrance')));
    expect(maxTravel('base')).toBeGreaterThan(maxTravel('tick'));
    expect(maxTravel('tick', 'move', 'carry')).toBeGreaterThan(maxTravel('tick', 'move', 'ui'));
    expect(maxTravel('tick', 'move', 'rig')).toBeLessThan(maxTravel('tick', 'move', 'ui'));
    const small = maxTravel('tick');
    sized(1920, 1080);
    expect(maxTravel('tick')).toBeCloseTo(small * 2, 9);
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

describe('what the motion gate needs to know about a move (class, exempt, reason)', () => {
  it('writes a declared class and exemption into the trace, and nothing for a move that declares none', () => {
    defineMotion({id: 'page.carry', shot: 'K01', prop: 'x', ease: 'even', frame_in: 0, frame_out: 20, from: 0, to: 600, class: 'page'});
    defineMotion({id: 'sweep', shot: 'K01', prop: 'x', ease: 'linear', frame_in: 0, frame_out: 20, from: 0, to: 100, exempt: 'Reading Sweep', reason: 'items resolve at a steady pace'});
    defineMotion({id: 'plain', shot: 'K01', prop: 'opacity', ease: 'enter', frame_in: 0, frame_out: 10, from: 0, to: 1});
    const rows = Object.fromEntries(traceMotions({act: 1}).map((r) => [r.id, r]));
    expect(rows['page.carry']).toMatchObject({class: 'carry', ease: 'even'});
    expect(rows.sweep).toMatchObject({exempt: 'reading_sweep', reason: 'items resolve at a steady pace'});
    expect('class' in rows.plain || 'exempt' in rows.plain).toBe(false);
    // "even" is the kit's own curve: no problem recorded for using it
    expect(motionProblems().join(' ')).not.toMatch(/even/);
  });

  it('calls a rig part rig and the camera a carry without being told, and refuses a class the gate wouldn\'t know', () => {
    defineMotion({id: 'mark.lean', shot: 'K01', prop: 'rotate', ease: 'move', frame_in: 0, frame_out: 12, from: 0, to: 4, rig: 'mark'});
    defineMotion({id: 'camera.x', shot: 'K01', prop: 'x', ease: 'move', frame_in: 0, frame_out: 12, from: 0, to: 40});
    defineMotion({id: 'odd', shot: 'K01', prop: 'x', ease: 'move', frame_in: 0, frame_out: 12, from: 0, to: 40, class: 'fast'});
    const rows = Object.fromEntries(traceMotions({act: 1}).map((r) => [r.id, r]));
    expect(rows['mark.lean'].class).toBe('rig');
    expect(rows['camera.x'].class).toBe('carry');
    expect('class' in rows.odd).toBe(false);
    expect(motionProblems().join(' ')).toMatch(/motion "odd": class "fast" isn't one of rig, ui, carry, whip/);
  });
});

describe('type in the trace (role and text)', () => {
  it("writes a text element's type role and words, so the hero, settled and legibility checks can see it", () => {
    defineMotion({id: 'title.rise', shot: 'K01', prop: 'y', ease: 'enter', frame_in: 0, frame_out: 12, from: 24, to: 0, entrance: true, role: 'Hero', text: '  Every  frame\n holds  '});
    defineMotion({id: 'card.x', shot: 'K01', prop: 'x', ease: 'move', frame_in: 0, frame_out: 12, from: 0, to: 40});
    const rows = Object.fromEntries(traceMotions({act: 1}).map((r) => [r.id, r]));
    expect(rows['title.rise']).toMatchObject({role: 'hero', text: 'Every frame holds', entrance: true});
    expect('role' in rows['card.x'] || 'text' in rows['card.x']).toBe(false); // a shape says nothing about type
    expect(motionProblems()).toEqual([]);
  });

  it('records words given without a role, and a role that is not a name, and leaves them off', () => {
    defineMotion({id: 'label.fade', shot: 'K01', prop: 'opacity', ease: 'enter', frame_in: 0, frame_out: 8, from: 0, to: 1, text: 'Ready'});
    defineMotion({id: 'odd.role', shot: 'K01', prop: 'y', ease: 'enter', frame_in: 0, frame_out: 8, from: 8, to: 0, role: '   '});
    const rows = Object.fromEntries(traceMotions({act: 1}).map((r) => [r.id, r]));
    expect(rows['label.fade'].text).toBe('Ready');
    expect('role' in rows['odd.role']).toBe(false);
    const p = motionProblems().join(' ');
    expect(p).toMatch(/motion "label.fade": it names its text but not its type role/);
    expect(p).toMatch(/motion "odd.role": role must be a type role's name/);
  });
});
