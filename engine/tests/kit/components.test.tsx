// Every component, rendered to markup with no browser (react-dom/server): it draws, it's a pure function of film time,
// it follows the frame's size and the brand's look, and it carries the brand's tokens.
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {beforeEach, describe, expect, it} from 'vitest';
import {
  Arrive,
  BarChart,
  Button,
  Camera,
  Captions,
  Card,
  CheckRow,
  Chip,
  CountUp,
  DistributionCurve,
  FadeCard,
  Field,
  Ground,
  KineticStack,
  KineticWords,
  KitProvider,
  KitReviewSheet,
  LineChart,
  LogoBuild,
  LogoSting,
  LookAtCamera,
  MarkCharacter,
  MotifMark,
  P,
  PerChar,
  PerWord,
  PlaceholderAct,
  RadarChart,
  RingChart,
  StreamText,
  TokenScope,
  WorldLayer,
  cam,
  camAt,
  clearMisses,
  filmFrameOf,
  gesture,
  logoPlan,
  misses,
  motifSpec,
  project,
  setBrand,
  setFilmData,
  stingLength,
  t90Of,
  word,
} from '@slate/kit';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

/** The visible text of some markup (tags gone, spaces kept). */
const text = (html: string) => html.replace(/<[^>]+>/g, '').replace(/&#x27;/g, "'").replace(/&amp;/g, '&');

const at = (t: number, node: React.ReactNode, size = {width: 960, height: 540}, look?: string) =>
  renderToStaticMarkup(
    <KitProvider t={t} width={size.width} height={size.height} look={look}>
      {node}
    </KitProvider>,
  );

describe('the clock root', () => {
  it('spreads motion-blur samples across a centred 180° shutter', () => {
    expect(filmFrameOf(10, 100, 1)).toBe(110);
    // 4 samples of master frame 2 (composition frames 8–11): 1.8125, 1.9375, 2.0625, 2.1875 → centred on 2
    const s = [8, 9, 10, 11].map((F) => filmFrameOf(F, 0, 4) as number);
    expect(s).toEqual([1.8125, 1.9375, 2.0625, 2.1875]);
    expect((s[0] + s[3]) / 2).toBeCloseTo(2, 12);
  });
});

describe('components draw, as pure functions of time', () => {
  it('draws the same markup for the same film time', () => {
    const node = <PerChar text="Hello there" t0={0.2} />;
    expect(at(0.3, node)).toBe(at(0.3, node));
    expect(at(0.3, node)).not.toBe(at(0.5, node));
  });

  it('draws type, UI and charts in the brand tokens', () => {
    const html = at(
      2,
      <TokenScope>
        <PerWord text="Lines per word" t0={0} />
        <Arrive t0={0}>
          <Button label="Continue" />
        </Arrive>
        <Chip label="Fine" tone="ok" />
        <Field label="Name" value="A value" />
        <CheckRow title="A step" state="done" meta="DONE" />
        <DistributionCurve dist={{mean: 0, sd: 1, min: -3, max: 3}} w={300} h={100} />
        <BarChart values={[1, 2, 3]} w={200} h={100} />
        <LineChart values={[1, 3, 2]} w={200} h={80} />
        <RadarChart size={200} spokes={['A', 'B', 'C']} series={[[0.5, 0.7, 0.9]]} />
        <RingChart size={100} value={0.5} />
        <StreamText lines={[[{text: 'Streaming '}, {text: 'answer', bold: true}]]} revealed={9} />
      </TokenScope>,
    );
    for (const words of ['Continue', 'Fine', 'A value', 'A step', 'DONE', 'Streaming answer', 'Lines per word']) expect(text(html)).toContain(words);
    expect(html).toContain('#3456A6'); // the fixture's accent
    expect(html).toContain('Helvetica Neue'); // the fixture's face
    expect(html).toContain('--slate-color-accent');
  });

  it('holds each figure of a count-up for three whole frames', () => {
    const frames = [0, 1, 2, 3, 4, 5].map((f) => at(0.1 + f / 25 + 0.001, <CountUp t0={0.1} from={0} to={100} duration={0.8} />));
    expect(frames[0]).toBe(frames[1]);
    expect(frames[1]).toBe(frames[2]);
    expect(frames[3]).not.toBe(frames[2]);
    expect(frames[3]).toBe(frames[4]);
  });
});

describe('the look decides what a card is', () => {
  const count = (html: string) => (html.match(/translateZ\(/g) ?? []).length;
  it('stacks paper thickness only in a papery look', () => {
    const card = <Card w={300} h={200} pose={P()} />;
    expect(count(at(0, card, undefined, 'paper-2.5d'))).toBe(6);
    expect(count(at(0, card, undefined, 'flat'))).toBe(0);
  });
  it('fades a translucent card as one flat sheet', () => {
    const html = at(0, <FadeCard w={300} h={200} pose={{...P(), op: 0.5}} />, undefined, 'paper-2.5d');
    expect(html).toMatch(/opacity:0\.5/);
  });
  it('grounds and cameras follow the frame', () => {
    const small = at(0, <Camera z={100}><div /></Camera>);
    const big = at(0, <Camera z={100}><div /></Camera>, {width: 1920, height: 1080});
    expect(small).toContain('left:480px');
    expect(big).toContain('left:960px');
    expect(at(0, <Ground dark />)).toContain('#1C2233');
  });
});

describe('sizes follow the frame, never a fixed resolution', () => {
  it('doubles type when the frame doubles', () => {
    const size = (html: string) => Number(html.match(/font-size:([\d.]+)px/)![1]);
    const a = size(at(0, <PerWord text="x" t0={-1} role="body" />));
    const b = size(at(0, <PerWord text="x" t0={-1} role="body" />, {width: 1920, height: 1080}));
    expect(b).toBeCloseTo(2 * a, 6);
    // a vertical frame keeps the same type size on the same short side
    const v = size(at(0, <PerWord text="x" t0={-1} role="body" />, {width: 540, height: 960}));
    expect(v).toBeCloseTo(a, 6);
  });
});

describe('cameras', () => {
  it('chains look-at moves on the brand eases and projects points', () => {
    const P0 = 1000;
    const c = camAt(2, cam(0, 0, 1), [{t0: 1, dur: 1, to: cam(100, 0, 2)}], P0);
    expect(c.x).toBeCloseTo(100, 9);
    expect(c.s).toBeCloseTo(2, 9);
    expect(project(c, 100, 0, 0, P0, 960, 540)).toMatchObject({x: 480, y: 270});
    expect(at(0, <LookAtCamera c={c}><div /></LookAtCamera>)).toContain('perspective');
  });
  it('keeps a world layer untransformed at rest (bit-identical to screen space)', () => {
    expect(at(0, <WorldLayer cam={{x: 0, y: 0, z: 0}}><div id="m" /></WorldLayer>)).not.toContain('scale(');
    expect(at(0, <WorldLayer cam={{x: 10, y: 0, z: 100}}><div /></WorldLayer>)).toContain('scale(');
  });
});

describe('the logo build and sting', () => {
  it('surveys first, then fills, then lands the keystone', () => {
    const node = <LogoBuild t0={0} x={100} y={100} w={400} />;
    const plan = logoPlan()!;
    const early = at(0.05, node);
    const late = at(plan.times.end + 0.5, node);
    const fills = (html: string) => (html.match(/clip-path="url\(/g) ?? []).length;
    expect(fills(early)).toBe(0);
    expect(fills(late)).toBe(4);
    expect(late).toContain('#3456A6'); // the keystone in the artwork's own colour
    expect(early).toContain('stroke-dasharray'); // the survey
  });
  it('clips each fill on an untransformed group (a clip on the transformed path itself clipped every fill away)', () => {
    const html = at(logoPlan()!.times.end, <LogoBuild t0={0} x={100} y={100} w={400} />);
    const clipped = html.match(/<[a-z]+ [^>]*clip-path="url\(#[^"]+-f\d+\)"[^>]*>/g) ?? [];
    expect(clipped.length).toBe(4);
    for (const tag of clipped) {
      expect(tag.startsWith('<g ')).toBe(true);
      expect(tag).not.toMatch(/transform=/);
    }
  });
  it('settles to the plain resting logo by settleBy', () => {
    const plan = logoPlan()!;
    const end = plan.times.end + 1;
    const settled = at(end, <LogoBuild t0={0} x={0} y={0} w={400} settleBy={end - 0.01} />);
    expect(settled).toContain('opacity="0"');
  });
  it('builds a sting with a tagline and knows its length', () => {
    expect(stingLength()).toBeGreaterThan(logoPlan()!.times.end);
    expect(text(at(3, <LogoSting t0={0} tagline="A line" />))).toContain('A line');
  });
});

describe('the motif and the character', () => {
  it('draws every part at rest', () => {
    const {spec} = motifSpec();
    const html = at(0, <MotifMark x={200} y={200} size={200} />);
    expect((html.match(/<rect /g) ?? []).length).toBe(spec.parts);
  });
  it('draws the character: its disc and its mark', () => {
    const html = at(1, <MarkCharacter x={300} y={300} size={200} track={gesture('delight', {frame: 20})} />);
    expect(html).toContain('border-radius:50%');
    expect(html).toContain('#3456A6');
  });
  it('maps meanings to gestures', () => {
    expect(gesture('hello', {frame: 10}).map((g) => g.kind)).toEqual(['enter', 'grow']);
    expect(gesture('thinking', {frame: 10})[0].kind).toBe('breathe');
  });
});

describe('kinetic type, captions and the placeholder', () => {
  it('lands each word on its VO word, with no misses', () => {
    clearMisses();
    const land = word('K01', 'lands');
    const before = at(land - 0.5, <KineticWords shot="K01" text="Every move lands on its word" />);
    const after = at(land + 0.6, <KineticWords shot="K01" text="Every move lands on its word" />);
    expect(before).not.toBe(after);
    expect(after).toContain('lands');
    expect(misses()).toEqual([]);
  });
  it('stacks lines, one replacing the next', () => {
    const html = at(4.5, <KineticStack shot="K02" lines={['64 parts', 'move on one clock']} />);
    expect(html).toContain('parts');
  });
  it('burns in the cue on screen, and nothing between cues', () => {
    expect(at(1.2, <Captions />)).toContain('Every move lands on its word.');
    expect(at(3, <Captions />)).toBe('');
  });
  it('shows each shot with its timing', () => {
    const html = at(4.5, <PlaceholderAct act={1} />);
    expect(html).toContain('K02');
    expect(html).toContain('placeholder');
    expect(html).toContain('frames 100–224');
  });
  it('puts every component on one review sheet', () => {
    const html = at(1.5, <KitReviewSheet />);
    for (const label of ['Palette', 'Type', 'Motion', 'Cards and app UI', 'Charts', 'Logo build', 'Motif', 'Type in motion', 'Captions']) expect(html).toContain(label);
    // the brand's own eases are on it, by name, each with its T90
    for (const name of ['enter', 'move', 'state', 'leave']) expect(text(html)).toContain(`${name} ${t90Of(name).toFixed(3)}`);
  });
});
