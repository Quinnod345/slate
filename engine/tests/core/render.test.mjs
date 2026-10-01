// render_mb, for real: 10 frames at 4 samples into ProRes 422 HQ 10-bit BT.709 with the exact frame count, real motion
// blur (edges averaged across the shutter), the BT.709 matrix on a saturated colour, lossless chunks, and no bundle
// left behind by a render that made its own.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {cli, decodeFrame, film, kitCore, ok, probe, readJson, scratch, twoActs} from './helpers.mjs';
import {spawnSync} from 'node:child_process';
import {ffmpegArgs, filterChain} from '../../remotion/render_mb.mjs';
import {COMPLETE_FLAGS, COMPLETE_SHELL, browserExecutable, browserStatus} from '../../remotion/browser.mjs';

let root;
let data;
let dir;
let bundle;
beforeAll(() => {
  root = scratch('render');
  data = path.join(root, 'data');
  dir = film(root, 'film');
  ok('browser-ensure', {}, {data});
  bundle = ok('bundle', {film: dir}, {data});
});
afterAll(() => {
  cli('bundle', {film: dir, release: true}, {data});
  fs.rmSync(root, {recursive: true, force: true});
});

const WIDTH = 320;
const HEIGHT = 180;

/** Pixels on the square's row (y = 90) that are neither background nor square: the blur. */
function edgePixels(file, n) {
  const buf = decodeFrame(file, n, 'gray16le');
  const row = [];
  for (let x = 0; x < WIDTH; x++) row.push(buf.readUInt16LE((90 * WIDTH + x) * 2) >> 8);
  const lo = Math.min(...row);
  const hi = Math.max(...row);
  return row.filter((v) => v > lo + 8 && v < hi - 8);
}

describe("the Chrome every Remotion render launches", () => {
  it('finishes every tile before it draws a sample, so no sub-frame loses one (a one-frame grey veil)', () => {
    expect(browserStatus().present).toBe(true);
    expect(browserExecutable()).toBe(COMPLETE_SHELL);
    const script = fs.readFileSync(COMPLETE_SHELL, 'utf8');
    for (const flag of COMPLETE_FLAGS) expect(script).toContain(flag);
    // it is Remotion's own shell underneath, the version Remotion 4.0.521 was tested with
    const v = spawnSync(COMPLETE_SHELL, ['--version'], {encoding: 'utf8'});
    expect(v.status).toBe(0);
    expect(v.stdout).toMatch(new RegExp(`\\b${browserStatus().version.replace(/\./g, '\\.')}\\b`));
  });
});

describe('the filter chain', () => {
  it('averages sub samples in 16-bit, at the film rate, into BT.709 with every tag', () => {
    const vf = filterChain(16, {num: 24000, den: 1001});
    expect(vf).toBe(
      "format=rgb48le,tmix=frames=16,select='eq(mod(n\\,16)\\,15)',setpts=N/(24000/1001)/TB," +
        'scale=out_color_matrix=bt709:out_range=tv,format=yuv422p10le,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv',
    );
    expect(filterChain(1, {num: 25, den: 1})).not.toMatch(/tmix|select/);
    const args = ffmpegArgs({sub: 4, fps: {num: 60, den: 1}, codec: 'prores', out: 'x.mov.part', format: 'mov'});
    expect(args.join(' ')).toMatch(/-framerate 240\/1 .*-r 60\/1 -fps_mode cfr -c:v prores_ks -profile:v 3 -vendor apl0 -pix_fmt yuv422p10le/);
  });
});

