// The kit's tilt raster mode (opt in per act): type drawn under a y-skew too small to see, so headless Chrome places
// it at quarter pixels instead of snapping it to whole ones, and nothing hinted or kept between frames. Hint mode, the
// default, must draw exactly what it drew before the mode existed: an act converts by wrapping its root, and its
// hint-mode drawing is its reference.
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {beforeEach, describe, expect, it} from 'vitest';
import {
  actRasterMode,
  Arrive,
  At,
  Camera,
  Card,
  exactLayout,
  FadeCard,
  Field,
  KineticWords,
  KitProvider,
  morphPose,
  P,
  PerChar,
  PerWord,
  Place,
  RasterModeProvider,
  setBrand,
  setFilmData,
  StreamText,
  TILT_SKEW,
  TiltPlane,
  TiltSpan,
  tiltDeg,
  TypeTilt,
  useTypeTilt,
  type RasterMode,
} from '@slate/kit';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

/** One frame of `node` at film time t, in a raster mode (or none: the default). */
function at(t: number, node: React.ReactNode, mode?: RasterMode): string {
  const body = mode ? <RasterModeProvider value={mode}>{node}</RasterModeProvider> : node;
  return renderToStaticMarkup(<KitProvider t={t}>{body}</KitProvider>);
}

/** A scene with every part tilt mode touches, mid-move (cards and type in motion, the camera pushing in). */
function Scene({t}: {t: number}) {
  const pose = P(-40 + 10 * t, 12 - 6 * t, 30 * t, 8 * (1 - t), 0, 0.9 + 0.1 * t);
  return (
    <>
      <Camera z={60 * t} x={4 * t} atRest={false}>
        <Card w={420} h={260} pose={pose} atRest={false}>
          <div style={{position: 'absolute', left: 24, top: 20}}>
            <PerChar text="Hold still" t0={0.1} t={t} />
          </div>
          <div style={{position: 'absolute', left: 24, top: 120}}>
            <PerWord text="every frame of it" t0={0.2} t={t} />
          </div>
          <Arrive t0={0.15} t={t}>
            <span>arrives as one</span>
          </Arrive>
        </Card>
        <At x={120} y={-80}>
          <span>a label in the world</span>
        </At>
        <Place x={-200} y={60} s={0.8}>
          <Field label="Name" value="Sam" fill={t} />
        </Place>
        <FadeCard w={300} h={180} pose={{...pose, op: 0.5, y: pose.y + 140}}>
          <span>fading card</span>
        </FadeCard>
      </Camera>
      <KineticWords shot="K01" text="Every move lands on its word" times={[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]} t={t} />
    </>
  );
}

describe('hint mode, the default, draws exactly what the kit drew before', () => {
  it('is the same page with no provider and with the hint provider, at every time', () => {
    for (const t of [0, 0.12, 0.31, 0.5, 0.77, 1]) {
      expect(at(t, <Scene t={t} />, 'hint')).toBe(at(t, <Scene t={t} />));
    }
  });

  it('adds nothing to the page: no tilt, no tilt markers, the raster hints where they were', () => {
    const html = at(0.31, <Scene t={0.31} />);
    expect(html).not.toMatch(/skewY|data-slate-tilt|translateZ\(0\.01px\)/);
    expect(html).toContain('will-change:transform');
  });

  it('draws TiltPlane, TiltSpan and TypeTilt as their content (TypeTilt as the plain box it lays out)', () => {
    const style = {position: 'absolute' as const, left: 10, top: 20};
    expect(at(0, <TiltPlane style={style}>x</TiltPlane>)).toBe('x');
    expect(at(0, <TiltSpan>x</TiltSpan>)).toBe('x');
    expect(at(0, <TypeTilt style={style}>x</TypeTilt>)).toBe(at(0, <div style={style}>x</div>));
  });
});

