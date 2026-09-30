// emit-events: the one emitter. Beats come from the act's own modules (and the kit's registry), are checked the way the
// films' emitters checked them, and land in timing/visual_events/actN.json; an act with no beats must say why.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {cli, film, kitCore, ok, readJson, scratch, twoActs} from './helpers.mjs';

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
  });
});