describe('render', () => {
  it('renders 10 frames at 4 samples into ProRes 422 HQ, 10-bit, BT.709, exactly 10 frames', () => {
    const out = path.join(root, 'out', 'act1.mov');
    const r = ok('render', {film: dir, act: 1, from: 0, to: 9, sub: 4, concurrency: 2, serve_url: bundle.serve_url, out}, {data});
    expect(r).toMatchObject({comp: 'Act1', act: 1, frames: [0, 9], film_frames: [0, 10], frame_count: 10, samples: 4, codec: 'prores', fps: '30/1'});
    expect(r.bundle_hash).toBe(bundle.bundle_hash);
    const s = probe(out);
    expect(s).toMatchObject({
      codec_name: 'prores',
      profile: 'HQ',
      pix_fmt: 'yuv422p10le',
      width: WIDTH,
      height: HEIGHT,
      color_range: 'tv',
      color_space: 'bt709',
      color_transfer: 'bt709',
      color_primaries: 'bt709',
      r_frame_rate: '30/1',
      nb_read_packets: '10',
    });
    expect(fs.existsSync(`${out}.part`)).toBe(false);
  });

  it('blurs for real: 4 samples give graded edges where 1 sample gives none', () => {
    const sharp = path.join(root, 'out', 'sub1.mov');
    ok('render', {film: dir, act: 1, from: 0, to: 9, sub: 1, concurrency: 2, serve_url: bundle.serve_url, out: sharp}, {data});
    expect(edgePixels(sharp, 5)).toEqual([]);
    // 8 px a frame over a 180° shutter is a 4 px smear sampled 4 times: 4 graded levels on each edge
    const blurred = edgePixels(path.join(root, 'out', 'act1.mov'), 5);
    expect(blurred.length).toBe(8);
    expect(new Set(blurred).size).toBe(4);
  });

  it('encodes a saturated colour with the BT.709 matrix (not BT.601)', () => {
    // the fixture's patch is rgb(0, 160, 200): BT.709 limited range is Y 507, Cb 647, Cr 224 in 10 bits
    const buf = decodeFrame(path.join(root, 'out', 'act1.mov'), 0, 'yuv444p10le');
    const plane = WIDTH * HEIGHT;
    const at = (p, x, y) => buf.readUInt16LE((p * plane + y * WIDTH + x) * 2);
    const [x, y] = [295, 25];
    expect(Math.abs(at(0, x, y) - 507)).toBeLessThanOrEqual(2);
    expect(Math.abs(at(1, x, y) - 647)).toBeLessThanOrEqual(2);
    expect(Math.abs(at(2, x, y) - 224)).toBeLessThanOrEqual(2);
  });

  it('writes a range as a lossless .mkv chunk (x264 qp 0, 10-bit 4:2:2), draft = 4 samples', () => {
    const out = path.join(root, 'out', 'chunk_10_19.mkv');
    const r = ok('render', {film: dir, act: 1, from: 10, to: 19, draft: true, trace: true, concurrency: 2, serve_url: bundle.serve_url, out}, {data});
    expect(r).toMatchObject({codec: 'lossless', samples: 4, frames: [10, 19], film_frames: [10, 20], frame_count: 10});
    expect(probe(out)).toMatchObject({codec_name: 'h264', pix_fmt: 'yuv422p10le', color_primaries: 'bt709', nb_read_packets: '10'});
    // trace: the act's motion trace comes from the kit's registry; this act doesn't use the kit, and says so
    expect(r.trace).toEqual({written: false, reason: "the act doesn't use the kit, so the kit's motion registry has nothing for it"});
  });

  it('refuses a range outside the act and a container it cannot write', () => {
    const bad = cli('render', {film: dir, act: 1, from: 5, to: 2, serve_url: bundle.serve_url, out: path.join(root, 'out', 'x.mov')}, {data});
    expect(bad.code).toBe(2);
    expect(bad.json.error).toMatch(/isn't inside Act1/);
    const ext = cli('render', {film: dir, act: 1, out: path.join(root, 'out', 'x.mp4')}, {data});
    expect(ext.code).toBe(2);
    expect(ext.json.error).toMatch(/\.mov \(ProRes\) or a \.mkv/);
    expect(cli('render', {film: dir, act: 7, out: path.join(root, 'out', 'x.mov')}, {data}).code).toBe(2);
  });

  it.skipIf(!kitCore)("with trace, writes the act's motion trace from the kit's motion registry (§12 shape)", () => {
    const traced = film(root, 'traced', (d) => {
      fs.writeFileSync(
        path.join(d, 'scenes/act1/beats.ts'),
        "import {defineMotion} from '@slate/kit/core';\nexport const slide = defineMotion({id: 'square_slide', shot: 'S01', prop: 'x', frame_in: 0, frame_out: 12, from: 20, to: 116, entrance: true});\n",
      );
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8').replace("import {film, type SceneProps} from '@film/data';", "import {film, type SceneProps} from '@film/data';\nimport './beats';"));
    });
    const r = ok('render', {film: traced, act: 1, from: 0, to: 1, sub: 1, concurrency: 1, trace: true, out: path.join(root, 'out', 'traced.mov')}, {data});
    expect(r.frame_count).toBe(2);
    expect(r.trace).toEqual({written: true, file: 'timing/motion/act1.json', entries: 1});
    const trace = readJson(path.join(traced, 'timing/motion/act1.json'));
    expect(Object.keys(trace[0]).sort()).toEqual(['ease', 'entrance', 'frame_in', 'frame_out', 'from', 'id', 'loop_period', 'prop', 'rig', 'samples', 'shot', 'to']);
    expect(trace[0]).toMatchObject({id: 'square_slide', shot: 'S01', prop: 'x', frame_in: 0, frame_out: 12, from: 20, to: 116, entrance: true});
  });

  // The films' lesson 3: one half-written act broke the shared bundle for every builder.
  it("renders one act while another act's file is half-written, and refuses the broken act by name", () => {
    const iso = film(root, 'half-written', (d) => {
      twoActs(d);
      fs.mkdirSync(path.join(d, 'scenes/act2'), {recursive: true});
      fs.writeFileSync(path.join(d, 'scenes/act2/index.tsx'), "import React from 'react';\nexport default function Act2() {\n  return (<div>half-written\n}\n");
    });
    const r = ok('render', {film: iso, act: 1, from: 0, to: 1, sub: 1, concurrency: 1, out: path.join(root, 'out', 'iso-act1.mov')}, {data});
    expect(r.frame_count).toBe(2);
    const two = cli('render', {film: iso, act: 2, sub: 1, out: path.join(root, 'out', 'iso-act2.mov')}, {data});
    expect(two.code).toBe(1);
    expect(two.json.error).toMatch(/^Act2 can't render: act 2 doesn't build \(scenes\/act2\/index\.tsx:\d+:\d+: /);
    expect(two.json.hint).toMatch(/slate check 2/);
    const whole = cli('render', {film: iso, act: 'film', sub: 1, out: path.join(root, 'out', 'iso-film.mov')}, {data});
    expect(whole.code).toBe(1);
    expect(whole.json.error).toMatch(/^Film can't render: act 2 doesn't build/);
    expect(fs.existsSync(path.join(root, 'out', 'iso-act2.mov'))).toBe(false);
  });

  // The films' lesson 2: a throw at module load took down every composition in the shared bundle.
  it('renders one act while another act throws as its module loads; only that act fails', () => {
    const iso = film(root, 'throws-at-load', (d) => {
      twoActs(d);
      fs.mkdirSync(path.join(d, 'scenes/act2'), {recursive: true});
      fs.writeFileSync(path.join(d, 'scenes/act2/index.tsx'), "import React from 'react';\nthrow new Error('the narrator never says that word');\nexport default function Act2() {\n  return null;\n}\n");
    });
    const r = ok('render', {film: iso, act: 1, from: 0, to: 1, sub: 1, concurrency: 1, out: path.join(root, 'out', 'load-act1.mov')}, {data});
    expect(r.frame_count).toBe(2);
    const two = cli('render', {film: iso, act: 2, from: 0, to: 0, sub: 1, concurrency: 1, out: path.join(root, 'out', 'load-act2.mov')}, {data});
    expect(two.code).not.toBe(0);
    expect(JSON.stringify(two.json)).toMatch(/the narrator never says that word/);
    expect(fs.existsSync(path.join(root, 'out', 'load-act2.mov'))).toBe(false);
  });

  it('a render that makes its own bundle deletes it when it is done', () => {
    const other = film(root, 'own-bundle', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8').replace('#101010', '#141414'));
    });
    const bundles = path.join(data, 'cache', 'bundles');
    const before = fs.readdirSync(bundles).filter((n) => !n.startsWith('.'));
    const r = ok('render', {film: other, act: 1, from: 0, to: 1, sub: 1, concurrency: 1, out: path.join(root, 'out', 'own.mov')}, {data});
    expect(r.bundle_reused).toBe(false);
    expect(fs.readdirSync(bundles).filter((n) => !n.startsWith('.'))).toEqual(before);
  });
});
