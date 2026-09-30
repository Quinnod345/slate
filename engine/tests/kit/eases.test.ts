// Eases, T90 and splines, checked against the shared vectors (tests/vectors/*.json), which pytest and the After
// Effects runner check too.
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';
import {BUILTIN, aeKeyframeEase, evalEase, makeEase, monoSpline, parseEase, startForSpec, t90, tAt, type EaseSpec} from '@slate/kit/core';

const here = path.dirname(fileURLToPath(import.meta.url));
const vectors = path.resolve(here, '../../../tests/vectors');
const EASES = JSON.parse(readFileSync(path.join(vectors, 'eases.json'), 'utf8'));
const T90 = JSON.parse(readFileSync(path.join(vectors, 't90.json'), 'utf8'));

const specOf = (s: {kind: string; bezier?: number[]; stiffness?: number}): EaseSpec => parseEase(s.kind === 'bezier' ? s.bezier : s.kind === 'spring0' ? {kind: 'spring0', stiffness: s.stiffness} : s.kind)!;

describe('the shared vectors', () => {
  it('match every ease sample', () => {
    const tol = EASES.tolerance as number;
    expect(EASES.eases.length).toBeGreaterThanOrEqual(10);
    for (const e of EASES.eases) {
      const spec = specOf(e.spec);
      expect(spec, e.name).not.toBeNull();
      for (const [u, y] of e.samples as [number, number][]) expect(Math.abs(evalEase(spec, u) - y), `${e.name} at ${u}`).toBeLessThanOrEqual(tol);
    }
  });

  it('match every T90 and landing', () => {
    const tol = T90.tolerance as number;
    for (const e of T90.t90) {
      const spec = specOf(e.spec);
      expect(Math.abs(t90(spec) - e.t90), e.name).toBeLessThanOrEqual(tol);
      expect(Math.abs(tAt(spec, 0.5) - e.t50), e.name).toBeLessThanOrEqual(tol);
      expect(Math.abs(tAt(spec, 0.99) - e.t99), e.name).toBeLessThanOrEqual(tol);
    }
    for (const l of T90.start_for) expect(Math.abs(startForSpec(l.land, l.dur, specOf(l.spec)) - l.start), l.ease).toBeLessThanOrEqual(tol);
  });

  it('match every AE speed and influence', () => {
    for (const e of EASES.eases.filter((x: {ae?: unknown}) => x.ae)) {
      const got = aeKeyframeEase(e.spec.bezier);
      for (const side of ['out', 'in'] as const) {
        expect(got[side].influence, `${e.name} ${side}`).toBeCloseTo(e.ae[side].influence, 9);
        expect(got[side].speed, `${e.name} ${side}`).toBeCloseTo(e.ae[side].speed, 9);
      }
    }
  });

  it('match every spline sample', () => {
    for (const s of EASES.splines) for (const [t, v] of s.samples as [number, number][]) expect(Math.abs(monoSpline(t, s.keys) - v), `${s.name} at ${t}`).toBeLessThanOrEqual(1e-12);
  });
});

describe('exact endpoints', () => {
  const kinds: EaseSpec[] = [{kind: 'linear'}, {kind: 'sine'}, {kind: 'spring0', stiffness: 7}, {kind: 'bezier', bezier: [0.2, 0.7, 0.2, 1]}, {kind: 'bezier', bezier: [0.34, 1.56, 0.64, 1]}];
  it('is exactly 0 before and at the start, exactly 1 at and after the end', () => {
    for (const k of kinds) {
      for (const u of [-10, -1e-12, 0, Number.NaN]) expect(evalEase(k, u)).toBe(0);
      for (const u of [1, 1 + 1e-12, 10]) expect(evalEase(k, u)).toBe(1);
      // just inside, it has started (no 1e-14 residue masquerading as "not yet")
      expect(evalEase(k, 1e-6)).toBeGreaterThanOrEqual(0);
    }
  });
  it('gives the solver a hard time and gets it right', () => {
    const hard = makeEase({kind: 'bezier', bezier: [1, 0, 0, 1]});
    let prev = 0;
    for (let i = 0; i <= 1000; i++) {
      const v = hard(i / 1000);
      expect(v).toBeGreaterThanOrEqual(prev - 1e-12); // monotone
      prev = v;
    }
    expect(hard(0.5)).toBeCloseTo(0.5, 12);
  });
});

describe('reading eases from a pack', () => {
  it('accepts the usual spellings', () => {
    expect(parseEase([0.1, 0.2, 0.3, 0.4])).toEqual({kind: 'bezier', bezier: [0.1, 0.2, 0.3, 0.4]});
    expect(parseEase('cubic-bezier(0.1, 0.2, 0.3, 0.4)')).toEqual({kind: 'bezier', bezier: [0.1, 0.2, 0.3, 0.4]});
    expect(parseEase({bezier: [0, 0, 1, 1], lineage: 'x'})).toEqual({kind: 'bezier', bezier: [0, 0, 1, 1]});
    expect(parseEase({cubic_bezier: '0,0,1,1'})).toEqual({kind: 'bezier', bezier: [0, 0, 1, 1]});
    expect(parseEase('linear')).toEqual({kind: 'linear'});
    expect(parseEase({kind: 'sine'})).toEqual({kind: 'sine'});
    expect(parseEase({kind: 'spring0', stiffness: 9})).toEqual({kind: 'spring0', stiffness: 9});
  });
  it('refuses a bezier that is not a function of time', () => {
    expect(parseEase([1.2, 0, 0.5, 1])).toBeNull();
    expect(parseEase([0.2, 0, -0.1, 1])).toBeNull();
    expect(parseEase('bouncy')).toBeNull();
    expect(parseEase([0, 1])).toBeNull();
  });
  it('lands a move on its beat', () => {
    // sine reaches 90% at 0.795: a 1 s sine move landing at 5 s starts at 4.205 s
    expect(startForSpec(5, 1, BUILTIN.sine)).toBeCloseTo(5 - 0.7951672353, 9);
    expect(t90(BUILTIN.linear)).toBeCloseTo(0.9, 12);
  });
});

describe('monotone splines', () => {
  it('is exact at every key and never leaves the range of its two keys', () => {
    const keys: [number, number][] = [
      [0, 0],
      [0.2, 100],
      [3, 110],
      [3.2, 300],
    ];
    for (const [t, v] of keys) expect(monoSpline(t, keys)).toBeCloseTo(v, 12);
    for (let i = 0; i <= 3200; i++) {
      const t = i / 1000;
      const seg = t < 0.2 ? [0, 100] : t < 3 ? [100, 110] : [110, 300];
      const v = monoSpline(t, keys);
      expect(v).toBeGreaterThanOrEqual(seg[0] - 1e-9);
      expect(v).toBeLessThanOrEqual(seg[1] + 1e-9);
    }
  });
  it('is at rest at the first and last key', () => {
    const keys: [number, number][] = [
      [0, 0],
      [1, 10],
      [2, 30],
    ];
    const h = 1e-6;
    expect(Math.abs(monoSpline(h, keys) - monoSpline(0, keys)) / h).toBeLessThan(1e-3);
    expect(Math.abs(monoSpline(2, keys) - monoSpline(2 - h, keys)) / h).toBeLessThan(1e-3);
  });
});
