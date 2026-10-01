// Lines and markers: the look's line weight, nothing drawn at zero length, dots that grow in, axis labels that land one
// by one at a readable size, and a logo build that fits whatever room it's given.
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {beforeEach, describe, expect, it} from 'vitest';
import {
  Axis,
  DistributionCurve,
  IconCheck,
  KitProvider,
  LineChart,
  LogoBuild,
  RingChart,
  dotGrowth,
  fitLogoBuild,
  lineArrivals,
  logoBuildExtent,
  logoPlan,
  lookOf,
  setBrand,
  setFilmData,
  useLine,
} from '@slate/kit';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

const at = (t: number, node: React.ReactNode, size = {width: 960, height: 540}, look?: string) =>
  renderToStaticMarkup(
    <KitProvider t={t} width={size.width} height={size.height} look={look}>
      {node}
    </KitProvider>,
  );

const dist = {mean: 0, sd: 1, min: -3, max: 3};
/** Every path or circle drawn with a dash (a stroke that draws on). */
const dashed = (html: string) => html.match(/<(path|circle)[^>]*stroke-dasharray="[^"]*"[^>]*>/g) ?? [];

describe("the look's line weight (L-kit-D131)", () => {
  it("draws the curve's and the line's default stroke at the look's line, at any frame size", () => {
    const fixtureLook = lookOf(FILM.brand as never).look;
    for (const size of [{width: 960, height: 540}, {width: 1920, height: 1080}]) {
      const want = fixtureLook.line * Math.min(size.width, size.height);
      const curve = at(0, <DistributionCurve dist={dist} w={300} h={100} />, size);
      const line = at(0, <LineChart values={[1, 3, 2]} w={300} h={100} />, size);
      for (const html of [curve, line]) {
        const sw = Number(html.match(/<path[^>]*stroke-width="([\d.]+)"[^>]*stroke-dasharray/)![1]);
        expect(sw).toBeCloseTo(want, 9);
      }
    }
  });

  it('gives acts the same weight through useLine()', () => {
    let seen = 0;
    const Probe = () => {
      seen = useLine();
      return null;
    };
    at(0, <Probe />, {width: 1920, height: 1080}, 'editorial');
    expect(seen).toBeCloseTo(lookOf({look: 'editorial'} as never).look.line * 1080, 9);
  });

  it('draws the logo build outline in the look\'s line weight, scaled into the artwork\'s units', () => {
    const plan = logoPlan()!;
    const w = 400;
    const html = at(plan.times.outlineStart + plan.times.outlineDur * 0.5, <LogoBuild t0={0} x={0} y={0} w={w} />);
    const outline = html.match(/<path[^>]*fill="none"[^>]*stroke-width="([\d.]+)"[^>]*path-length="1"/) ?? html.match(/<path[^>]*stroke-width="([\d.]+)"[^>]*pathLength="1"/);
    expect(outline).not.toBeNull();
    const k = w / plan.viewBox[2];
    expect(Number(outline![1]) * k).toBeCloseTo(lookOf(FILM.brand as never).look.line * 540, 3);
  });
});

describe('nothing draws at zero length (L-kit-D133)', () => {
  it('leaves out every drawn-on stroke while its progress is 0: a zero-length dash with round caps still paints a dot', () => {
    expect(dashed(at(0, <DistributionCurve dist={dist} w={300} h={100} drawn={0} />))).toEqual([]);
    expect(dashed(at(0, <LineChart values={[1, 3, 2]} w={300} h={100} drawn={0} dots />))).toEqual([]);
    expect(at(0, <LineChart values={[1, 3, 2]} w={300} h={100} drawn={0} dots />)).not.toContain('<circle');
    expect(at(0, <IconCheck draw={0} />)).not.toContain('<path');
    expect(dashed(at(0, <RingChart size={100} value={0} />))).toEqual([]);
    // the logo build's outline before it starts drawing
    const plan = logoPlan()!;
    const before = at(plan.times.outlineStart - 0.01, <LogoBuild t0={0} x={0} y={0} w={400} />);
    expect(before).not.toMatch(/<path[^>]*fill="none"[^>]*stroke-dasharray="0 2"/);
  });

  it('still draws them once they have any length', () => {
    expect(dashed(at(0, <DistributionCurve dist={dist} w={300} h={100} drawn={0.01} />)).length).toBe(1);
    expect(dashed(at(0, <LineChart values={[1, 3, 2]} w={300} h={100} drawn={0.01} />)).length).toBe(1);
    expect(at(0, <IconCheck draw={0.01} />)).toContain('<path');
    expect(dashed(at(0, <RingChart size={100} value={0.01} />)).length).toBe(1);
  });
});

