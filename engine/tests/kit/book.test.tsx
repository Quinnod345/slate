import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {beforeEach, describe, expect, it} from 'vitest';
import {bookGeometry, HingedBook, KitProvider, PageLeaf, pageStripAngles, pageStripGeometry, pageTurnShadow,
  RasterModeProvider, setBrand, setFilmData} from '@slate/kit';
import {bookReviewState, BookReview} from '../../remotion/kit/review/book';
import FILM from './fixtures/film-data';

beforeEach(() => {setFilmData(FILM as never); setBrand(null);});
const html = (node: React.ReactNode, mode: 'hint' | 'tilt' = 'tilt') => renderToStaticMarkup(
  <KitProvider><RasterModeProvider value={mode}>{node}</RasterModeProvider></KitProvider>);

describe('hinged page geometry', () => {
  it('rests perfectly flat, curls farther at the free edge, and chains without gaps', () => {
    expect(pageStripAngles(0)).toEqual(Array(11).fill(0));
    expect(pageStripAngles(-180)).toEqual(Array(11).fill(-180));
    const strips = pageStripGeometry(403.7, -74);
    expect(strips).toHaveLength(10);
    for (let i = 1; i < strips.length; i++) {
      expect(strips[i].angle).toBeLessThan(strips[i - 1].angle);
      expect(strips[i].x).toBe(strips[i - 1].endX);
      expect(strips[i].z).toBe(strips[i - 1].endZ);
      expect(strips.slice(0, i + 1).reduce((sum, s) => sum + s.relative, 0)).toBeCloseTo(strips[i].angle, 10);
    }
    const rigid = pageStripGeometry(403.7, -74, 10, 0).at(-1)!;
    expect(rigid.endX).toBeCloseTo(403.7 * Math.cos(74 * Math.PI / 180), 10);
    expect(rigid.endZ).toBeCloseTo(403.7 * Math.sin(74 * Math.PI / 180), 10);
  });
  it('registers every verso strip to the exact resting page, including fractional widths', () => {
    for (const w of [217, 403.7, 611.25]) {
      for (const overlap of [0, 0.6, 1.25]) {
        for (const g of pageStripGeometry(w, -180, 10, 1, overlap)) {
          // Local back-face mirror then the whole turned leaf mirror: a content point q lands at q-w.
          for (const q of [0, w * 0.37, w]) {
            const backLocal = g.faceW - (q + g.versoX);
            expect(g.x - backLocal).toBeCloseTo(q - w, 10);
          }
        }
      }
      const before = pageStripGeometry(w, -179.951).at(-1)!;
      expect(Math.abs(before.endX + w)).toBeLessThan(w * 0.000001);
      expect(Math.abs(before.endZ)).toBeLessThan(w * 0.001);
    }
  });
  it('hands the shadow through zero at upright with no full-strength jump across the spine', () => {
    for (const side of [1, -1] as const) {
      expect(pageTurnShadow(-90, side, 400).opacity).toBe(0);
      expect(pageTurnShadow(0, side, 400).opacity).toBe(0);
      expect(pageTurnShadow(-180, side, 400).opacity).toBe(0);
    }
    const right = pageTurnShadow(-89.999, 1, 400), left = pageTurnShadow(-90.001, -1, 400);
    expect(right.opacity).toBeLessThan(0.00001);
    expect(right.opacity).toBeCloseTo(left.opacity, 12);
    expect(pageTurnShadow(-45, 1, 400).opacity).toBeGreaterThan(0.1);
    expect(pageTurnShadow(-45, -1, 400).opacity).toBe(0);
  });
  it('orders arbitrarily long stacks above the base and open cover lining', () => {
    for (const n of [0, 1, 3, 24]) {
      const g = bookGeometry({w: 400, h: 550}, n);
      expect(g.back).toBeLessThan(g.base);
      expect(g.coverRight - g.coverThickness).toBeGreaterThan(g.base);
      for (let i = 0; i < n; i++) {
        expect(g.leafZ(i, 0)).toBeGreaterThan(g.base);
        expect(g.leafZ(i, -180)).toBeGreaterThan(g.coverLeft + g.coverThickness);
        expect(g.coverRight - g.coverThickness).toBeGreaterThan(g.right(i));
        if (i) {expect(g.right(i)).toBeLessThan(g.right(i - 1)); expect(g.left(i)).toBeGreaterThan(g.left(i - 1));}
      }
    }
  });
  it('rejects malformed geometry and clamps overshooting easing at the physical rests', () => {
    expect(() => bookGeometry({w: 0, h: 1}, 1)).toThrow(/width/);
    expect(() => pageStripGeometry(10, NaN)).toThrow(/angle/);
    expect(() => pageStripAngles(-50, 0)).toThrow(/strips/);
    expect(() => pageStripAngles(-50, 10, -1)).toThrow(/curl/);
    expect(pageStripAngles(20)).toEqual(Array(11).fill(0));
    expect(pageStripAngles(-220)).toEqual(Array(11).fill(-180));
  });
});

