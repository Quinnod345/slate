// The open web renderer, end to end: real headless Chrome, real ffmpeg, tiny fixture films (320 x 180 at 25 fps, at
// most 20 frames). Every render uses software GL (swangle), which is bit-exact run to run.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {BRAND, ENGINE, FIXTURES, RENDER, SCENES, alive, decodeFrame, decodeLuma, decodePngRed, envFor, film, ok, probe, readJson, run, scratch, scratchFilm} from './helpers.mjs';

const W = 320;
const H = 180;
let root;

beforeAll(() => {
  root = scratch('web-render');
});

afterAll(() => {
  fs.rmSync(root, {recursive: true, force: true});
});

const px = (buf, x, y, width = W) => buf[y * width + x];

describe('the template scene', () => {
  it('renders exactly the frames asked for, as a lossless 10-bit BT.709 chunk', () => {
    const out = path.join(root, 'template.mkv');
    const r = ok(scratchFilm(10, {sub: 2, out}), {root});
    expect(r.ok).toBe(true);
    expect(r.engine).toBe('web');
    expect(r.frame_count).toBe(10);
    expect(r.frames).toEqual([0, 9]);
    expect(r.samples).toBe(2);
    const s = probe(out);
    expect(Number(s.nb_read_packets)).toBe(10);
    expect(s.pix_fmt).toBe('yuv422p10le');
    expect([s.color_space, s.color_transfer, s.color_primaries]).toEqual(['bt709', 'bt709', 'bt709']);
    expect(s.r_frame_rate).toBe('25/1');
    expect([s.width, s.height]).toEqual([W, H]);
    expect(r.gates.find((g) => g.gate === 'web.network').status).toBe('PASS');
    expect(r.gates.find((g) => g.gate === 'fonts')).toBeTruthy(); // checked, whatever the demo pack's fonts are
    expect(r.scene.entry).toMatch(/engine\/web\/template\/index\.html$/);
    expect(alive(r.browser.pid)).toBe(false);
    expect(fs.existsSync(`${out}.part`)).toBe(false);
  });

  it('draws the same pictures on every run, and with two pages as with one', () => {
    const args = (out, concurrency) => scratchFilm(6, {sub: 2, out: path.join(root, out), hashes: true, concurrency});
    const a = ok(args('det-a.mkv', 1), {root});
    const b = ok(args('det-b.mkv', 1), {root});
    const c = ok(args('det-c.mkv', 2), {root});
    expect(a.frame_hashes).toHaveLength(6);
    expect(new Set(a.frame_hashes).size).toBeGreaterThan(1); // the scene moves
    expect(b.frame_hashes).toEqual(a.frame_hashes);
    expect(c.pages).toBe(2);
    expect(c.frame_hashes).toEqual(a.frame_hashes);
  });

  it('masters to ProRes 422 HQ in a .mov', () => {
    const out = path.join(root, 'master.mov');
    const r = ok(scratchFilm(2, {sub: 1, out}), {root});
    expect(r.codec).toBe('prores');
    const s = probe(out);
    expect(s.codec_name).toBe('prores');
    expect(s.profile).toBe('HQ');
    expect(s.pix_fmt).toBe('yuv422p10le');
    expect(Number(s.nb_read_packets)).toBe(2);
  });

  it('writes one averaged frame as a still', () => {
    const out = path.join(root, 'still.png');
    const r = ok(scratchFilm(8, {sub: 4, from: 5, to: 5, out}), {root});
    expect(r.frame_count).toBe(1);
    const s = probe(out);
    expect([s.codec_name, s.width, s.height]).toEqual(['png', W, H]);
  });
});

