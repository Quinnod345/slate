// The motif rig: one clock, three phase relationships, calm at rest, turns that land on themselves.
import {beforeEach, describe, expect, it} from 'vitest';
import {clearMotions, groupOf, isCalm, motifBeats, motifLint, motifPose, motifSpec, motions, placeParts, registerMotifTrace, setBrand, setFilmData, traceMotions, type MotifTrack} from '@slate/kit/core';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
  clearMotions();
});

const {spec} = motifSpec();
const big = () => ({x: 480, y: 270, size: 200}); // well above the full-choreography size on a 540-line frame
const small = () => ({x: 480, y: 270, size: 20}); // below the unison size: moves as a whole

describe('the spec', () => {
  it("reads the brand's motif", () => {
    expect(spec.parts).toBe(6);
    expect(spec.shape).toBe('rounded-square');
    expect(spec.groups).toBe(2);
    expect(spec.maxOvershoot).toBe(0); // the fixture's overshoot policy is "not allowed"
  });
  it('draws a plain ring, and says so, for a brand with no motif', () => {
    const r = motifSpec(null, {});
    expect(r.declared).toBe(false);
    expect(r.problems.join(' ')).toMatch(/no motif/);
  });
});

describe('rest and one clock', () => {
  it('is exactly the rest mark outside every gesture', () => {
    const track: MotifTrack = [{kind: 'pulse', frame: 50}, {kind: 'turn', frame: 100}];
    for (const f of [0, 20, 30, 75, 130, 300]) expect(isCalm(spec, motifPose(track, f, {spec, layout: big, short: 540}))).toBe(true);
    expect(isCalm(spec, motifPose(track, 50, {spec, layout: big, short: 540}))).toBe(false);
  });

  it('is a pure function of the frame (a frame alone equals the same frame in sequence)', () => {
    const track: MotifTrack = [{kind: 'wave', frame: 40}, {kind: 'breathe', frame: 80, cycles: 2}];
    const alone = motifPose(track, 97.3125, {spec, layout: big, short: 540});
    for (let f = 0; f < 97; f++) motifPose(track, f, {spec, layout: big, short: 540});
    expect(motifPose(track, 97.3125, {spec, layout: big, short: 540})).toEqual(alone);
  });
});

describe('the three phase relationships', () => {
  it('unison: every part identical at every frame of a pulse', () => {
    const track: MotifTrack = [{kind: 'pulse', frame: 30}];
    for (let f = 18; f <= 45; f += 0.5) {
      const p = motifPose(track, f, {spec, layout: big, short: 540});
      const r0 = p.parts[0].rho;
      for (const part of p.parts) expect(part.rho).toBeCloseTo(r0, 12);
    }
  });

  it('pairs: the second group runs the first group exactly `pairsLag` frames later', () => {
    const track: MotifTrack = [{kind: 'breathe', frame: 30}];
    for (let f = 30; f <= 80; f++) {
      const now = motifPose(track, f, {spec, layout: big, short: 540});
      const earlier = motifPose(track, f - spec.pairsLag, {spec, layout: big, short: 540});
      const a = now.parts.find((_, i) => groupOf(spec, i) === 1)!.rho;
      const b = earlier.parts.find((_, i) => groupOf(spec, i) === 0)!.rho;
      expect(a).toBeCloseTo(b, 12);
    }
  });

  it('the wave: slot k at frame f is slot 0 at frame f − k × step', () => {
    const track: MotifTrack = [{kind: 'wave', frame: 40, from: 0, dir: 1}];
    for (let f = 30; f <= 80; f++) {
      const now = motifPose(track, f, {spec, layout: big, short: 540});
      for (let k = 1; k < spec.parts; k++) {
        const then = motifPose(track, f - k * spec.waveStep, {spec, layout: big, short: 540});
        expect(now.parts[k].rho).toBeCloseTo(then.parts[0].rho, 12);
      }
    }
  });

  it('never dips below rest unless the brand allows overshoot', () => {
    const track: MotifTrack = [{kind: 'wave', frame: 40, dip: 0.12}];
    for (let f = 30; f <= 80; f += 0.25) for (const p of motifPose(track, f, {spec, layout: big, short: 540}).parts) expect(p.rho).toBeGreaterThanOrEqual(-1e-12);
    const allowed = motifSpec(null, {motion: {eases: {m: 'sine'}, overshoot: {allowed: true, max: 0.12}}, motif: {parts: 6}}).spec;
    const dips = [];
    for (let f = 30; f <= 80; f += 0.25) dips.push(Math.min(...motifPose(track, f, {spec: allowed, layout: big, short: 540}).parts.map((p) => p.rho)));
    expect(Math.min(...dips)).toBeLessThan(-0.05);
  });
});