describe('book surfaces and film clock', () => {
  const leaf = (theta: number) => <PageLeaf w={400} h={550} theta={theta} recto={<b>front</b>} verso={<b>back</b>} />;
  it('has ten physical front/back faces while turning and a single crisp face at rest', () => {
    const moving = html(leaf(-85));
    expect((moving.match(/data-slate-book-strip=/g) ?? [])).toHaveLength(10);
    expect((moving.match(/backface-visibility:hidden/g) ?? [])).toHaveLength(40);
    expect(html(leaf(0))).toContain('<b>front</b>');
    expect(html(leaf(0))).not.toContain('<b>back</b>');
    expect(html(leaf(-180))).toContain('<b>back</b>');
    expect(html(leaf(-180))).not.toContain('data-slate-book-strip');
  });
  it('registers the verso equally in both raster modes and fades shadows continuously', () => {
    expect(html(leaf(-85), 'tilt')).toContain('transform:translateX(-359.4px)');
    expect(html(leaf(-85), 'hint')).toContain('left:-359.4px');
    expect(html(leaf(-85), 'hint')).not.toContain('transform:translateX');
    const props = {w: 400, h: 550, state: {cover: -180, leaves: [-75]}, coverOuter: 'outside', coverInner: 'lining',
      leaves: [{recto: 'recto', verso: 'verso'}], last: 'last'};
    const book = html(<HingedBook {...props} />);
    expect(book).toContain('data-slate-book-shadow="1"');
    expect(book).not.toMatch(/preserve-3d[^>]*(?:opacity:|filter:)/);
    // A shallow turn still draws its tiny shadow; a visibility threshold would pop it on part-way into the turn.
    expect(html(<HingedBook {...props} state={{cover: -180, leaves: [-0.5]}} />)).toContain('data-slate-book-shadow="1"');
    expect(html(<HingedBook {...props} state={{cover: -180, leaves: [-89.9]}} />)).toContain('data-slate-book-shadow="1"');
    expect(html(<HingedBook {...props} state={{cover: -180, leaves: [-90]}} />)).not.toContain('data-slate-book-shadow');
  });
  it('runs the same neutral proof at integer and rational rates and fractional exposure times', () => {
    for (const [num, den] of [[24, 1], [25, 1], [30000, 1001]]) {
      const rate = num / den;
      for (const seconds of [0, 0.5, 1.375, 2.425, 3.1]) {
        const frame = seconds * rate;
        const state = bookReviewState(frame * den / num), expected = bookReviewState(seconds);
        expect(state.cover).toBeCloseTo(expected.cover, 10);
        state.leaves.forEach((angle, i) => expect(angle).toBeCloseTo(expected.leaves[i], 10));
        const markup = renderToStaticMarkup(<KitProvider t={frame * den / num} width={640} height={360}><BookReview /></KitProvider>);
        expect(markup).toContain('data-slate-book="hinged"');
        expect(markup).not.toMatch(/NaN|Infinity/);
      }
    }
    expect(bookReviewState(0)).toEqual({cover: -0, leaves: [-0, -0]});
    expect(bookReviewState(3.1)).toEqual({cover: -180, leaves: [-180, -180]});
  });
});
