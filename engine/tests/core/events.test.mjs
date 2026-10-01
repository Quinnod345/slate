// emit-events: the one emitter. Beats come from the act's own modules (and the kit's registry), are checked the way the
// films' emitters checked them, and land in timing/visual_events/actN.json; an act with no beats must say why.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {cli, film, kitCore, ok, readJson, scratch, twoActs, writeJson} from './helpers.mjs';

let root;
let data;
beforeAll(() => {
  root = scratch('events');
  data = path.join(root, 'data');
});
afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

/** A variant of the fixture whose act 1 exports different beats. */
function withEvents(name, eventsSource) {
  return film(root, name, (d) => {
    const f = path.join(d, 'scenes/act1/index.tsx');
    const text = fs.readFileSync(f, 'utf8');
    const start = text.indexOf('export const events = [');
    const end = text.indexOf('];', start) + 2;
    fs.writeFileSync(f, text.slice(0, start) + eventsSource + text.slice(end));
  });
}

describe('emit-events', () => {
  it("writes the act's beats, sorted, checked, from the module the picture uses", () => {
    const dir = film(root, 'basic');
    const r = ok('emit-events', {film: dir}, {data});
    expect(r.acts).toHaveLength(1);
    expect(r.acts[0]).toMatchObject({act: 1, file: 'timing/visual_events/act1.json', silent: false, events: 2, kinds: {whoosh: 1, land: 1}, sources: ['scenes/act1/index.tsx']});
    const beats = readJson(path.join(dir, 'timing/visual_events/act1.json'));
    expect(beats).toEqual([
      {shot: 'S01', id: 'square_enters', t: 0.1, kind: 'whoosh', size: 's', note: 'The square slides in from the left.'},
      {shot: 'S02', id: 'square_crosses_centre', t: 20 / 30, kind: 'land', size: 'm', note: 'The square reaches the middle of the frame.'},
    ]);
    expect(r.acts[0].motion).toEqual({written: false, reason: "the act doesn't use the kit, so the kit's motion registry has nothing for it"});
  });

  it('writes {silent: true, reason} for an act that says it has no beats', () => {
    const dir = withEvents('silent', "export const silent = 'a held title card: nothing lands';");
    ok('emit-events', {film: dir, act: 1}, {data});
    expect(readJson(path.join(dir, 'timing/visual_events/act1.json'))).toEqual({silent: true, reason: 'a held title card: nothing lands', generated: 'do not edit by hand: slate emit-events'});
  });

  it('refuses an act with no beats that does not say it is silent', () => {
    const dir = withEvents('mute', 'export const events = [];');
    const r = cli('emit-events', {film: dir}, {data});
    expect(r.code).toBe(1);
    expect(r.json).toMatchObject({ok: false, kind: 'refused'});
    expect(r.json.error).toMatch(/no beats and doesn't say it's silent/);
    expect(fs.existsSync(path.join(dir, 'timing/visual_events/act1.json'))).toBe(false);
  });

  it('refuses beats that break the rules, naming each problem', () => {
    const dir = withEvents(
      'bad',
      [
        'export const events = [',
        "  {shot: 'S01', id: 'a', t: 0.1, kind: 'pop', size: 's', note: 'a kind the films used once'},",
        "  {shot: 'S01', id: 'a', t: 0.2, kind: 'impact', size: 'xl', note: 'twice'},",
        "  {shot: 'S01', id: 'c', t: 0.3, kind: 'impact', size: 'l', note: ''},",
        "  {shot: 'S02', id: 'd', t: 0.1, kind: 'tap', size: 'm', note: 'wrong shot for its time'},",
        "  {shot: 'S09', id: 'e', t: 5, kind: 'tap', size: 'm', note: 'outside the act'},",
        "  {shot: 'S02', id: 'f', t: 0.9, kind: 'count', size: 's', note: 'a count with bad extras', from: 'zero', dur: -1, pan: 2, x: 'left'},",
        '];',
      ].join('\n'),
    );
    const r = cli('emit-events', {film: dir}, {data});
    expect(r.code).toBe(1);
    const problems = r.json.details.problems.join('\n');
    expect(problems).toMatch(/kind "pop" isn't one of tap, type, land, snap, count, draw, data, page, stamp, whoosh, swell, character, impact/);
    expect(problems).toMatch(/the id "a" is used twice/);
    expect(problems).toMatch(/size "xl" isn't s, m or l/);
    expect(problems).toMatch(/needs a note/);
    expect(problems).toMatch(/says S02 but lands at 0\.100 s/);
    expect(problems).toMatch(/outside act 1/);
    expect(problems).toMatch(/2 impacts in act 1: at most one/);
    expect(problems).toMatch(/\(f\): from must be a number/);
    expect(problems).toMatch(/\(f\): x must be a number/);
    expect(problems).toMatch(/\(f\): a count runs over time, so it needs start and dur \(it ends on t\)/);
    expect(problems).toMatch(/\(f\): pan must be a number from -1 \(left\) to 1 \(right\)/);
  });

  it('holds every span to its beat, word for word as slate.timing.beats.problems() does', () => {
    // a draw and a count end on t; a whoosh peaks inside its span; start and dur go together
    const good = withEvents(
      'spans-ok',
      [
        'export const events = [',
        "  {shot: 'S01', id: 'rule', t: 0.3, start: 0.1, dur: 0.2, kind: 'draw', size: 's', note: 'a rule draws across'},",
        "  {shot: 'S02', id: 'tally', t: 0.9, start: 0.6, dur: 0.3, kind: 'count', size: 'm', note: 'the tally counts up', from: 0, to: 64},",
        "  {shot: 'S01', id: 'rush', t: 0.25, start: 0.1, dur: 0.4, kind: 'whoosh', size: 's', note: 'the card rushes past'},",
        '];',
      ].join('\n'),
    );
    const r = ok('emit-events', {film: good}, {data});
    expect(r.acts[0]).toMatchObject({events: 3, kinds: {draw: 1, count: 1, whoosh: 1}});
    expect(readJson(path.join(good, 'timing/visual_events/act1.json')).find((b) => b.id === 'rule')).toMatchObject({start: 0.1, dur: 0.2, t: 0.3});

    const bad = withEvents(
      'spans-bad',
      [
        'export const events = [',
        "  {shot: 'S01', id: 'no_span', t: 0.3, kind: 'draw', size: 's', note: 'a draw with no span'},",
        "  {shot: 'S01', id: 'starts_on_t', t: 0.1, start: 0.1, dur: 0.2, kind: 'draw', size: 's', note: 'timed at its start, as one film did'},",
        "  {shot: 'S01', id: 'half', t: 0.2, start: 0.1, kind: 'tap', size: 's', note: 'a start with no dur'},",
        "  {shot: 'S02', id: 'backwards', t: 0.7, start: 0.8, dur: 0, kind: 'land', size: 's', note: 'a span of nothing'},",
        "  {shot: 'S02', id: 'late_peak', t: 0.95, start: 0.5, dur: 0.2, kind: 'swell', size: 'm', note: 'peaks after its span'},",
        '];',
      ].join('\n'),
    );
    const res = cli('emit-events', {film: bad}, {data});
    expect(res.code).toBe(1);
    const problems = res.json.details.problems.join('\n');
    expect(problems).toMatch(/\(no_span\): a draw runs over time, so it needs start and dur \(it ends on t\)/);
    expect(problems).toMatch(/\(starts_on_t\): a draw ends on its beat, but start \+ dur is 0\.300 s and t is 0\.100 s \(\+200 ms\)/);
    expect(problems).toMatch(/\(half\): start and dur go together \(the span a sustained motion runs over\)/);
    expect(problems).toMatch(/\(backwards\): the span 0\.800–0\.800 s isn't a forward stretch of film time/);
    expect(problems).toMatch(/\(late_peak\): a swell peaks inside its span, but t 0\.950 s is outside 0\.500–0\.700 s/);
    expect(fs.existsSync(path.join(bad, 'timing/visual_events/act1.json'))).toBe(false);
  });

  it('refuses an act whose module throws while loading, and says why', () => {
    const dir = film(root, 'throws', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8') + "\nthrow new Error('the narrator never says that word');\n");
    });
    const r = cli('emit-events', {film: dir}, {data});
    expect(r.code).toBe(1);
    expect(r.json.error).toMatch(/throws while loading: the narrator never says that word/);
  });

  it('skips an act that is not built yet when emitting every act, and refuses it by name', () => {
    const dir = withEvents('partial', "export const events = [{shot: 'S01', id: 'square_enters', t: 0.1, kind: 'whoosh', size: 's', note: 'The square slides in.'}];");
    twoActs(dir);
    const r = ok('emit-events', {film: dir}, {data});
    expect(r.acts.map((a) => a.act)).toEqual([1]);
    expect(r.skipped).toEqual([{act: 2, reason: 'not built yet'}]);
    const one = cli('emit-events', {film: dir, act: 2}, {data});
    expect(one.code).toBe(2);
    expect(one.json.error).toMatch(/isn't built yet/);
  });

  it('in a dry run computes and checks every act, and writes nothing', () => {
    const dir = film(root, 'dry');
    const r = ok('emit-events', {film: dir, dry_run: true}, {data});
    expect(r.dry_run).toBe(true);
    expect(r.acts[0]).toMatchObject({act: 1, events: 2, dry_run: true, written: false, problems: [], misses: []});
    expect(fs.existsSync(path.join(dir, 'timing/visual_events'))).toBe(false);
  });

  it('in a dry run reports what would be refused, instead of refusing', () => {
    const dir = withEvents('dry-mute', 'export const events = [];');
    const r = ok('emit-events', {film: dir, dry_run: true}, {data});
    expect(r.acts[0].problems.join('\n')).toMatch(/no beats and doesn't say it's silent/);
    expect(fs.existsSync(path.join(dir, 'timing/visual_events/act1.json'))).toBe(false);
  });

  it("merges placed captures' taps into their acts, with their source, sizes spelled s/m/l, ties ordered by id", () => {
    const dir = film(root, 'captures', (d) => {
      writeJson(path.join(d, 'captures/demo/beats.json'), {
        capture: 'demo',
        film_in: 3,
        events: [
          {shot: 'S01', id: 'demo.tap1', t: 0.1, frame: 3, kind: 'tap', size: 'small', note: 'tap in capture demo', source: 'capture:demo'},
          {shot: 'S02', id: 'demo.type2', t: 0.8, frame: 24, kind: 'type', size: 'm', note: 'typing in capture demo'},
        ],
      });
      writeJson(path.join(d, 'captures/later/beats.json'), {capture: 'later', film_in: null, events: []});
    });
    const r = ok('emit-events', {film: dir}, {data});
    expect(r.acts[0]).toMatchObject({events: 4, capture_beats: 2, kinds: {whoosh: 1, land: 1, tap: 1, type: 1}});
    expect(r.acts[0].notes.join('\n')).toMatch(/capture later isn't placed on the film clock yet/);
    const beats = readJson(path.join(dir, 'timing/visual_events/act1.json'));
    // demo.tap1 and square_enters both land at 0.1 s: ids break the tie
    expect(beats.map((b) => b.id)).toEqual(['demo.tap1', 'square_enters', 'square_crosses_centre', 'demo.type2']);
    expect(beats[0]).toMatchObject({size: 's', source: 'capture:demo', frame: 3});
    expect(beats[3]).toMatchObject({size: 'm', source: 'capture:demo'});
  });

  it('refuses a capture tap that no act holds, rather than dropping it', () => {
    const dir = film(root, 'stray', (d) => {
      writeJson(path.join(d, 'captures/demo/beats.json'), {capture: 'demo', film_in: 0, events: [{shot: 'S99', id: 'demo.lost', t: 9, kind: 'tap', size: 's', note: 'a tap past the end'}]});
    });
    const r = cli('emit-events', {film: dir}, {data});
    expect(r.code).toBe(1);
    expect(r.json.details.problems.join('\n')).toMatch(/a capture tap no act holds: captures\/demo\/beats\.json: demo\.lost at 9\.000 s on "S99" is in no act/);
  });

  it("allows a beat within half a frame of the act's end (the kit's rule) and refuses one past it", () => {
    const near = withEvents('edge-ok', "export const events = [{shot: 'S02', id: 'edge', t: 1 + 0.4 / 30, kind: 'land', size: 'm', note: 'lands on the last frame'}];");
    ok('emit-events', {film: near}, {data});
    const far = withEvents('edge-out', "export const events = [{shot: 'S02', id: 'edge', t: 1 + 0.6 / 30, kind: 'land', size: 'm', note: 'lands after the act'}];");
    const r = cli('emit-events', {film: far}, {data});
    expect(r.code).toBe(1);
    expect(r.json.details.problems.join('\n')).toMatch(/t 1\.020 s is outside act 1 \(0\.000–1\.000 s\)/);
  });

  it("hands a web act to the open web renderer's emit mode, then adds its captures' taps", () => {
    const dir = withEvents('web-act', "export const events = [{shot: 'S01', id: 'square_enters', t: 0.1, kind: 'whoosh', size: 's', note: 'The square slides in.'}];");
    twoActs(dir);
    fs.mkdirSync(path.join(dir, 'scenes/act2/web'), {recursive: true});
    fs.writeFileSync(path.join(dir, 'scenes/act2/web/index.html'), '<!doctype html><title>act 2</title>\n');
    writeJson(path.join(dir, 'captures/demo/beats.json'), {capture: 'demo', film_in: 15, events: [{shot: 'S02', id: 'demo.tap', t: 0.6, kind: 'tap', size: 'small', note: 'a tap in act 2'}]});
    // a stand-in for engine/web/render.mjs: writes what the page would say, and hands back what it was asked
    const fake = path.join(root, 'fake-web-emit.mjs');
    fs.writeFileSync(
      fake,
      [
        "import fs from 'node:fs';",
        "import path from 'node:path';",
        'export async function webRender(args) {',
        "  const beats = [{shot: 'S02', id: 'page.lands', t: 0.8, kind: 'land', size: 'm', note: 'the page lands'}];",
        "  const rel = `timing/visual_events/act${args.act}.json`;",
        '  if (args.dry_run) return {acts: [{act: args.act, file: rel, silent: false, events: 1, beats, problems: [], dry_run: true, written: false, asked: args}], skipped: [], dry_run: true};',
        "  const file = path.join(args.film, 'timing', 'visual_events', `act${args.act}.json`);",
        '  fs.mkdirSync(path.dirname(file), {recursive: true});',
        '  fs.writeFileSync(file, JSON.stringify(beats));',
        '  return {acts: [{act: args.act, file: rel, silent: false, events: 1, asked: args}], skipped: []};',
        '}',
        '',
      ].join('\n'),
    );
    const r = ok('emit-events', {film: dir}, {data, env: {SLATE_WEB_RENDER_MODULE: fake}});
    expect(r.acts.map((a) => [a.act, a.engine ?? 'remotion'])).toEqual([[1, 'remotion'], [2, 'web']]);
    expect(r.acts[1].asked).toEqual({film: dir, act: 2, emit: true});
    expect(r.acts[1]).toMatchObject({events: 2, capture_beats: 1, kinds: {tap: 1, land: 1}});
    expect(readJson(path.join(dir, 'timing/visual_events/act2.json')).map((b) => [b.id, b.size])).toEqual([['demo.tap', 's'], ['page.lands', 'm']]);
    // a dry run asks the page too (dry_run: nothing written), and judges its beats with the captures' taps merged in
    const before = fs.readFileSync(path.join(dir, 'timing/visual_events/act2.json'), 'utf8');
    const dry = ok('emit-events', {film: dir, dry_run: true}, {data, env: {SLATE_WEB_RENDER_MODULE: fake}});
    expect(dry.skipped).toEqual([]);
    const web = dry.acts.find((a) => a.act === 2);
    expect(web.asked).toEqual({film: dir, act: 2, emit: true, dry_run: true});
    expect(web).toMatchObject({engine: 'web', events: 2, capture_beats: 1, problems: [], dry_run: true, written: false});
    expect(web.beats.map((b) => b.id)).toEqual(['demo.tap', 'page.lands']);
    expect(fs.readFileSync(path.join(dir, 'timing/visual_events/act2.json'), 'utf8')).toBe(before);
  });

  it.skipIf(!kitCore)("refuses to write while a word lookup falls back to an estimate (film C's rule)", () => {
    const dir = film(root, 'misses', (d) => {
      fs.writeFileSync(path.join(d, 'scenes/act1/beats.ts'), "import {word} from '@slate/kit/core';\nexport const said = word('S02', 'square');\n");
    });
    const r = cli('emit-events', {film: dir}, {data});
    expect(r.code).toBe(1);
    // the fixture has no VO at all, so the kit also records the read and the cue it couldn't find
    expect(r.json.error).toMatch(/3 word lookups fell back to an estimate \(.*S02:square\): the beats would land on guessed times/);
    expect(r.json.details.misses).toContain('S02:square');
    expect(fs.existsSync(path.join(dir, 'timing/visual_events/act1.json'))).toBe(false);
    const dry = ok('emit-events', {film: dir, dry_run: true}, {data});
    expect(dry.acts[0].misses).toEqual(r.json.details.misses);
  });

  it.skipIf(!kitCore)('refuses what the kit recorded about a bad defineEvent (one id, two different beats)', () => {
    const dir = film(root, 'kit-problems', (d) => {
      fs.writeFileSync(
        path.join(d, 'scenes/act1/beats.ts'),
        [
          "import {defineEvent} from '@slate/kit/core';",
          "defineEvent({shot: 'S01', id: 'twice', frame: 4, kind: 'tap', size: 's', note: 'first'});",
          "defineEvent({shot: 'S01', id: 'twice', frame: 6, kind: 'tap', size: 's', note: 'second'});",
          '',
        ].join('\n'),
      );
    });
    const r = cli('emit-events', {film: dir}, {data});
    expect(r.code).toBe(1);
    expect(r.json.details.problems.join('\n')).toMatch(/the kit: event "twice" in S01 is defined twice with different values/);
  });

  it.skipIf(!kitCore)("adds the kit's registered beats and writes the act's motion trace (§12 shape)", () => {
    const dir = film(root, 'kit', (d) => {
      fs.writeFileSync(
        path.join(d, 'scenes/act1/beats.ts'),
        [
          "import {defineEvent, defineMotion} from '@slate/kit/core';",
          "export const slide = defineMotion({id: 'square_slide', shot: 'S01', prop: 'x', frame_in: 0, frame_out: 12, from: 20, to: 116, entrance: true});",
          "export const parks = defineEvent({shot: 'S01', id: 'square_parks', frame: 12, kind: 'land', size: 'm', note: 'The square parks.'});",
          '',
        ].join('\n'),
      );
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8').replace("import {film, type SceneProps} from '@film/data';", "import {film, type SceneProps} from '@film/data';\nimport './beats';"));
    });
    const r = ok('emit-events', {film: dir}, {data});
    expect(r.acts[0].events).toBe(3);
    expect(r.acts[0].motion).toEqual({written: true, file: 'timing/motion/act1.json', entries: 1});
    const trace = readJson(path.join(dir, 'timing/motion/act1.json'));
    expect(Object.keys(trace[0]).sort()).toEqual(['ease', 'entrance', 'frame_in', 'frame_out', 'from', 'id', 'loop_period', 'prop', 'rig', 'samples', 'shot', 'to'].sort());
    expect(trace[0]).toMatchObject({id: 'square_slide', shot: 'S01', prop: 'x', frame_in: 0, frame_out: 12, from: 20, to: 116, entrance: true});
    expect(trace[0].samples).toHaveLength(13);
    expect(trace[0].samples[0]).toEqual([0, 20]);
    expect(trace[0].samples[12]).toEqual([12, 116]);
    const beats = readJson(path.join(dir, 'timing/visual_events/act1.json'));
    expect(beats.map((b) => b.id)).toEqual(['square_enters', 'square_parks', 'square_crosses_centre']);

    // `trace` writes the motion trace alone (slate render refreshes it after each act render), with what it read
    fs.rmSync(path.join(dir, 'timing/motion/act1.json'));
    const t = ok('trace', {film: dir, act: 1}, {data});
    expect(t).toMatchObject({act: 1, written: true, file: 'timing/motion/act1.json', entries: 1});
    expect(t.sources).toEqual(['scenes/act1/beats.ts', 'scenes/act1/index.tsx']);
    expect(readJson(path.join(dir, 'timing/motion/act1.json'))[0].id).toBe('square_slide');
  });

  it('trace: says why there is nothing to trace, and refuses a call without an act', () => {
    const dir = film(root, 'trace-plain');
    expect(ok('trace', {film: dir, act: 1}, {data})).toMatchObject({written: false, reason: "the act doesn't use the kit, so the kit's motion registry has nothing for it"});
    const web = film(root, 'trace-web', (d) => {
      fs.rmSync(path.join(d, 'scenes/act1/index.tsx'));
      fs.mkdirSync(path.join(d, 'scenes/act1/web'), {recursive: true});
      fs.writeFileSync(path.join(d, 'scenes/act1/web/index.html'), '<!doctype html><title>web act</title>\n');
    });
    expect(ok('trace', {film: web, act: 1}, {data})).toMatchObject({written: false, reason: expect.stringMatching(/web scene/)});
    expect(cli('trace', {film: dir}, {data}).code).toBe(2);
    expect(cli('trace', {film: dir, act: 9}, {data}).code).toBe(2);
  });
});
