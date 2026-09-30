// The '@slate/web' runtime in Node (GSAP runs happily on plain objects): brand eases with exact endpoints, tween() on
// whole film frames, the trace it writes, and the picture following the trace frame for frame. '@film/data' is the web
// fixture film (25 fps, one act of two shots, the web-fixture brand).
import {describe, expect, it} from 'vitest';
import {gsap} from 'gsap';
import {defineEvent, easeNamed, secOf} from '@slate/kit/core';
import {END_SNAP_FRAMES, actDuration, brandEase, defineScene, registerBrandEases, sequenceUrl, timeline, tween, tweenProblems, type SlateScene} from '@slate/web';

type Row = {id: string; shot: string; prop: string; ease: string; frame_in: number; frame_out: number; from: number; to: number; samples: [number, number][]; entrance: boolean; unit?: string};

const scene: SlateScene = defineScene({register: false, fonts: false});
const trace = (): Row[] => (scene.trace ? (scene.trace() as Row[]) : []);
const row = (id: string): Row => {
  const r = trace().find((x) => x.id === id);
  if (!r) throw new Error(`no trace row ${id}`);
  return r;
};
const FPS = 25;

describe('brand eases for GSAP', () => {
  it('start at exactly 0 and end at exactly 1, and follow the kit in between', () => {
    for (const name of ['enter', 'move', 'state', 'leave', 'linear']) {
      const e = brandEase(name, 10);
      expect(e(0)).toBe(0);
      expect(e(1)).toBe(1);
      expect(e(-0.2)).toBe(0);
      expect(e(1.3)).toBe(1);
      // GSAP's rounding (to 1e-7 s) can leave a whole frame a hair before a move's end: it still lands exactly
      expect(e(1 - 1e-9)).toBe(1);
      expect(e(1e-9)).toBe(0);
      for (const u of [0.1, 0.33, 0.5, 0.77, 0.9]) expect(e(u)).toBe(easeNamed(name)(u));
    }
    // the snap is far inside the smallest sub-frame sample offset (1/256 of a frame at 64 samples)
    expect(END_SNAP_FRAMES).toBeLessThan(1 / 256);
  });

  it('register with GSAP by name', () => {
    const names = registerBrandEases();
    expect(names).toEqual(expect.arrayContaining(['brand.enter', 'brand.move', 'brand.state', 'brand.leave', 'brand.entrance', 'brand.exit']));
    const parsed = gsap.parseEase('brand.enter') as (p: number) => number;
    expect(parsed(0.4)).toBe(easeNamed('enter')(0.4));
    expect(parsed(1)).toBe(1);
  });
});