describe('tilt mode', () => {
  it("tilts type by a skew Skia can't round away: 0.034° at full size, more for smaller type, the same above 1", () => {
    expect(Math.tan((tiltDeg(1) * Math.PI) / 180)).toBeCloseTo(TILT_SKEW, 10);
    expect(tiltDeg(1).toFixed(4)).toBe('0.0344');
    expect(tiltDeg(0.5).toFixed(4)).toBe('0.0688');
    expect(tiltDeg(2)).toBe(tiltDeg(1));
    expect(tiltDeg(0.01)).toBe(tiltDeg(0.1)); // floored: a speck of type isn't skewed by degrees
  });

  it('tilts the type in a card\'s face and holds no raster hints or keys anywhere', () => {
    const html = at(0.31, <Scene t={0.31} />, 'tilt');
    expect(html).not.toContain('will-change');
    expect(html).toContain('data-slate-tilt="face"');
    expect(html).toMatch(/data-slate-tilt="face"[^>]*transform:skewY\(0\.\d{4}deg\)/);
  });

  it("sizes the face's tilt for the card's on-screen scale (the camera's push × the card's own scale)", () => {
    const small = at(0, <Camera><Card w={200} h={100} pose={P(0, 0, 0, 0, 0, 0.5)}>x</Card></Camera>, 'tilt');
    expect(small).toContain(`skewY(${tiltDeg(0.5).toFixed(4)}deg)`);
    const full = at(0, <Camera><Card w={200} h={100} pose={P(0, 0, 0, 0, 0, 1)}>x</Card></Camera>, 'tilt');
    expect(full).toContain(`skewY(${tiltDeg(1).toFixed(4)}deg)`);
  });

  it('lets a wide card keep its face untilted and tilt each text where it sits (faceTilt off)', () => {
    const html = at(0, <Card w={1400} h={300} pose={P()} faceTilt={false}><TiltSpan>Growth</TiltSpan><i style={{display: 'block', height: 2, background: 'currentColor'}} /></Card>, 'tilt');
    expect(html).not.toContain('data-slate-tilt="face"');
    expect(html).toContain('data-slate-tilt="span"');
  });

  it('puts the tilt inside each blurred character and word (TiltSpan), where Chrome may raster on its own', () => {
    const chars = at(0.12, <PerChar text="Hold" t0={0.1} />, 'tilt');
    expect((chars.match(/data-slate-tilt="span"/g) ?? []).length).toBe(4);
    expect(chars).toMatch(/filter:blur\([^)]*\)[^>]*><span data-slate-tilt="span"/);
    const words = at(0.25, <PerWord text="every frame of it" t0={0.2} />, 'tilt');
    expect((words.match(/data-slate-tilt="span"/g) ?? []).length).toBe(4);
    const kinetic = at(0.3, <KineticWords shot="K01" text="Every move lands" times={[0.1, 0.2, 0.3]} />, 'tilt');
    expect((kinetic.match(/data-slate-tilt="span"/g) ?? []).length).toBe(3);
  });

  it("appends the tilt to type that moves on its own transform, and never twice inside a tilt", () => {
    const alone = at(0.2, <Arrive t0={0.15}>moving</Arrive>, 'tilt');
    expect(alone).toMatch(/transform:translateY\([^)]*\) skewY\(0\.\d{4}deg\)/);
    const nested = at(0.2, <TypeTilt><Arrive t0={0.15}>moving</Arrive></TypeTilt>, 'tilt');
    expect((nested.match(/skewY/g) ?? []).length).toBe(1);
    const field = at(0, <Field value="Sam" fill={0.5} />, 'tilt');
    expect(field).toMatch(/translateY\([^)]*\) skewY\(/);
    function Probe() {
      return <i>{`[${useTypeTilt()}]`}</i>;
    }
    expect(at(0, <Probe />, 'tilt')).toContain(`[ skewY(${tiltDeg(1).toFixed(4)}deg)]`);
    expect(at(0, <Probe />, 'hint')).toContain('[]');
  });

  it('tilts type placed in the world (At) inside its own box', () => {
    const html = at(0, <Camera><At x={10} y={20}><span>label</span></At></Camera>, 'tilt');
    expect(html).toMatch(/translate3d\(10px, 20px, 0px\)"><div data-slate-tilt="type"/);
  });

  it('makes free type a plane in depth with the tilt drawn inside it', () => {
    const html = at(0, <TypeTilt plane style={{position: 'absolute', left: 150, top: 118}}>Free type</TypeTilt>, 'tilt');
    expect(html).toMatch(/data-slate-tilt="plane" style="[^"]*transform:translateZ\(0\.01px\)"><div style="transform:skewY\(/);
    expect(at(0, <TiltPlane style={{position: 'absolute'}}>x</TiltPlane>, 'tilt')).toContain('data-slate-tilt="plane"');
  });

  it('reads the act\'s raster mode from the render\'s input props, with the act\'s own as the fallback', () => {
    expect(actRasterMode('tilt', {})).toBe('tilt');
    expect(actRasterMode('tilt', {raster: 'hint'})).toBe('hint');
    expect(actRasterMode('hint', {raster: 'tilt'})).toBe('tilt');
    expect(actRasterMode('hint', {raster: 'sharp'})).toBe('hint');
  });
});