describe('isolation from the network', () => {
  let canary;
  let hits = 0;
  let scene;

  beforeAll(async () => {
    // another server on this very Mac: the page must not reach it either
    canary = http.createServer((req, res) => {
      hits++;
      res.end('leaked');
    });
    await new Promise((resolve) => canary.listen(0, '127.0.0.1', resolve));
    scene = path.join(root, 'network-scene');
    fs.cpSync(path.join(SCENES, 'network'), scene, {recursive: true});
    fs.writeFileSync(path.join(scene, 'canary.json'), JSON.stringify({port: canary.address().port}));
  });

  afterAll(() => new Promise((resolve) => canary.close(resolve)));

  it('refuses the render, naming the attempt, when a page reaches for the network', () => {
    const out = path.join(root, 'network-fail.mkv');
    const r = run(scratchFilm(2, {scene, out}), {root});
    expect(r.code).toBe(1);
    expect(r.json.ok).toBe(false);
    expect(r.json.kind).toBe('refused');
    expect(r.json.error).toMatch(/network/);
    const urls = r.json.details.network.blocked.map((b) => b.url).join(' ');
    expect(urls).toContain('example.com');
    expect(r.json.details.gates[0]).toMatchObject({gate: 'web.network', status: 'FAIL'});
    expect(fs.existsSync(out)).toBe(false);
    expect(alive(r.json.details.browser_pid)).toBe(false);
    expect(hits).toBe(0);
  });

  it('reports every attempt, each one refused, when asked to render anyway', () => {
    const out = path.join(root, 'network-report.mkv');
    const r = ok(scratchFilm(2, {scene, out, on_blocked: 'report'}), {root});
    const blocked = r.network.blocked.map((b) => b.url);
    expect(blocked.some((u) => u.startsWith('https://example.com/'))).toBe(true); // fetch
    expect(blocked).toContain('https://example.com/pixel.png'); // an image
    expect(blocked.some((u) => u.startsWith('https://fonts.example.com/'))).toBe(true); // a stylesheet in the page
    expect(blocked.some((u) => u.startsWith('http://127.0.0.1:') && u.endsWith('/leak'))).toBe(true); // another local server
    expect(r.network.wall.some((w) => /example\.com/.test(w.target))).toBe(true); // the WebSocket, caught by the wall
    expect(r.gates.find((g) => g.gate === 'web.network').status).toBe('FAIL');
    expect(hits).toBe(0);
    // the page itself saw every attempt fail: the frame is green, not red
    const rgb = decodeFrame(out, 0, 'rgb24');
    const i = (90 * W + 160) * 3;
    expect(rgb[i + 1]).toBeGreaterThan(120);
    expect(rgb[i]).toBeLessThan(40);
  });
});

describe('the film clock', () => {
  const BASE = 30;
  const STEP = 20;
  const bands = {a: 40, b: 120, c: 200, d: 280}; // the centre column of each band

  it('shows frame N at t = N x fps_den / fps_num, through seek, performance.now, Date and animation frames', () => {
    const out = path.join(root, 'clock-1.mkv');
    ok(scratchFilm(10, {scene: path.join(SCENES, 'clock'), sub: 1, out}), {root});
    for (let n = 0; n < 10; n++) {
      const grey = decodeLuma(out, n);
      for (const [band, x] of Object.entries(bands)) {
        expect(Math.abs(px(grey, x, 90) - (BASE + STEP * n)), `frame ${n}, band ${band}`).toBeLessThanOrEqual(1);
      }
    }
  });

  it('spreads the samples across a centred shutter, so their average is still the frame time', () => {
    const out = path.join(root, 'clock-4.mkv');
    const framesDir = path.join(root, 'clock-4-frames');
    ok(scratchFilm(10, {scene: path.join(SCENES, 'clock'), sub: 4, out, frames_dir: framesDir}), {root});
    for (let n = 0; n < 10; n++) {
      const grey = decodeLuma(out, n);
      for (const [band, x] of Object.entries(bands)) {
        expect(Math.abs(px(grey, x, 90) - (BASE + STEP * n)), `frame ${n}, band ${band}`).toBeLessThanOrEqual(1.5);
      }
    }
    // each sample of frame 2 drew its own time: (2 + offset) frames, offsets ±1/16 and ±3/16 of a frame
    const offsets = [-0.1875, -0.0625, 0.0625, 0.1875];
    offsets.forEach((off, k) => {
      const png = path.join(framesDir, `f00002_s0${k}.png`);
      const grey = decodePngRed(png);
      expect(Math.abs(px(grey, bands.a, 90) - (BASE + STEP * (2 + off))), `sample ${k}`).toBeLessThanOrEqual(1);
    });
  });
});

describe('motion blur by sub-frame accumulation', () => {
  it('blurs a moving edge and leaves a still one exactly as it was', () => {
    const scene = path.join(SCENES, 'edges');
    const sharp = path.join(root, 'edges-1.mkv');
    const blurred = path.join(root, 'edges-8.mkv');
    ok(scratchFilm(4, {scene, sub: 1, out: sharp}), {root});
    ok(scratchFilm(4, {scene, sub: 8, out: blurred}), {root});
    const a = decodeLuma(sharp, 2);
    const b = decodeLuma(blurred, 2);
    // the still bar's half of the frame is identical, pixel for pixel
    expect(Buffer.compare(a.subarray(0, 90 * W), b.subarray(0, 90 * W))).toBe(0);
    // the moving bar: hard edges with one sample, a ramp at each edge with eight
    const row = 130;
    const mid = (buf) => Array.from({length: W}, (_, x) => px(buf, x, row)).filter((v) => v > 20 && v < 235).length;
    expect(mid(a)).toBeLessThanOrEqual(2);
    expect(mid(b)).toBeGreaterThanOrEqual(6);
    expect(Buffer.compare(a.subarray(100 * W, 160 * W), b.subarray(100 * W, 160 * W))).not.toBe(0);
  });
});

