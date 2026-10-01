// SVG geometry and the logo build plan.
import {beforeEach, describe, expect, it} from 'vitest';
import {parsePath, parseSvg, parseTransform, planLogoBuild, logoEvents, segsBBox, segsVertices, setBrand, setFilmData, spanProblems} from '@slate/kit/core';
import FILM from './fixtures/film-data';
import BRAND from './fixtures/neutral-brand.json';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

const SVG = BRAND.logos.primary.svg;

describe('path data', () => {
  it('reads absolute and relative commands, implicit repeats and H/V', () => {
    const segs = parsePath('m10 10 h20 v20 h-20 z');
    expect(segs.map((s) => s.kind)).toEqual(['M', 'L', 'L', 'L', 'Z']);
    expect(segs[2].to).toEqual({x: 30, y: 30});
    expect(segsBBox(segs)).toEqual({x0: 10, y0: 10, x1: 30, y1: 30});
    const implicit = parsePath('M0 0 10 0 10 10');
    expect(implicit.map((s) => s.kind)).toEqual(['M', 'L', 'L']);
  });

  it('gives the exact box of a curve, not its control points', () => {
    // a cubic whose controls reach y = -40 only bulges to y = -30
    const b = segsBBox(parsePath('M0 0 C0 -40 100 -40 100 0'));
    expect(b.y0).toBeCloseTo(-30, 9);
    expect(b.x1).toBe(100);
  });

  it('reads run-together arc flags', () => {
    const a = parsePath('M0 0 a10 10 0 01 20 0');
    const b = parsePath('M0 0 a10 10 0 0 1 20 0');
    expect(a).toEqual(b);
    expect(segsBBox(a).y0).toBeCloseTo(-10, 6);
  });

  it('finds corners where the outline turns, not along smooth curves', () => {
    const square = segsVertices(parsePath('M0 0H10V10H0Z'));
    expect(square.corners.length).toBe(4);
    const circle = segsVertices(parsePath('M0 5A5 5 0 1 0 10 5A5 5 0 1 0 0 5Z'));
    expect(circle.corners.length).toBe(0);
  });

  it('applies transforms, including rotate about a point', () => {
    const m = parseTransform('translate(10 20) scale(2)');
    expect(m).toEqual([2, 0, 0, 2, 10, 20]);
    const r = parseTransform('rotate(90 5 5)');
    // (10, 5) turns to (5, 10) about (5, 5)
    expect(r[0] * 10 + r[2] * 5 + r[4]).toBeCloseTo(5, 9);
    expect(r[1] * 10 + r[3] * 5 + r[5]).toBeCloseTo(10, 9);
  });
});

describe('the fixture logo', () => {
  it('parses every shape with its fill', () => {
    const p = parseSvg(SVG);
    expect(p.viewBox).toEqual([0, 0, 200, 60]);
    expect(p.shapes.map((s) => s.tag)).toEqual(['rect', 'path', 'path', 'rect', 'circle']);
    expect(p.shapes[4].fill).toBe('#3456A6');
    expect(p.shapes[1].bbox).toEqual({x0: 50, y0: 10, x1: 90, y1: 50});
    expect(p.shapes[4].bbox.x0).toBeCloseTo(162, 9);
    expect(p.problems).toEqual([]);
  });

  it('reads group transforms, inherited fills and class fills', () => {
    const svg = '<svg viewBox="0 0 10 10"><style>.k{fill:#123456}</style><g transform="translate(5 0)" fill="#ABCDEF"><rect width="2" height="2"/><rect class="k" y="4" width="2" height="2"/></g><text>hi</text></svg>';
    const p = parseSvg(svg);
    expect(p.shapes[0].bbox).toEqual({x0: 5, y0: 0, x1: 7, y1: 2});
    expect(p.shapes[0].fill).toBe('#ABCDEF');
    expect(p.shapes[1].fill).toBe('#123456');
    expect(p.problems.join(' ')).toMatch(/<text> isn't read/);
  });

  it('plans the build: order, keystone last, guides from the edges, times in order', () => {
    const plan = planLogoBuild(SVG, BRAND.logos.primary.build as never);
    expect(plan.keystone).toBe(4);
    const order = plan.shapes.filter((s) => !s.keystone).sort((a, b) => a.order - b.order).map((s) => s.index);
    expect(order).toEqual([0, 1, 2, 3]);
    expect(plan.guides.h).toEqual([10, 38, 50]);
    expect(plan.guides.v.length).toBeGreaterThan(3);
    expect(plan.marks.length).toBeGreaterThan(8);
    const t = plan.times;
    expect(t.outlineStart).toBeLessThan(t.fillStart);
    expect(t.fillStart).toBeLessThan(t.fillEnd);
    expect(t.fillEnd).toBeLessThan(t.dropStart!);
    expect(t.land!).toBeGreaterThan(t.dropStart!);
    expect(t.end).toBeGreaterThanOrEqual(t.land! + t.echoDur);
    // timing comes from the brand's scale: the fixture's "broad" is 0.8 s
    expect(t.constructDur).toBe(0.8);
    const ev = logoEvents(plan, 2, 'K01');
    expect(ev.map((e) => e.kind)).toEqual(['draw', 'draw', 'land', 'impact']);
    // each draw gives its span and ends on its beat (the contact), as slate.timing.beats has every engine write it
    const [construct, outline] = ev;
    expect(construct).toMatchObject({start: 2, dur: t.constructEnd});
    expect(construct.t).toBeCloseTo(2 + t.constructEnd, 12);
    // the survey ends when the last guide has drawn out or the last mark arrived, whichever is later
    expect(t.constructEnd).toBeGreaterThanOrEqual(t.constructDur);
    expect(outline).toMatchObject({start: 2 + t.outlineStart, dur: t.outlineDur});
    expect(outline.t).toBeCloseTo(2 + t.outlineStart + t.outlineDur, 12);
    expect(spanProblems(ev, 25)).toEqual([]);
  });

  it('says when the keystone or the order names shapes the logo lacks', () => {
    const plan = planLogoBuild(SVG, {keystone: 'nope', order: [9, 0]});
    expect(plan.keystone).toBeNull();
    expect(plan.problems.join(' ')).toMatch(/keystone "nope"/);
    expect(plan.problems.join(' ')).toMatch(/fill order names shapes/);
  });
});