describe('a card whose size morphs (morphPose, tilt mode)', () => {
  it('lays out at whole even sizes and scales to the true size; at even sizes nothing is scaled', () => {
    expect(exactLayout(540.3, 340.2, true)).toMatchObject({W: 540, H: 340, sized: true});
    expect(exactLayout(540.3, 340.2, true).sx).toBeCloseTo(540.3 / 540, 12);
    expect(exactLayout(538, 336, true)).toEqual({W: 538, H: 336, sx: 1, sy: 1, sized: false});
    expect(exactLayout(540.3, 340.2, false)).toEqual({W: 540.3, H: 340.2, sx: 1, sy: 1, sized: false});
  });

  it('scales the face, the layers and the pages to the true size, and the content back to 1', () => {
    const pose = morphPose(P(10.37, 20.61));
    const html = at(0, <Card w={540.3} h={340.2} pose={pose} pages={[{dx: 6, dy: 6, dz: -4, rz: 1, op: 1}]}>content</Card>, 'tilt');
    expect(html).toMatch(/left:-270px;top:-170px/);
    const sx = (540.3 / 540).toFixed(6);
    const sy = (340.2 / 340).toFixed(6);
    expect(html).toContain(`scale(${sx}, ${sy})`); // the face and every layer
    expect(html).toContain(`rotateZ(1deg) scale(${sx}, ${sy})`); // the page behind it
    expect(html).toContain(`scale(${(540 / 540.3).toFixed(6)}, ${(340 / 340.2).toFixed(6)})`); // the content back
    expect(html).toMatch(/border-radius:[\d.]+px \/ [\d.]+px/); // the radius drawn after the scale is the true radius
    // hint mode ignores the mark: the card draws as it always did
    expect(at(0, <Card w={540.3} h={340.2} pose={pose}>content</Card>, 'hint')).toBe(at(0, <Card w={540.3} h={340.2} pose={P(10.37, 20.61)}>content</Card>));
  });
});

describe('running text in tilt mode (StreamText)', () => {
  it('tilts the stream as one block and fades each character by its colour, never an opacity of its own', () => {
    const lines = [[{text: 'Steady'}, {text: ' now', bold: true}]];
    const hint = at(0, <StreamText lines={lines} revealed={4} />);
    expect(hint).toContain('opacity:0');
    const tilt = at(0, <StreamText lines={lines} revealed={4} />, 'tilt');
    expect(tilt).toMatch(/^<div data-slate-tilt="type"/);
    expect(tilt).not.toContain('opacity');
    expect(tilt).not.toContain('inline-block'); // characters stay inline, so the line keeps its kerning
    expect(tilt).toMatch(/color:rgba\(\d+,\d+,\d+,0\)/); // the characters still to come, laid out but clear
  });
});