describe('a web act in a film', () => {
  it('starts from the template, renders on the film clock and writes the act\'s motion trace', () => {
    const f = film(root, {name: 'template-film'});
    const s = ok({film: f, act: 1, scaffold: true}, {root});
    expect(s.files.sort()).toEqual(['scenes/act1/web/index.html', 'scenes/act1/web/scene.ts']);
    const again = run({film: f, act: 1, scaffold: true}, {root});
    expect(again.code).toBe(1);
    expect(again.json.kind).toBe('refused');

    const out = path.join(f, 'renders', 'act1.chunk.mkv');
    const r = ok({film: f, act: 1, from: 0, to: 4, sub: 1, brand_path: BRAND, gl: 'swangle', concurrency: 1, out}, {root});
    expect(r.film_frames).toEqual([0, 5]);
    expect(r.frame_count).toBe(5);
    expect(r.gates.find((g) => g.gate === 'fonts')).toMatchObject({status: 'PASS'});
    expect(r.trace).toMatchObject({written: true, file: 'timing/motion/act1.json'});

    const trace = readJson(path.join(f, 'timing', 'motion', 'act1.json'));
    const ids = trace.map((row) => row.id).sort();
    expect(ids).toEqual(['template.line-in.opacity', 'template.line-in.y', 'template.object-turn']);
    for (const row of trace) {
      expect(row.samples[0]).toEqual([row.frame_in, row.from]); // exact endpoints
      expect(row.samples.at(-1)).toEqual([row.frame_out, row.to]);
      expect(row.samples).toHaveLength(row.frame_out - row.frame_in + 1);
    }
    const turn = trace.find((row) => row.id === 'template.object-turn');
    expect(turn).toMatchObject({shot: 'W01', prop: 'rotation.y', ease: 'move', unit: 'rad', frame_in: 0, frame_out: 45, entrance: false}); // signature: 1.8 s
    const lineIn = trace.find((row) => row.id === 'template.line-in.opacity');
    expect(lineIn).toMatchObject({ease: 'enter', entrance: true, frame_in: 5, frame_out: 18, from: 0, to: 1}); // tick 0.2 s, base 0.52 s

    const e = ok({film: f, act: 1, emit: true, brand_path: BRAND, gl: 'swangle'}, {root});
    expect(e.acts[0]).toMatchObject({act: 1, silent: false, events: 1, file: 'timing/visual_events/act1.json'});
    const events = readJson(path.join(f, 'timing', 'visual_events', 'act1.json'));
    expect(events[0]).toMatchObject({shot: 'W01', id: 'template.line-lands', kind: 'land', size: 's'});
    expect(alive(e.browser.pid)).toBe(false);
  });

  it("fits the template's moves inside a short act, so its beat lands inside the act and emit-events takes it", () => {
    const f = film(root, {name: 'short-film'});
    const shotsFile = path.join(f, 'timing', 'shots.json');
    const shots = readJson(shotsFile);
    // one act of 10 frames (0.4 s at 25 fps), shorter than the brand's base entrance plus its tick
    shots.total_frames = 10;
    shots.acts = [{index: 1, name: 'Short', shots: ['W01']}];
    shots.shots = [{id: 'W01', act: 1, frame_in: 0, frame_out: 10, frames: 10, vo: '', on_screen: ''}];
    fs.writeFileSync(shotsFile, JSON.stringify(shots, null, 1));
    ok({film: f, act: 1, scaffold: true}, {root});
    const e = ok({film: f, act: 1, emit: true, brand_path: BRAND, gl: 'swangle'}, {root});
    expect(e.acts[0]).toMatchObject({act: 1, silent: false, events: 1});
    const [beat] = readJson(path.join(f, 'timing', 'visual_events', 'act1.json'));
    expect(beat.id).toBe('template.line-lands');
    expect(beat.t).toBeGreaterThan(0);
    expect(beat.t).toBeLessThan(0.4);
    for (const row of readJson(path.join(f, 'timing', 'motion', 'act1.json'))) expect(row.frame_out).toBeLessThanOrEqual(10);
    expect(alive(e.browser.pid)).toBe(false);
  });

  it('moves on the frames the trace says, frame for frame', () => {
    const f = film(root, {name: 'tween-film', scene: 'tween'});
    const out = path.join(f, 'renders', 'tween.mkv');
    ok({film: f, act: 1, from: 0, to: 19, sub: 1, brand_path: BRAND, gl: 'swangle', concurrency: 2, out}, {root});
    const [row] = readJson(path.join(f, 'timing', 'motion', 'act1.json'));
    expect(row).toMatchObject({id: 'bar.slide', prop: 'x', ease: 'move', frame_in: 5, frame_out: 15, from: 0, to: 200, unit: 'px'});
    const at = new Map(row.samples);
    for (let n = 0; n < 20; n++) {
      const expected = 20 + (n < row.frame_in ? row.from : n > row.frame_out ? row.to : at.get(n));
      const grey = decodeLuma(out, n);
      let edge = -1;
      for (let x = 0; x < W; x++) {
        if (px(grey, x, 90) > 127) {
          edge = x;
          break;
        }
      }
      expect(Math.abs(edge - expected), `frame ${n}`).toBeLessThanOrEqual(1);
    }
    const e = ok({film: f, act: 1, emit: true, brand_path: BRAND, gl: 'swangle'}, {root});
    expect(e.acts[0].events).toBe(1);
    const [beat] = readJson(path.join(f, 'timing', 'visual_events', 'act1.json'));
    expect(beat).toMatchObject({id: 'bar.lands', kind: 'land', t: 0.6});
  });

  it('refuses a scene whose brand fonts would be substituted', () => {
    const brand = path.join(root, 'brand-missing-face');
    fs.mkdirSync(brand, {recursive: true});
    const pack = readJson(path.join(BRAND, 'brand.json'));
    pack.type.families = [
      {role: 'sans', family: 'Slate Fixture Face That Is Not Installed', weights: [400], source: 'installed', licence: 'fixture'},
      {role: 'display', family: 'Slate Fixture Web Face', weights: [400], source: 'google', licence: 'fixture'},
    ];
    fs.writeFileSync(path.join(brand, 'brand.json'), JSON.stringify(pack));
    const f = film(root, {name: 'fonts-film', scene: 'tween'});
    const r = run({film: f, act: 1, from: 0, to: 1, sub: 1, brand_path: brand, gl: 'swangle', out: path.join(f, 'renders', 'x.mkv')}, {root});
    expect(r.code).toBe(1);
    expect(r.json.error).toMatch(/font gate failed/);
    const gate = r.json.details.gates[0];
    expect(gate).toMatchObject({gate: 'fonts', status: 'FAIL'});
    const failures = gate.details.failures.join('\n');
    expect(failures).toMatch(/did not render/);
    expect(failures).toMatch(/never touches the network/);
  });
});