describe('tween()', () => {
  it('lands on whole film frames and writes one trace row per property, endpoints exact', () => {
    const box = {x: 0, y: 7};
    const res = tween(box, {x: [10, 110], y: 57}, {id: 'unit.box', shot: 'W01', frame_in: 5, frame_out: 15, ease: 'move', unit: 'px'});
    expect(res).toMatchObject({ids: ['unit.box.x', 'unit.box.y'], shot: 'W01', ease: 'move', frame_in: 5, frame_out: 15, start: 0.2, duration: 0.4});
    const x = row('unit.box.x');
    expect(x).toMatchObject({shot: 'W01', prop: 'x', ease: 'move', frame_in: 5, frame_out: 15, from: 10, to: 110, entrance: false, unit: 'px'});
    expect(x.samples).toHaveLength(11);
    expect(x.samples[0]).toEqual([5, 10]);
    expect(x.samples[10]).toEqual([15, 110]);
    expect(row('unit.box.y')).toMatchObject({from: 7, to: 57}); // from where it stood
  });

  it('turns a start in film seconds and a duration token into frames, as slate.clock would', () => {
    const res = tween({o: 0}, {o: 1}, {id: 'unit.token', at: 0.2, dur: 'base', entrance: true});
    // base is 0.52 s: 13 frames at 25 fps; the entrance role is the brand's "enter"
    expect(res).toMatchObject({frame_in: 5, frame_out: 18, ease: 'enter', shot: 'W01'});
    expect(row('unit.token')).toMatchObject({entrance: true, ease: 'enter'});
    const late = tween({o: 0}, {o: 1}, {id: 'unit.late', at: 1.4, dur: 0.32});
    expect(late).toMatchObject({shot: 'W02', frame_in: 35, frame_out: 43}); // the shot its first frame falls in
  });

  it('draws exactly what the trace says, frame by frame, sub-frames in between', async () => {
    const box = {x: 0};
    tween(box, {x: [0, 200]}, {id: 'unit.follow', shot: 'W01', frame_in: 4, frame_out: 16, ease: 'enter'});
    const r = row('unit.follow');
    const at = new Map(r.samples);
    for (let f = 0; f <= 24; f++) {
      await scene.seek(f / FPS);
      const want = f < r.frame_in ? r.from : f > r.frame_out ? r.to : (at.get(f) as number);
      expect(Math.abs(box.x - want), `frame ${f}`).toBeLessThanOrEqual(1e-6);
    }
    await scene.seek(16 / FPS);
    expect(box.x).toBe(200); // exactly the end, not a hair short of it
    await scene.seek(4 / FPS);
    expect(box.x).toBe(0);
    await scene.seek(10.25 / FPS); // a motion-blur sample between frames 10 and 11
    const between = 200 * easeNamed('enter')((10.25 - 4) / 12);
    expect(Math.abs(box.x - between)).toBeLessThan(1e-4);
  });

  it('chains moves on one property: the second takes over where the first ended', async () => {
    const box = {x: 0};
    tween(box, {x: [0, 100]}, {id: 'unit.chain-1', frame_in: 20, frame_out: 25, ease: 'state'});
    tween(box, {x: 40}, {id: 'unit.chain-2', frame_in: 30, frame_out: 35, ease: 'state'});
    expect(row('unit.chain-2')).toMatchObject({from: 100, to: 40});
    for (const [f, want] of [[19, 0], [25, 100], [28, 100], [35, 40], [40, 40]] as const) {
      await scene.seek(f / FPS);
      expect(box.x, `frame ${f}`).toBe(want);
    }
  });

  it('records what it had to assume instead of throwing', () => {
    tween({q: 0}, {q: 1}, {id: 'unit.no-length', frame_in: 2});
    expect(tweenProblems().join('\n')).toMatch(/unit\.no-length" has no length/);
    expect(scene.problems ? scene.problems().join('\n') : '').toMatch(/no length/);
  });
});

describe('defineScene()', () => {
  it("takes its length from the film clock and seeks every timeline it made", async () => {
    expect(actDuration()).toBe(2);
    expect(scene.duration).toBe(2);
    const dot = {r: 0};
    timeline().to(dot, {r: 1, duration: 1, ease: 'none'}, 0.5);
    await scene.seek(1);
    expect(dot.r).toBeCloseTo(0.5, 9);
    await scene.seek(0);
    expect(dot.r).toBe(0);
  });

  it('hands over the beats the act defined with the kit', async () => {
    defineEvent({shot: 'W02', id: 'unit.beat', t: secOf(30), kind: 'tap', size: 's', note: 'a fixture tap'});
    const events = (typeof scene.events === 'function' ? await scene.events() : []) as {id: string; t: number}[];
    expect(events.find((e) => e.id === 'unit.beat')).toMatchObject({t: 1.2});
  });
});

describe('image sequences', () => {
  it('name the image a film frame shows, held at both ends', () => {
    const seq = {dir: 'captures/app/frames', count: 10};
    expect(sequenceUrl(seq, 0)).toBe('/_slate/film/captures/app/frames/f00000.jpg');
    expect(sequenceUrl(seq, 7)).toBe('/_slate/film/captures/app/frames/f00007.jpg');
    expect(sequenceUrl(seq, 99)).toBe('/_slate/film/captures/app/frames/f00009.jpg');
    expect(sequenceUrl({dir: './plate', pattern: 'p_%04d.png', first: 1, filmFrameIn: 25}, 26)).toBe('./plate/p_0002.png');
    expect(sequenceUrl({dir: './plate', pattern: 'p_%04d.png', first: 1, filmFrameIn: 25}, 3)).toBe('./plate/p_0001.png');
  });
});