describe('dots grow in as the line reaches them (L-kit-D143)', () => {
  const values = [0, 4, 1, 3];
  const w = 300;
  const h = 100;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - (v / 4) * h] as [number, number]);
  const reach = lineArrivals(pts);
  const dotAt = (drawn: number, i: number) => {
    const html = at(0, <LineChart values={values} w={w} h={h} drawn={drawn} dots />);
    const x = pts[i][0];
    return html.match(new RegExp(`<circle cx="${x}" [^>]*>`))?.[0] ?? null;
  };

  it('arrives where the line does: shares of its length, not of x', () => {
    expect(reach[0]).toBe(0);
    expect(reach[reach.length - 1]).toBeCloseTo(1, 12);
    // the second segment is the longest, so the line reaches the third point later than 2/3 of the way across
    expect(reach[2]).not.toBeCloseTo(2 / 3, 3);
  });

  it('is absent just before its tenth of the draw, part-grown inside it, and whole when the line arrives', () => {
    for (const i of [1, 2, 3]) {
      expect(dotAt(reach[i] - 0.1 - 0.005, i)).toBeNull();
      const half = dotAt(reach[i] - 0.05, i)!;
      expect(Number(half.match(/opacity="([\d.]+)"/)![1])).toBeCloseTo(0.5, 6);
      const whole = dotAt(reach[i], i)!;
      expect(whole).not.toContain('opacity');
      expect(Number(whole.match(/ r="([\d.]+)"/)![1])).toBeCloseTo(3, 9); // a(3) on a 540-line frame
    }
  });

  it("grows the line's first dot over the first tenth, so nothing shows before the line starts", () => {
    expect(dotGrowth(0, 0)).toBe(0);
    expect(dotGrowth(0.05, 0)).toBeCloseTo(0.5, 12);
    expect(dotGrowth(0.1, 0)).toBe(1);
  });
});

describe('an axis stages its labels (L-kit-D103)', () => {
  it('draws each label with its own progress, and leaves out a label that has none yet', () => {
    const html = at(0, <Axis w={600} labels={[{at: 0, text: 'Low', op: 1}, {at: 0.5, text: 'Middle', op: 0.4}, {at: 1, text: 'High', op: 0}]} />);
    expect(html).toContain('>Low<');
    expect(html).toMatch(/opacity:0\.4[^"]*">Middle</);
    expect(html).not.toContain('High');
    expect(html.match(/>Low</)![0]).toBeTruthy();
    expect(html).not.toMatch(/opacity:[^"]*">Low</);
  });

  it('sets its labels in the body role, which reads at 28 px or more on 1080 lines', () => {
    const html = at(0, <Axis w={600} labels={[{at: 0.5, text: 'Label'}]} />, {width: 1920, height: 1080});
    const size = Number(html.match(/font-size:([\d.]+)px[^"]*">Label</)![1]);
    expect(size).toBeGreaterThanOrEqual(28);
  });
});

describe('the logo build fits the room it is given (the review sheet logo cell)', () => {
  it('keeps everything the build draws inside the area: the survey, the hanging keystone and its echo', () => {
    const plan = logoPlan()!;
    const e = logoBuildExtent(plan);
    // the keystone hangs above its place before it drops, so the extent reaches above the artwork's own box
    expect(e.y0).toBeLessThan(plan.box.y0);
    for (const [W, H] of [[497, 200], [300, 300], [800, 120]]) {
      const f = fitLogoBuild(plan, W, H);
      const k = f.w / plan.viewBox[2];
      const X = (u: number) => f.x + (u - plan.viewBox[0]) * k;
      const Y = (v: number) => f.y + (v - plan.viewBox[1]) * k;
      expect(X(e.x0)).toBeGreaterThanOrEqual(-1e-9);
      expect(Y(e.y0)).toBeGreaterThanOrEqual(-1e-9);
      expect(X(e.x1)).toBeLessThanOrEqual(W + 1e-9);
      expect(Y(e.y1)).toBeLessThanOrEqual(H + 1e-9);
    }
  });
});