describe('fonts', () => {
  const brandWith = (name, families) => {
    const dir = path.join(root, name);
    fs.mkdirSync(dir, {recursive: true});
    const pack = readJson(path.join(BRAND, 'brand.json'));
    pack.type.families = families;
    fs.writeFileSync(path.join(dir, 'brand.json'), JSON.stringify(pack));
    return dir;
  };

  it('reports a failed font gate, and still renders, for a scene on its own', () => {
    const brand = brandWith('brand-preview', [{role: 'sans', family: 'Slate Fixture Face That Is Not Installed', weights: [400], source: 'installed', licence: 'fixture'}]);
    const out = path.join(root, 'preview-fonts.mkv');
    const r = ok(scratchFilm(2, {scene: path.join(SCENES, 'tween'), sub: 1, brand_path: brand, out}), {root});
    expect(r.frame_count).toBe(2);
    expect(r.gates.find((g) => g.gate === 'fonts')).toMatchObject({status: 'FAIL'});
    expect(r.warnings.join('\n')).toMatch(/on_fonts is "report"/);
    const strict = run(scratchFilm(2, {scene: path.join(SCENES, 'tween'), sub: 1, brand_path: brand, out, on_fonts: 'fail'}), {root});
    expect(strict.code).toBe(1);
  });

  const LOCAL_SOURCE = '/System/Library/Fonts/Supplemental/Andale Mono.ttf';
  it.runIf(fs.existsSync(LOCAL_SOURCE))('loads a family the pack serves from Google Fonts from the local fonts folder', () => {
    // a copy of one of this Mac's own fonts, under a name no browser knows, standing in for a downloaded family
    const family = 'Slate Local Fixture Face';
    const folder = path.join(root, 'data', 'fonts', family);
    fs.mkdirSync(folder, {recursive: true});
    fs.copyFileSync(LOCAL_SOURCE, path.join(folder, 'SlateLocalFixtureFace-Regular.ttf'));
    const brand = brandWith('brand-local-font', [{role: 'sans', family, weights: [400], source: 'google', licence: 'fixture'}]);
    const f = film(root, {name: 'local-font-film', scene: 'tween'});
    const r = ok({film: f, act: 1, from: 0, to: 1, sub: 1, brand_path: brand, gl: 'swangle', out: path.join(f, 'renders', 'x.mkv')}, {root});
    const gate = r.gates.find((g) => g.gate === 'fonts');
    expect(gate).toMatchObject({status: 'PASS'});
    expect(gate.evidence).toEqual([`${family} 400: rendered`]);
  });
});

