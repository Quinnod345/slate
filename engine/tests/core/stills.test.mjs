// Stills from one bundle: review stills at a scale, a labelled contact sheet with its index, and another entry's
// compositions (how the kit's review sheets render) reading the neutral film when no film is given.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {ENGINE, cli, film, ok, probe, readJson, scratch} from './helpers.mjs';

let root;
let data;
let dir;
let bundle;
beforeAll(() => {
  root = scratch('stills');
  data = path.join(root, 'data');
  dir = film(root, 'film');
  bundle = ok('bundle', {film: dir}, {data});
});
afterAll(() => {
  cli('bundle', {film: dir, release: true}, {data});
  fs.rmSync(root, {recursive: true, force: true});
});

describe('stills', () => {
  it('renders stills from any composition at a scale, named <comp>_f<frame>.png', () => {
    const out = path.join(root, 'stills');
    const r = ok('stills', {film: dir, serve_url: bundle.serve_url, specs: ['Act1:0', 'Film:29'], scale: 0.5, out_dir: out, pixels: true}, {data});
    expect(r.stills.map((s) => path.basename(s.file))).toEqual(['Act1_f00000.png', 'Film_f00029.png']);
    for (const s of r.stills) {
      expect(probe(s.file)).toMatchObject({width: 160, height: 90});
      expect(s.pixels).toMatch(/^[0-9a-f]{64}$/);
    }
    // the square has moved between frame 0 and frame 29
    expect(r.stills[0].pixels).not.toBe(r.stills[1].pixels);
  });

  it('tiles a labelled contact sheet and writes its index (frame, shot, timecode per cell)', () => {
    const out = path.join(root, 'sheet');
    const r = ok('stills', {film: dir, serve_url: bundle.serve_url, out_dir: out, contact_sheet: {comp: 'Act1', every_s: 0.25, columns: 3, scale: 0.5}}, {data});
    expect(r.sheet).toMatchObject({cells: 4, rows: 2, columns: 3, labels: true});
    // 3 cells of 160 across, 8 px padding and margins: 3 × 160 + 4 × 8
    expect(probe(r.sheet.file)).toMatchObject({width: 3 * 160 + 4 * 8, height: 2 * 90 + 3 * 8});
    const index = readJson(r.sheet.index);
    expect(index.cells.map((c) => [c.frame, c.shot, c.timecode])).toEqual([
      [0, 'S01', '00:00:00:00'],
      [8, 'S01', '00:00:00:08'],
      [15, 'S02', '00:00:00:15'],
      [23, 'S02', '00:00:00:23'],
    ]);
  });

  it('refuses a still that is not <composition>:<frame>, or a frame the composition does not have', () => {
    expect(cli('stills', {film: dir, serve_url: bundle.serve_url, specs: ['Act1']}, {data}).code).toBe(2);
    const r = cli('stills', {film: dir, serve_url: bundle.serve_url, specs: ['Act1:30'], out_dir: path.join(root, 'x')}, {data});
    expect(r.code).toBe(2);
    expect(r.json.error).toMatch(/frames 0–29/);
  });

  it("renders another entry's compositions (a review sheet), reading the neutral film when no film is given", () => {
    const entryDir = path.join(root, 'review');
    fs.mkdirSync(entryDir, {recursive: true});
    fs.writeFileSync(
      path.join(entryDir, 'entry.tsx'),
      [
        "import React from 'react';",
        "import {AbsoluteFill, Composition, registerRoot} from 'remotion';",
        "import {film} from '@film/data';",
        'const Sheet: React.FC = () => <AbsoluteFill style={{background: "#202020"}} />;',
        'registerRoot(() => <Composition id="Review-Sheet" component={Sheet} durationInFrames={1} fps={film.fps_num / film.fps_den} width={film.width} height={film.height} />);',
        '',
      ].join('\n'),
    );
    const r = ok('stills', {entry: path.join(entryDir, 'entry.tsx'), specs: ['Review-Sheet:0'], scale: 0.25, out_dir: path.join(root, 'review-out')}, {data});
    expect(r.film).toBe('neutral');
    expect(probe(r.stills[0].file)).toMatchObject({width: 400, height: 225});
    // it made its own bundle, so it removed it
    expect(fs.readdirSync(path.join(data, 'cache', 'bundles')).filter((n) => n.includes('entry-'))).toEqual([]);
  });

  const kitReview = path.join(ENGINE, 'remotion', 'kit', 'review', 'entry.tsx');
  it.skipIf(!fs.existsSync(kitReview))("renders the kit's own review sheet and returns its font gate as a `fonts` gate result", () => {
    const r = ok('stills', {entry: kitReview, specs: ['Kit-Review:30'], scale: 0.25, out_dir: path.join(root, 'kit-review')}, {data});
    expect(probe(r.stills[0].file)).toMatchObject({width: 400, height: 225});
    expect(r.gates).toHaveLength(1);
    expect(r.gates[0]).toMatchObject({gate: 'fonts', status: 'PASS'});
    expect(Object.keys(r.gates[0]).sort()).toEqual(['checked_at', 'details', 'engine', 'evidence', 'gate', 'inputs', 'status', 'summary']);
  });
});
