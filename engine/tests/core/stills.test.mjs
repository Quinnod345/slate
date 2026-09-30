// Stills from one bundle: review stills at a scale, a labelled contact sheet with its index, and another entry's
// compositions (how the kit's review sheets render) reading the neutral film when no film is given.
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {ENGINE, cli, film, ok, probe, readJson, scratch, twoActs} from './helpers.mjs';

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
    // the geometry a reader needs to cut the cells back out (slate qa access), never guessed from the sheet's size
    expect(index).toMatchObject({cell: [160, 90], padding: 8, margin: 8, labels: true, rows: 2, columns: 3});
    expect(index.cells.map((c) => [c.frame, c.shot, c.timecode])).toEqual([
      [0, 'S01', '00:00:00:00'],
      [8, 'S01', '00:00:00:08'],
      [15, 'S02', '00:00:00:15'],
      [23, 'S02', '00:00:00:23'],
    ]);
  });

  it("refuses a still of an act that doesn't build (and of the film), before any bundling", () => {
    const d = film(root, 'broken-still', (f) => {
      twoActs(f);
      fs.mkdirSync(path.join(f, 'scenes/act2'), {recursive: true});
      fs.writeFileSync(path.join(f, 'scenes/act2/index.tsx'), 'export default function Act2() {\n  return (\n}\n');
    });
    const bundles = path.join(data, 'cache', 'bundles');
    const before = fs.readdirSync(bundles).sort();
    const r = cli('stills', {film: d, specs: ['Act1:0', 'Act2:0'], out_dir: path.join(root, 'broken-out')}, {data});
    expect(r.code).toBe(1);
    expect(r.json.error).toMatch(/^Act2 can't render: act 2 doesn't build/);
    const sheet = cli('stills', {film: d, contact_sheet: {comp: 'Film'}, out_dir: path.join(root, 'broken-out')}, {data});
    expect(sheet.code).toBe(1);
    expect(sheet.json.error).toMatch(/^Film can't render/);
    expect(fs.readdirSync(bundles).sort()).toEqual(before);
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

  it("serves the film's captures and plates to staticFile() as image sequences, read in place", () => {
    const dir = film(root, 'media', (d) => {
      // one frame each, as the capture conform and the plate conform write them (f%05d.jpg)
      for (const [rel, colour] of [['captures/demo/frames/f00000.jpg', 'red'], ['plates/S01/plate/frames/f00000.jpg', 'blue']]) {
        fs.mkdirSync(path.dirname(path.join(d, rel)), {recursive: true});
        const r = spawnSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', `color=c=${colour}:s=64x36`, '-frames:v', '1', '-q:v', '2', path.join(d, rel)]);
        expect(r.status).toBe(0);
      }
      fs.writeFileSync(
        path.join(d, 'scenes/act1/index.tsx'),
        [
          "import React from 'react';",
          "import {AbsoluteFill, Img, staticFile} from 'remotion';",
          "export const silent = 'a still of two frames';",
          'export default function Act1() {',
          "  return (<AbsoluteFill style={{flexDirection: 'row'}}>",
          "    <Img src={staticFile('captures/demo/frames/f00000.jpg')} style={{width: '50%', height: '100%'}} />",
          "    <Img src={staticFile('plates/S01/plate/frames/f00000.jpg')} style={{width: '50%', height: '100%'}} />",
          '  </AbsoluteFill>);',
          '}',
          '',
        ].join('\n'),
      );
    });
    const bundles = path.join(data, 'cache', 'bundles');
    const before = fs.readdirSync(bundles).sort();
    const r = ok('stills', {film: dir, specs: ['Act1:0'], scale: 1, out_dir: path.join(root, 'media-out'), gl: 'swangle'}, {data});
    expect(r.bundle_reused).toBe(false);
    const raw = spawnSync('ffmpeg', ['-v', 'error', '-i', r.stills[0].file, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], {maxBuffer: 1 << 24}).stdout;
    const px = (x, y) => [...raw.subarray((y * 320 + x) * 3, (y * 320 + x) * 3 + 3)];
    const near = (a, b) => a.every((v, i) => Math.abs(v - b[i]) <= 16);
    expect(near(px(80, 90), [255, 0, 0]), String(px(80, 90))).toBe(true);
    expect(near(px(240, 90), [0, 0, 255]), String(px(240, 90))).toBe(true);
    // the render's own bundle is gone; the film's media is where it was, and only links point at it
    expect(fs.readdirSync(bundles).sort()).toEqual(before);
    expect(fs.existsSync(path.join(dir, 'captures/demo/frames/f00000.jpg'))).toBe(true);
    expect(fs.lstatSync(path.join(dir, '.slate/public/captures')).isSymbolicLink()).toBe(true);
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