describe('refusals', () => {
  it('names the frame a scene throws at', () => {
    const r = run(scratchFilm(6, {scene: path.join(SCENES, 'throws'), sub: 1, out: path.join(root, 'throws.mkv')}), {root});
    expect(r.code).toBe(1);
    expect(r.json.kind).toBe('refused');
    expect(r.json.error).toMatch(/frame 3/);
    expect(fs.existsSync(path.join(root, 'throws.mkv'))).toBe(false);
    expect(alive(r.json.details.browser_pid)).toBe(false);
  });

  it('stops at the error when a scene throws while it gets ready, instead of waiting out the timeout', () => {
    const t0 = Date.now();
    const r = run(scratchFilm(2, {scene: path.join(SCENES, 'ready-throws'), load_timeout_ms: 120000, out: path.join(root, 'ready.mkv')}), {root});
    expect(r.code).toBe(1);
    expect(r.json.error).toMatch(/threw while it got ready: .*fails while it gets ready/);
    expect(r.json.details.page_errors[0].message).toMatch(/fails while it gets ready/);
    expect(Date.now() - t0).toBeLessThan(60000);
  });

  it('says so when a page never registers its scene', () => {
    const r = run(scratchFilm(2, {scene: path.join(SCENES, 'silent-page'), load_timeout_ms: 1500, out: path.join(root, 'silent.mkv')}), {root});
    expect(r.code).toBe(1);
    expect(r.json.error).toMatch(/never registered window\.slateScene/);
  });

  it("keeps the film's frame rate", () => {
    const r = run(scratchFilm(2, {scene: path.join(SCENES, 'wrong-fps'), out: path.join(root, 'fps.mkv')}), {root});
    expect(r.code).toBe(1);
    expect(r.json.error).toMatch(/runs at 24\/1 fps, but .* runs at 25\/1/);
  });

  it('exits 3 when there is no scene, and 2 for a bad call', () => {
    const missing = run(scratchFilm(2, {scene: path.join(root, 'no-such-scene'), out: path.join(root, 'm.mkv')}), {root});
    expect(missing.code).toBe(3);
    expect(missing.json.kind).toBe('missing_prerequisite');
    const f = film(root, {name: 'bare-film'});
    const noAct = run({film: f, act: 1, out: path.join(f, 'renders', 'x.mkv')}, {root});
    expect(noAct.code).toBe(3);
    expect(noAct.json.hint).toMatch(/template/);
    for (const bad of [
      scratchFilm(2, {sub: 0, out: path.join(root, 'b.mkv')}),
      scratchFilm(2, {out: path.join(root, 'b.avi')}),
      scratchFilm(2, {from: 1, to: 5, out: path.join(root, 'b.mkv')}),
      scratchFilm(4, {from: 0, to: 2, out: path.join(root, 'b.png')}),
      scratchFilm(2, {gl: 'metal', out: path.join(root, 'b.mkv')}),
      {emit: true, frames: 2},
    ]) {
      const r = run(bad, {root});
      expect(r.code, JSON.stringify(bad)).toBe(2);
      expect(r.json.kind).toBe('usage');
    }
  });
});

