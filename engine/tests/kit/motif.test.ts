// The motif rig: one clock, three phase relationships, calm at rest, turns that land on themselves.
import {beforeEach, describe, expect, it} from 'vitest';
import {brokenMargins, characterAcceptance, checkTrack, clearMargins, clearMotifTraces, clearMotions, defineMargin, gestureSpans, groupOf, isCalm, margin, margins, motifBeats, motifLint, motifPose, motifSpec, motifTraces, motions, placeParts, registerMotifTrace, setBrand, setFilmData, STILL_FRAMES, traceMotions, type MotifTrack, type MotifTraceRecord} from '@slate/kit/core';
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
    // a turn and the echo that rings out with it share a contact frame: one sound plays, by priority
    const beats = motifBeats([{kind: 'turn', frame: 40}, {kind: 'ripple', frame: 40}, {kind: 'wave', frame: 80}], spec);
    const frames = beats.map((b) => b.frame);
    expect(new Set(frames).size).toBe(frames.length);
    expect(beats.find((b) => b.frame === 40)?.gesture).toBe('turn'); // priority
    expect(beats.filter((b) => b.gesture === 'wave').length).toBe(spec.parts);
    expect(beats.every((b) => b.kind === 'character' && Number.isInteger(b.frame))).toBe(true);
  });

  it("keys a gesture's contact beat to its word and its stressed syllable, for the sync gate", () => {
    // a turn keyed to "steady" (its stressed syllable at 1.30-1.42 s), a wave keyed to a word with no syllable known
    const track: MotifTrack = [
      {kind: 'turn', t: 1.3, word: 'steady', stress_start: 1.3, stress_end: 1.42},
      {kind: 'wave', frame: 90, word: 'again', occ: 2},
    ];
    const beats = motifBeats(track, spec);
    const turn = beats.find((b) => b.gesture === 'turn')!;
    expect(turn).toMatchObject({word: 'steady', stress_start: 1.3, stress_end: 1.42, kind: 'character'});
    const waves = beats.filter((b) => b.gesture === 'wave');
    expect(waves[0]).toMatchObject({word: 'again', occ: 2});
    expect('stress_end' in waves[0]).toBe(false); // no syllable known: the gate uses the word's end
    expect(waves.slice(1).every((b) => b.word === undefined)).toBe(true); // the wave's later steps follow its contact
  });

  it("lands a gesture keyed to a stressed syllable on the contact: the syllable's first frame plus 2", () => {
    const fps = FILM.film.fps_num / FILM.film.fps_den;
    const [beat] = motifBeats([{kind: 'turn', word: 'now', stress_start: 2.0, stress_end: 2.2}], spec);
    expect(beat.frame).toBe(Math.round(2.0 * fps) + 2);
    expect(beat).toMatchObject({word: 'now', stress_start: 2.0, stress_end: 2.2});
  });

  it("keeps a word on its frame's one sound when a gesture of higher priority takes the frame", () => {
    const beats = motifBeats([{kind: 'ripple', frame: 40, word: 'land'}, {kind: 'turn', frame: 40}], spec);
    expect(beats).toHaveLength(1);
    expect(beats[0]).toMatchObject({gesture: 'turn', word: 'land'});
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

// The combination rules (the films' character spec §2.13; L-kit-D081): the rig switches off what they forbid, so the
// picture and the sound never show it, and motifLint lists it. One case per rule.
describe('how gestures combine', () => {
  const opts = {spec, layout: big, short: 540};
  /** The pose with a gesture and without it: switched off means the picture is exactly as if it weren't there. */
  const sameWithout = (track: MotifTrack, drop: number, frames: number[]) => {
    const rest = track.filter((_, i) => i !== drop);
    for (const f of frames) expect(motifPose(track, f, opts)).toEqual(motifPose(rest, f, opts));
  };

  it('runs nothing momentary during a hold, and lists it', () => {
    const track: MotifTrack = [{kind: 'hold', frame: 30, untilFrame: 80}, {kind: 'pulse', frame: 55}];
    const r = checkTrack(track, opts);
    expect(r.off.map((o) => o.g.kind)).toEqual(['pulse']);
    expect(motifLint(track, opts)[0]).toMatch(/nothing runs during a hold/);
    sameWithout(track, 1, [40, 50, 55, 60, 70]);
    // the home that releases a hold runs (it's the release)
    expect(checkTrack([{kind: 'hold', frame: 30}, {kind: 'home', frame: 80}], opts).off).toEqual([]);
  });

  it('runs only a lean or a hold while parts are away, and lists the rest', () => {
    const track: MotifTrack = [
      {kind: 'exit', frame: 20},
      {kind: 'pulse', frame: 70},
      {kind: 'lean', frame: 80, toward: {x: 800, y: 270}},
      {kind: 'enter', frame: 140},
      {kind: 'pulse', frame: 200},
    ];
    const r = checkTrack(track, opts);
    expect(r.off.map((o) => `${o.g.kind}@${o.C}`)).toEqual(['pulse@70']);
    expect(r.off[0].reason).toMatch(/parts are away/);
    expect(r.allowed.map((g) => g.kind)).toEqual(['exit', 'lean', 'enter', 'pulse']);
    sameWithout(track, 1, [60, 70, 75]);
    // parts that haven't arrived yet (a track that starts with an enter) are away too
    const early = checkTrack([{kind: 'turn', frame: 10}, {kind: 'enter', frame: 60}], opts);
    expect(early.off.map((o) => o.g.kind)).toEqual(['turn']);
    expect(early.off[0].reason).toMatch(/haven't arrived/);
  });

  it('never runs two gestures on one channel at once, and lists the second', () => {
    // a lean is held (not momentary), so it may overlap a momentary gesture, but never another move of the offset
    const leans: MotifTrack = [{kind: 'lean', frame: 30, toward: {x: 800, y: 270}}, {kind: 'lean', frame: 36, toward: {x: 480, y: 600}}];
    const r = checkTrack(leans, opts);
    expect(r.off.map((o) => o.index)).toEqual([1]);
    expect(r.off[0].reason).toMatch(/one channel at once.*offset/);
    sameWithout(leans, 1, [25, 30, 36, 40]);
    // different channels may overlap: a lean while a turn runs
    expect(checkTrack([{kind: 'turn', frame: 40}, {kind: 'lean', frame: 40, toward: {x: 800, y: 270}}], opts).off).toEqual([]);
  });

  it('runs one momentary gesture at a time, even on different channels', () => {
    const track: MotifTrack = [{kind: 'grow', frame: 40}, {kind: 'turn', frame: 42}];
    const r = checkTrack(track, opts);
    expect(r.off.map((o) => o.g.kind)).toEqual(['turn']);
    expect(r.off[0].reason).toMatch(/one momentary gesture at a time/);
    sameWithout(track, 1, [36, 40, 44, 50]);
  });

  it('lists momentary gestures closer than 4 still frames, and lets them run', () => {
    const [pulse] = gestureSpans([{kind: 'pulse', frame: 30}], opts);
    const [grow] = gestureSpans([{kind: 'grow', frame: 0}], opts);
    // the grow starts 2 still frames after the pulse ends
    const close: MotifTrack = [{kind: 'pulse', frame: 30}, {kind: 'grow', frame: pulse.end + 2 - grow.start}];
    expect(checkTrack(close, opts).off).toEqual([]);
    const lint = motifLint(close, opts);
    expect(lint).toHaveLength(1);
    expect(lint[0]).toMatch(/2 still frames between them \(keep 4 still frames/);
    // exactly 4 still frames is clean
    const spaced: MotifTrack = [{kind: 'pulse', frame: 30}, {kind: 'grow', frame: pulse.end + STILL_FRAMES - grow.start}];
    expect(motifLint(spaced, opts)).toEqual([]);
  });

  it('makes no sound for a gesture it switches off', () => {
    const beats = motifBeats([{kind: 'hold', frame: 30, untilFrame: 80}, {kind: 'turn', frame: 55}], spec, {layout: big, short: 540});
    expect(beats.map((b) => b.gesture)).toEqual(['hold']);
  });

  it("gives a clean track an empty lint, and spaces a meaning's own steps", () => {
    const clean: MotifTrack = [{kind: 'enter', frame: 40}, {kind: 'pulse', frame: 90}, {kind: 'turn', frame: 140}, {kind: 'hold', frame: 200, untilFrame: 240}, {kind: 'home', frame: 300}];
    expect(motifLint(clean, opts)).toEqual([]);
  });
});

// The hand-in check's character gate runs these on every mark an act registers (L-kit-077 and the motion lessons).
describe('character acceptance', () => {
  const layout = () => ({x: 480, y: 270, size: 120});
  const record = (track: MotifTrack, extra: Partial<MotifTraceRecord> = {}): MotifTraceRecord => ({rig: 'mark', shot: 'K01', track, layout, from: 0, to: 120, spec, short: 540, character: true, ...extra});

  it('passes a clean track: calm at both ends and after each gesture, still between them', () => {
    const r = characterAcceptance(record([{kind: 'pulse', frame: 30}, {kind: 'turn', frame: 70}]));
    expect(r.problems).toEqual([]);
    expect(r.checks.join(' ')).toMatch(/lint: clean/);
  });

  it('fails a track the combination rules object to', () => {
    const r = characterAcceptance(record([{kind: 'pulse', frame: 30}, {kind: 'turn', frame: 32}]));
    expect(r.problems.join('\n')).toMatch(/one momentary gesture at a time/);
  });

  it("fails a mark that isn't calm at the act's end", () => {
    const r = characterAcceptance(record([{kind: 'breathe', frame: 100}]), {act: {from: 0, to: 121}});
    expect(r.problems.join('\n')).toMatch(/not calm at the act's end \(frame 120\)/);
  });

  it('fails a mark that floats between gestures', () => {
    const floating = (f: number) => ({x: 480, y: 270, size: 120 + Math.sin(f / 7) * 3});
    const r = characterAcceptance(record([{kind: 'pulse', frame: 30}], {layout: floating}));
    expect(r.problems.join('\n')).toMatch(/moves between gestures \(frame \d+/);
    // a carried mark (its centre moving, its shape not) isn't floating
    const carried = (f: number) => ({x: 400 + f * 2, y: 270, size: 120});
    expect(characterAcceptance(record([{kind: 'pulse', frame: 30}], {layout: carried})).problems).toEqual([]);
  });

  it('fails a gesture that dithers across a fused mark\'s split point', () => {
    // a fused mark (parts overlapping at rest) moved on a curve that doubles back: the spread crosses the split
    // point again and again instead of once out and once back
    setBrand({motion: {eases: {wobble: [0.3, 2.5, 0.7, -1.5]}}, motif: {parts: 6, part_size: 0.33, ring: 0.3, curves: {move: 'wobble', land: 'spring0'}}} as never);
    const fused = motifSpec().spec;
    const r = characterAcceptance(record([{kind: 'pulse', frame: 30, amount: 0.2}], {spec: fused}));
    expect(r.problems.join('\n')).toMatch(/crosses the split point between parts \d and \d \d+ times/);
    // the same mark on a curve that doesn't double back crosses once out and once back
    setBrand({motion: {eases: {calm: [0.4, 0, 0.2, 1]}}, motif: {parts: 6, part_size: 0.33, ring: 0.3, curves: {move: 'calm', land: 'spring0'}}} as never);
    const steady = motifSpec().spec;
    expect(characterAcceptance(record([{kind: 'pulse', frame: 30, amount: 0.2}], {spec: steady})).problems).toEqual([]);
  });

  it('fails a clipping container that cuts into the clear circle while the mark moves, and passes one with room', () => {
    // the disc is 120 / 0.5 = 240 px: a clear circle of 0.85 × 240 = 204 px round its centre
    const tight = characterAcceptance(record([{kind: 'pulse', frame: 30}], {clip: {x: 300, y: 100, w: 360, h: 340}}));
    expect(tight.problems.join('\n')).toMatch(/clipping container cuts into it while it moves \(frame/);
    const roomy = characterAcceptance(record([{kind: 'pulse', frame: 30}], {clip: {x: 0, y: 0, w: 960, h: 540}}));
    expect(roomy.problems).toEqual([]);
  });

  it('keeps every registered mark for the hand-in check', () => {
    clearMotifTraces();
    registerMotifTrace([{kind: 'pulse', frame: 30}], {rig: 'mark', shot: 'K01', layout, from: 0, to: 90, short: 540, character: true});
    const [t] = motifTraces();
    expect(t.rig).toBe('mark');
    expect(t.character).toBe(true);
    expect(characterAcceptance(t).problems).toEqual([]);
  });
});

describe('timing margins', () => {
  it('records the slack between two beats and finds the broken ones', () => {
    clearMargins();
    expect(margin('the card lands before "report"', 2.0, 2.4, 0.1)).toBeCloseTo(0.3, 12);
    margin('the chat settles before it goes', 5.0, 4.9);
    defineMargin({name: 'worked out by the act', slack: Number.NaN});
    expect(margins().map((m) => m.name)).toHaveLength(3);
    expect(brokenMargins().map((m) => m.name)).toEqual(['the chat settles before it goes', 'worked out by the act']);
    clearMargins();
    expect(margins()).toEqual([]);
  });
});