describe('gestures', () => {
  it('a turn lands exactly on itself: the same picture, one slot on', () => {
    const track: MotifTrack = [{kind: 'turn', frame: 40}];
    const before = placeParts(spec, motifPose(track, 0, {spec, layout: big, short: 540}), big());
    const after = placeParts(spec, motifPose(track, 80, {spec, layout: big, short: 540}), big());
    // part i ends where part i+1 started
    for (let i = 0; i < spec.parts; i++) {
      const j = (i + 1) % spec.parts;
      expect(after[i].x).toBeCloseTo(before[j].x, 9);
      expect(after[i].y).toBeCloseTo(before[j].y, 9);
    }
    expect(isCalm(spec, motifPose(track, 80, {spec, layout: big, short: 540}))).toBe(true);
  });

  it('a lean holds until home brings it back', () => {
    const track: MotifTrack = [
      {kind: 'lean', frame: 30, toward: {x: 480 + 300, y: 270}},
      {kind: 'home', frame: 90},
    ];
    const held = motifPose(track, 60, {spec, layout: big, short: 540});
    expect(held.ox).toBeCloseTo(0.08, 9);
    expect(held.oy).toBeCloseTo(0, 9);
    expect(isCalm(spec, motifPose(track, 100, {spec, layout: big, short: 540}))).toBe(true);
  });

  it('a hold pulls in and stays perfectly still until released', () => {
    const track: MotifTrack = [{kind: 'hold', frame: 30, untilFrame: 70}];
    const a = motifPose(track, 40, {spec, layout: big, short: 540});
    const b = motifPose(track, 65, {spec, layout: big, short: 540});
    expect(a).toEqual(b);
    expect(a.parts[0].rho).toBeCloseTo(-0.14, 9);
    expect(isCalm(spec, motifPose(track, 95, {spec, layout: big, short: 540}))).toBe(true);
  });

  it('ripples echo out from the mark and are gone after', () => {
    const track: MotifTrack = [{kind: 'ripple', frame: 20, count: 2}];
    expect(motifPose(track, 30, {spec, layout: big, short: 540}).ripples.length).toBe(2);
    expect(motifPose(track, 100, {spec, layout: big, short: 540}).ripples.length).toBe(0);
  });

  it('enters from outside the ring, faded, and ends whole', () => {
    const track: MotifTrack = [{kind: 'enter', frame: 40}];
    const start = motifPose(track, 10, {spec, layout: big, short: 540});
    expect(start.parts[0].op).toBe(0);
    expect(start.parts[0].rho).toBeCloseTo(2.2, 9);
    expect(isCalm(spec, motifPose(track, 60, {spec, layout: big, short: 540}))).toBe(true);
  });

  it('a small mark moves as a whole: per-part spread becomes scale', () => {
    const track: MotifTrack = [{kind: 'pulse', frame: 30}];
    const p = motifPose(track, 30, {spec, layout: small, short: 540});
    expect(p.regime).toBe('whole');
    expect(p.parts.every((x) => x.rho === 0)).toBe(true);
    expect(p.scale).toBeGreaterThan(1);
  });
});

describe('sound and checks', () => {
  it('gives one sound per frame, on whole contact frames', () => {
    const beats = motifBeats([{kind: 'turn', frame: 40}, {kind: 'pulse', frame: 40}, {kind: 'wave', frame: 80}], spec);
    const frames = beats.map((b) => b.frame);
    expect(new Set(frames).size).toBe(frames.length);
    expect(beats.find((b) => b.frame === 40)?.gesture).toBe('turn'); // priority
    expect(beats.filter((b) => b.gesture === 'wave').length).toBe(spec.parts);
    expect(beats.every((b) => b.kind === 'character' && Number.isInteger(b.frame))).toBe(true);
  });

  it('lints gestures that crowd each other', () => {
    expect(motifLint([{kind: 'pulse', frame: 10}, {kind: 'turn', frame: 12}]).length).toBe(1);
    expect(motifLint([{kind: 'pulse', frame: 10}, {kind: 'turn', frame: 40}])).toEqual([]);
  });

  it('registers every part as traced channels for the motion gate', () => {
    registerMotifTrace([{kind: 'wave', frame: 40}], {rig: 'motif:hello', shot: 'K01', layout: big, from: 20, to: 90, short: 540});
    expect(motions().length).toBe(spec.parts * 2);
    const trace = traceMotions({act: 1});
    expect(trace.every((e) => e.rig === 'motif:hello')).toBe(true);
    expect(trace[0].samples.length).toBe(71);
  });
});