describe('React Three Fiber', () => {
  it('advances once per seek (frameloop never) and draws through the accumulation renderer, the same every run', () => {
    const scene = path.join(SCENES, 'r3f');
    const a = ok(scratchFilm(6, {scene, sub: 1, out: path.join(root, 'r3f-a.mkv'), hashes: true}), {root});
    const b = ok(scratchFilm(6, {scene, sub: 1, out: path.join(root, 'r3f-b.mkv'), hashes: true}), {root});
    expect(b.frame_hashes).toEqual(a.frame_hashes);
    expect(new Set(a.frame_hashes).size).toBe(6); // the box turns every frame
    const first = decodeLuma(path.join(root, 'r3f-a.mkv'), 0);
    expect(px(first, 160, 90)).toBeGreaterThan(40); // the box is there, lit, in the middle of a black frame
    expect(px(first, 5, 5)).toBeLessThan(5);
  });
});

describe('three.js inside a Remotion scene', () => {
  it('draws through @remotion/three in an act rendered by engine-core', () => {
    const f = film(root, {name: 'remotion-three-film'});
    fs.mkdirSync(path.join(f, 'scenes', 'act1'), {recursive: true});
    fs.copyFileSync(path.join(FIXTURES, 'remotion-three', 'index.tsx'), path.join(f, 'scenes', 'act1', 'index.tsx'));
    const outDir = path.join(root, 'remotion-three-stills');
    const res = spawnSync(process.execPath, [path.join(ENGINE, 'cli.mjs'), 'stills', '--json', JSON.stringify({film: f, specs: ['Act1:3'], out_dir: outDir, gl: 'swangle', scale: 1})], {
      cwd: root,
      encoding: 'utf8',
      timeout: 240_000,
      env: envFor(root),
    });
    const result = JSON.parse(res.stdout.trim().split('\n').at(-1));
    expect(result, res.stderr.slice(-2000)).toMatchObject({ok: true});
    const [still] = result.stills;
    const rgb = decodeFrame(still.file, 0, 'rgb24');
    const at = (x, y) => rgb[(y * W + x) * 3];
    expect(at(160, 90)).toBeGreaterThan(60); // the lit box in the middle
    expect(at(5, 5)).toBeLessThan(5); // the black ground
    expect(fs.readdirSync(path.join(root, 'data', 'cache', 'bundles'))).toEqual([]); // the bundle was released
  });
});

describe('no Remotion licence needed', () => {
  it('renders without loading a single Remotion package when it is pointed at a headless shell', () => {
    const shellRoot = path.join(ENGINE, 'node_modules', '.remotion', 'chrome-headless-shell');
    const platform = fs.readdirSync(shellRoot).find((d) => fs.statSync(path.join(shellRoot, d)).isDirectory());
    const shell = path.join(shellRoot, platform, `chrome-headless-shell-${platform}`, 'chrome-headless-shell');
    const log = path.join(root, 'modules.log');
    const out = path.join(root, 'no-remotion.mkv');
    const args = scratchFilm(2, {sub: 1, out, scene: path.join(SCENES, 'tween')});
    const res = spawnSync(process.execPath, ['--import', path.join(FIXTURES, 'load-log.mjs'), RENDER, '--json', JSON.stringify(args)], {
      cwd: root,
      encoding: 'utf8',
      timeout: 240_000,
      env: envFor(root, {SLATE_WEB_BROWSER: shell, SLATE_TEST_LOAD_LOG: log}),
    });
    const result = JSON.parse(res.stdout.trim().split('\n').at(-1));
    expect(result, res.stderr.slice(-2000)).toMatchObject({ok: true, frame_count: 2});
    expect(result.browser.source).toBe('SLATE_WEB_BROWSER');
    const loaded = fs.readFileSync(log, 'utf8').split('\n').filter(Boolean);
    expect(loaded.some((u) => u.includes('/node_modules/puppeteer'))).toBe(true); // the log saw the packages it did load
    expect(loaded.filter((u) => /\/node_modules\/(remotion|@remotion)\//.test(u))).toEqual([]);
  });
});

describe("the engine's command line", () => {
  it('runs web-render through cli.mjs with the same one-object contract', () => {
    const out = path.join(root, 'via-cli.mkv');
    const r = ok(scratchFilm(2, {sub: 1, out, scene: path.join(SCENES, 'clock')}), {root, via: 'cli'});
    expect(r.command).toBe('web-render');
    expect(r.protocol).toBe(1);
    expect(r.frame_count).toBe(2);
    const bad = run(scratchFilm(2, {sub: 99, out}), {root, via: 'cli'});
    expect(bad.code).toBe(2);
    expect(bad.json).toMatchObject({ok: false, kind: 'usage'});
  });
});
