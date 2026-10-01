// The character on screen: its disc placed by a transform, staging that never changes a gesture, the gesture sheet's
// cells, and a two-sided card that turns past 90° with a face where its back is.
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {beforeEach, describe, expect, it} from 'vitest';
import {
  Card,
  DISC_BASE,
  GESTURE_KINDS,
  GESTURE_SIZES,
  GestureSheet,
  gestureSheetLayout,
  KitProvider,
  MarkCharacter,
  motifSpec,
  P,
  setBrand,
  setFilmData,
  type MotifTrack,
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

/** The centres of the mark's parts as drawn (rounded-square parts: a rect translated to its centre). */
const partCentres = (html: string) => [...html.matchAll(/<rect [^>]*transform="translate\(([-\d.]+) ([-\d.]+)\) rotate\(([-\d.]+)\)"/g)].map((m) => ({x: Number(m[1]), y: Number(m[2]), rot: Number(m[3])}));

describe("the character's disc sits under its parts on every frame (L-kit-S013)", () => {
  it('places and sizes a fixed circle with a transform, keeping sub-pixel offsets', () => {
    const html = at(0, <MarkCharacter x={300.37} y={200.61} size={200} />);
    // a DISC_BASE circle, translated to the disc's corner and scaled to its size, to the ten-thousandth of a pixel
    expect(html).toContain(`width:${DISC_BASE}px;height:${DISC_BASE}px;border-radius:50%`);
    expect(html).toContain('transform:translate(200.3700px, 100.6100px) scale(2.000000)');
    // the centre it lands on is the layout's own, fractions and all
    const m = html.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([\d.]+)\)/)!;
    const k = Number(m[3]);
    expect(Number(m[1]) + (DISC_BASE * k) / 2).toBeCloseTo(300.37, 9);
    expect(Number(m[2]) + (DISC_BASE * k) / 2).toBeCloseTo(200.61, 9);
  });

  it('keeps the shadow the same size whatever the transform scales', () => {
    const html = at(0, <MarkCharacter x={300} y={200} size={400} />);
    // 0.1 of the disc's size, given in the disc's own units (scale 4): 40 px on screen
    expect(html).toMatch(/box-shadow:0 10\.0000px 28\.0000px -12\.0000px/);
  });

  it('can still be laid out the old way', () => {
    const html = at(0, <MarkCharacter x={300.37} y={200.61} size={200} subpixel={false} />);
    expect(html).toContain('left:200.37px;top:100.61000000000001px;width:200px;height:200px');
  });
});

describe('staging a close-up never changes a gesture (L-kit-D075)', () => {
  const {spec} = motifSpec();
  // a small disc runs its gestures as a whole; staged 2.5 times larger it would cross into full choreography if the
  // staging were taken for its size
  const track: MotifTrack = [{kind: 'breathe', frame: 30}, {kind: 'lean', frame: 120, toward: {x: 900, y: 270}}];
  const x = 480;
  const y = 270;
  const size = 50;

  it('draws the same pose, scaled and moved with the mark', () => {
    for (const t of [1.2, 1.6, 2.0, 5.2]) {
      const plain = partCentres(at(t, <MarkCharacter x={x} y={y} size={size} track={track} />));
      const staged = partCentres(at(t, <MarkCharacter x={x} y={y} size={size} track={track} markScale={2.5} markOffset={{x: 0.1, y: -0.05}} />));
      expect(plain).toHaveLength(spec.parts);
      expect(staged).toHaveLength(spec.parts);
      const ox = x + 0.1 * size;
      const oy = y - 0.05 * size;
      for (let i = 0; i < spec.parts; i++) {
        expect(staged[i].x - ox).toBeCloseTo((plain[i].x - x) * 2.5, 2);
        expect(staged[i].y - oy).toBeCloseTo((plain[i].y - y) * 2.5, 2);
        expect(staged[i].rot).toBeCloseTo(plain[i].rot, 6);
      }
    }
  });
});

describe('the gesture sheet (L-kit-079, L-kit-D082)', () => {
  it('has a cell for every gesture kind, keyed to land at t = 1 s, with its sounds', () => {
    const L = gestureSheetLayout();
    const html = at(1, <GestureSheet />, {width: L.width, height: L.height});
    for (const kind of GESTURE_KINDS) expect(html).toContain(`>${kind} · `);
    // two characters a cell: the smallest and the largest size a film uses it
    expect((html.match(/border-radius:50%/g) ?? []).length).toBe(2 * GESTURE_KINDS.length);
    expect(html).toMatch(/\d+ sounds?, f\d+/);
  });

  it("sizes the characters for the film's own short side: 48 and 820 px on 1080 lines", () => {
    const L = gestureSheetLayout();
    expect(GESTURE_SIZES.large * 1080).toBeCloseTo(820, 6);
    expect(GESTURE_SIZES.small * 1080).toBeCloseTo(48, 6);
    expect(L.large).toBeCloseTo(GESTURE_SIZES.large * 540, 9);
    expect(L.small).toBeCloseTo(GESTURE_SIZES.small * 540, 9);
    expect(L.width).toBeGreaterThanOrEqual(L.cols * L.large);
  });
});

describe('a two-sided card turns past 90° (L-kit-052)', () => {
  it('puts a back face at the far side of the edge slices, turned around, and each face hides its own back', () => {
    const html = at(0, <Card w={300} h={200} pose={P(0, 0, 0, 0, 135)} back="#EEDDCC" />, undefined, 'paper-2.5d');
    expect(html).toContain('rotateY(180deg)');
    expect(html).toContain('background:#EEDDCC');
    expect((html.match(/backface-visibility:hidden/g) ?? []).length).toBe(2);
    // behind the six thickness slices of the papery look
    const z = Number(html.match(/translateZ\((-[\d.]+)px\) rotateY\(180deg\)/)![1]);
    const slices = [...html.matchAll(/translateZ\((-[\d.]+)px\)"/g)].map((m) => Number(m[1]));
    expect(z).toBeLessThan(Math.min(...slices));
  });

  it('draws a one-sided card exactly as before', () => {
    const html = at(0, <Card w={300} h={200} pose={P()} />, undefined, 'paper-2.5d');
    expect(html).not.toContain('rotateY(180deg)');
    expect(html).not.toContain('backface-visibility');
  });
});
