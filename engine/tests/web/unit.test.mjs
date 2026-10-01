// The web renderer's parts that need no browser: the encoder (held to render_mb's), the sub-frame clock, job
// resolution and its refusals, the page clock in a sandbox, and the local server's walls.
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import * as mb from '../../remotion/render_mb.mjs';
import * as enc from '../../web/lib/encode.mjs';
import {DEFAULT_SHUTTER, TEMPLATE_DIR, WEB_ENGINE_VERSION, checkArgs, cleanupJob, defaultConcurrency, resolveJob, sampleOffsets, sampleTimes} from '../../web/lib/job.mjs';
import {DEFAULT_EPOCH_MS, installPageClock} from '../../web/lib/page-clock.mjs';
import {fontUrl, startServer} from '../../web/lib/server.mjs';
import {createBundler} from '../../web/lib/bundle.mjs';
import {RUNTIME_DIR} from '../../web/lib/job.mjs';
import {faceOfFile, filmDataFor, fontFolders, mediaMounts} from '../../web/render.mjs';
import {KIT_DIR, NODE_MODULES} from '../../remotion/lib/common.mjs';
import {loadFilm} from '../../remotion/lib/film.mjs';
import {BRAND, ENGINE, FILM, film, readJson, scratch} from './helpers.mjs';

describe('the encoder', () => {
  it("is render_mb's chain, byte for byte, so a web act masters, splices and gates like a Remotion act", () => {
    for (const fps of [{num: 25, den: 1}, {num: 24000, den: 1001}, {num: 60, den: 1}]) {
      for (const sub of [1, 2, 4, 16]) {
        expect(enc.filterChain(sub, fps)).toBe(mb.filterChain(sub, fps));
        for (const [codec, format, out] of [['prores', 'mov', 'a.mov'], ['lossless', 'matroska', 'a.mkv']]) {
          expect(enc.ffmpegArgs({sub, fps, codec, out, format})).toEqual(mb.ffmpegArgs({sub, fps, codec, out, format}));
        }
      }
    }
    expect(enc.PRORES).toEqual(mb.PRORES);
    expect(enc.LOSSLESS).toEqual(mb.LOSSLESS);
    expect(enc.BT709).toEqual(mb.BT709);
    expect([enc.DELIVERY_SAMPLES, enc.DRAFT_SAMPLES]).toEqual([mb.DELIVERY_SAMPLES, mb.DRAFT_SAMPLES]);
  });

  it('reads what an output asks for, and refuses what can\'t be made', () => {
    expect(enc.outputKind('x.mov')).toEqual({format: 'mov', codec: 'prores', still: false});
    expect(enc.outputKind('x.mkv')).toEqual({format: 'matroska', codec: 'lossless', still: false});
    expect(enc.outputKind('x.part.mkv')).toMatchObject({codec: 'lossless'});
    expect(enc.outputKind('x.png')).toEqual({format: 'image2', codec: 'png', still: true});
    expect(enc.outputKind('x.mov', 'lossless')).toEqual({format: 'mov', codec: 'lossless', still: false});
    for (const [out, codec] of [['x.avi', null], ['x', null], ['x.mkv', 'prores'], ['x.png', 'prores'], ['x.mov', 'h264']]) {
      expect(() => enc.outputKind(out, codec), `${out} ${codec}`).toThrow(expect.objectContaining({kind: 'usage'}));
    }
  });
});

describe('the sub-frame clock', () => {
  it("places samples as the films did: 180° shutter, centred on the frame (local = f − 0.25 + 0.5 (k + 0.5) / S)", () => {
    for (const S of [2, 4, 8, 16]) {
      const offsets = sampleOffsets(S);
      offsets.forEach((o, k) => expect(o).toBeCloseTo(-0.25 + (0.5 * (k + 0.5)) / S, 12));
      expect(offsets.reduce((a, b) => a + b, 0)).toBeCloseTo(0, 12); // centred: the average is the frame itself
    }
    expect(sampleOffsets(1)).toEqual([0]);
    expect(DEFAULT_SHUTTER).toBe(180);
    // any shutter: the kit's filmFrameOf formula (engine/remotion/kit/clock.tsx)
    const kit = (k, S, shutter) => (shutter / 360) * ((k + 0.5) / S - 0.5);
    for (const shutter of [90, 270, 360]) sampleOffsets(6, shutter).forEach((o, k) => expect(o).toBeCloseTo(kit(k, 6, shutter), 12));
  });

  it('turns an act frame and its samples into act seconds, t = (frame + offset) × fps_den ÷ fps_num', () => {
    const fps = {num: 24000, den: 1001};
    const times = sampleTimes(10, 4, 180, fps);
    expect(times.map((s) => s.sample)).toEqual([0, 1, 2, 3]);
    times.forEach((s, k) => {
      expect(s.local).toBeCloseTo(10 + sampleOffsets(4)[k], 12);
      expect(s.t).toBeCloseTo((s.local * 1001) / 24000, 12);
    });
    expect(sampleTimes(7, 1, 180, {num: 25, den: 1})).toEqual([{sample: 0, local: 7, t: 7 / 25}]);
  });

  it("matches the runtime's version", () => {
    const env = fs.readFileSync(path.join(ENGINE, 'web', 'runtime', 'env.ts'), 'utf8');
    expect(env).toContain(`WEB_RUNTIME_VERSION = '${WEB_ENGINE_VERSION}'`);
  });
});

describe('resolving a call', () => {
  let root;
  beforeAll(() => {
    root = scratch('web-unit');
  });
  afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

  it('renders the template on a scratch film when there is no film, and cleans the scratch film up', async () => {
    const job = await resolveJob({frames: 12, fps_num: 25, fps_den: 1, width: 64, height: 36, out: path.join(root, 'a.mkv')});
    try {
      expect(job.standalone).toBe(true);
      expect(job.scene.dir).toBe(fs.realpathSync(TEMPLATE_DIR));
      expect([job.from, job.to, job.count]).toEqual([0, 11, 12]);
      expect([job.ctx.width, job.ctx.height, job.ctx.fps.rational]).toEqual([64, 36, '25/1']);
      expect(job.samples).toBe(enc.DELIVERY_SAMPLES);
      expect(job.kind).toMatchObject({codec: 'lossless'});
      expect(job.trace).toBe(false);
      expect(job.concurrency).toBe(Math.min(defaultConcurrency(), 12));
      expect(fs.existsSync(path.join(job.scratchDir, 'timing', 'shots.json'))).toBe(true);
    } finally {
      cleanupJob(job);
    }
    expect(fs.existsSync(job.scratchDir)).toBe(false);
    const draft = await resolveJob({frames: 2, draft: true, out: path.join(root, 'b.mkv')});
    cleanupJob(draft);
    expect(draft.samples).toBe(enc.DRAFT_SAMPLES);
  });

  it("takes a film's rate, size and act frames from film.json and timing/shots.json", async () => {
    const f = film(root, {name: 'resolve-film', scene: 'clock'});
    const job = await resolveJob({film: f, act: 1, from: 3, to: 9, sub: 2, brand_path: BRAND});
    expect(job.standalone).toBe(false);
    expect(job.act).toMatchObject({act: 1, frameIn: 0, frameOut: 50, frames: 50, shots: ['W01', 'W02']});
    expect([job.from, job.to, job.count, job.samples]).toEqual([3, 9, 7, 2]);
    expect(job.out).toBe(path.join(f, 'renders', 'act1.web.mov'));
    expect(job.kind.codec).toBe('prores');
    expect(job.trace).toBe(true);
    expect(job.brandJson.name).toBe('web-fixture');
    expect(job.scene.dir).toBe(fs.realpathSync(path.join(f, 'scenes', 'act1', 'web')));
  });

  it("resolves a relative scene, out and frames_dir from the caller's folder, film or no film (as cli.mjs promises)", async () => {
    const f = film(root, {name: 'caller-film', scene: 'clock'});
    const caller = path.join(root, 'caller');
    fs.mkdirSync(caller, {recursive: true});
    fs.cpSync(path.join(f, 'scenes', 'act1', 'web'), path.join(caller, 'my-scene'), {recursive: true});
    const job = await resolveJob({film: f, act: 1, scene: 'my-scene', out: 'out.mkv', frames_dir: 'frames', brand_path: BRAND}, {cwd: caller});
    expect(job.scene.dir).toBe(fs.realpathSync(path.join(caller, 'my-scene')));
    expect(job.out).toBe(path.join(caller, 'out.mkv'));
    expect(job.framesDir).toBe(path.join(caller, 'frames'));
  });

  it('reads a dry run only with emit: it checks beats without writing them', async () => {
    const f = film(root, {name: 'dry-film', scene: 'clock'});
    await expect(resolveJob({film: f, act: 1, dry_run: true})).rejects.toMatchObject({kind: 'usage', message: expect.stringMatching(/"dry_run" goes with "emit"/)});
    const job = await resolveJob({film: f, act: 1, emit: true, dry_run: true, brand_path: BRAND});
    expect([job.mode, job.dryRun]).toEqual(['emit', true]);
  });

  it("serves a derived film's reused captures and plates from its parent, where .slate/refs.json says they are", () => {
    const parent = film(root, {name: 'parent-film'});
    fs.mkdirSync(path.join(parent, 'captures', 'demo', 'frames'), {recursive: true});
    const child = film(root, {name: 'child-film'});
    fs.mkdirSync(path.join(child, '.slate'), {recursive: true});
    fs.writeFileSync(path.join(child, '.slate', 'refs.json'), JSON.stringify({captures: path.join(parent, 'captures')}));
    expect(mediaMounts(loadFilm(child))).toEqual({'/_slate/film/captures/': path.join(parent, 'captures')});
    // a film with its own copies needs no extra mount
    expect(mediaMounts(loadFilm(parent))).toEqual({});
  });

  it("refuses an arg it doesn't read, naming the one that was probably meant", () => {
    expect(() => checkArgs({film: 'x', sample: 4})).toThrow(expect.objectContaining({kind: 'usage', message: expect.stringMatching(/"sample" \(did you mean "samples"\?\)/)}));
    expect(() => checkArgs({film: 'x', act: 1, from: 0, to: 3, sub: 2, out: 'x.mkv', codec: 'lossless', brand_path: 'b', serve_url: 'u'})).not.toThrow();
  });

  it('refuses bad calls with usage errors, and a missing scene as a missing prerequisite', async () => {
    const f = film(root, {name: 'refuse-film', scene: 'clock'});
    const bare = film(root, {name: 'bare-film'});
    const usage = [
      {film: f, act: 2},
      {film: f, act: 1, from: 10, to: 5},
      {film: f, act: 1, to: 50},
      {film: f, act: 1, sub: 65},
      {film: f, act: 1, samples: 0},
      {film: f, act: 1, shutter: 0},
      {film: f, act: 1, concurrency: 9},
      {film: f, act: 1, on_blocked: 'ignore'},
      {film: f, act: 1, gl: 'metal'},
      {film: f, act: 1, out: path.join(root, 'x.gif')},
      {film: f, act: 1, from: 1, to: 2, out: path.join(root, 'x.png')},
      {film: f, act: 1, brand_path: path.join(root, 'no-brand')},
      {frames: 0, out: path.join(root, 'x.mkv')},
      {frames: 3},
      {out: path.join(root, 'x.mkv')},
      {emit: true},
    ];
    for (const args of usage) await expect(resolveJob(args), JSON.stringify(args)).rejects.toMatchObject({kind: 'usage'});
    await expect(resolveJob({film: bare, act: 1})).rejects.toMatchObject({kind: 'missing_prerequisite'});
    await expect(resolveJob({scene: path.join(root, 'nowhere'), frames: 2, out: path.join(root, 'x.mkv')})).rejects.toMatchObject({kind: 'missing_prerequisite'});
    // a scratch film made for a refused call doesn't linger
    const left = fs.readdirSync(os.tmpdir()).filter((n) => n.startsWith('slate-web-film-')).map((n) => path.join(os.tmpdir(), n));
    const fresh = left.filter((d) => Date.now() - fs.statSync(d).mtimeMs < 5000);
    expect(fresh).toEqual([]);
  });
});

describe("a scene's modules", () => {
  let root;
  beforeAll(() => {
    root = scratch('web-bundle');
  });
  afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

  it("compile with '@slate/web', the kit and the film's own '@film/data', spoken forms in .slate/ included", async () => {
    const f = film(root, {name: 'data-film', scene: 'tween'});
    fs.mkdirSync(path.join(f, '.slate'), {recursive: true});
    fs.writeFileSync(path.join(f, '.slate', 'spoken.json'), JSON.stringify({'64': [['sixty-four']]}));
    const data = filmDataFor(loadFilm(f), readJson(path.join(BRAND, 'brand.json')), BRAND);
    expect(data.contents).toContain("from './.slate/spoken.json'");
    expect(data.contents).toContain("from './timing/shots.json'");
    const bundler = createBundler({runtimeDir: RUNTIME_DIR, kitDir: KIT_DIR, nodeModules: NODE_MODULES, filmData: () => data, urlFor: () => null});
    const {code} = await bundler.bundle(path.join(f, 'scenes', 'act1', 'web', 'scene.ts'));
    const text = code.toString();
    expect(text).toContain('sixty-four'); // the spoken forms
    expect(text).toContain('web-fixture'); // the film and its brand
    expect(text).toContain('bar.slide'); // the scene itself
    expect(text).not.toMatch(/from\s*["'](gsap|three|@slate)/); // everything bundled: the page imports nothing by name
  });

  it('name an asset outside the folders the page can reach, rather than loading it by accident', async () => {
    const f = film(root, {name: 'asset-film'});
    const scene = path.join(f, 'scenes', 'act1', 'web');
    fs.mkdirSync(scene, {recursive: true});
    fs.writeFileSync(path.join(root, 'outside.png'), 'not really a png');
    fs.writeFileSync(path.join(scene, 'scene.js'), "import pic from '../../../../outside.png'; console.log(pic);\n");
    const bundler = createBundler({
      runtimeDir: RUNTIME_DIR,
      kitDir: KIT_DIR,
      nodeModules: NODE_MODULES,
      filmData: () => filmDataFor(loadFilm(f)),
      urlFor: (p) => (p.startsWith(f) ? `/_slate/film/${path.relative(f, p)}` : null),
    });
    await expect(bundler.bundle(path.join(scene, 'scene.js'))).rejects.toMatchObject({errors: [expect.objectContaining({text: expect.stringMatching(/outside the scene folder/)})]});
  });
});

describe('local font files', () => {
  it('read their weight and style from their names, as foundries name them', () => {
    expect(faceOfFile('PublicSans-Regular.woff2')).toEqual({weight: '400', style: 'normal'});
    expect(faceOfFile('PublicSans-SemiBoldItalic.ttf')).toEqual({weight: '600', style: 'italic'});
    expect(faceOfFile('Face-ExtraLight.otf')).toEqual({weight: '200', style: 'normal'});
    expect(faceOfFile('Face-Bold.woff')).toEqual({weight: '700', style: 'normal'});
    expect(faceOfFile('Face-Black.woff2')).toEqual({weight: '900', style: 'normal'});
    expect(faceOfFile('PublicSans-VariableFont_wght.ttf')).toEqual({weight: '1 1000', style: 'normal'});
    expect(faceOfFile('PublicSans-Italic-VariableFont_wght.ttf')).toEqual({weight: '1 1000', style: 'italic'});
    expect(faceOfFile('Face.ttf')).toEqual({weight: '400', style: 'normal'});
  });

  it("are looked for in the brand's fonts folder, then Slate's", () => {
    const dirs = fontFolders('Public Sans', '/brands/demo');
    expect(dirs[0]).toBe(path.join('/brands/demo', 'fonts', 'Public Sans'));
    expect(dirs).toContain(path.join('/brands/demo', 'fonts', 'PublicSans'));
    expect(dirs.at(-1)).toMatch(/fonts\/PublicSans$/);
  });
});

describe('the page clock', () => {
  /** A bare stand-in for a page, enough for installPageClock to run in Node. */
  function page() {
    const queue = [];
    const ctx = {
      performance: {now: () => 123456.789},
      requestAnimationFrame: (cb) => queue.push(cb),
      document: {getAnimations: () => [], images: [], fonts: {ready: Promise.resolve()}},
      setTimeout,
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(`(${installPageClock.toString()})(${JSON.stringify({epoch: DEFAULT_EPOCH_MS, seed: 7})});`, ctx);
    return ctx;
  }

  it('drives Date, performance.now and animation frames from the frame time, and seeds Math.random', () => {
    const a = page();
    const run = (code) => vm.runInContext(code, a);
    expect(run('performance.now()')).toBe(0);
    expect(run('Date.now()')).toBe(DEFAULT_EPOCH_MS);
    run('__slateClock.set(1520)');
    expect(run('performance.now()')).toBe(1520);
    expect(run('Date.now()')).toBe(DEFAULT_EPOCH_MS + 1520);
    expect(run('new Date().getTime()')).toBe(DEFAULT_EPOCH_MS + 1520);
    expect(run('new Date(0).getTime()')).toBe(0);
    expect(run('typeof Date()')).toBe('string');
    run('var seen = []; requestAnimationFrame((ts) => seen.push(ts)); requestAnimationFrame((ts) => seen.push(ts + 1));');
    expect(run('seen.length')).toBe(0);
    expect(run('__slateClock.flush()')).toBe(2);
    expect(run('JSON.stringify(seen)')).toBe('[1520,1521]');
    expect(run('__slateClock.flush()')).toBe(0); // each queued callback runs once
    const b = page();
    const draws = (ctx) => vm.runInContext('[Math.random(), Math.random(), Math.random()]', ctx);
    expect(draws(a)).toEqual(draws(b)); // the same seed, the same "random" layout
  });
});

describe('the local server', () => {
  let root;
  let server;
  const get = (url, method = 'GET') =>
    new Promise((resolve, reject) => {
      const req = http.request(url, {method}, (res) => {
        const chunks = [];
        res.on('data', (d) => chunks.push(d));
        res.on('end', () => resolve({status: res.statusCode, body: Buffer.concat(chunks).toString()}));
      });
      req.on('error', reject);
      req.end();
    });

  beforeAll(async () => {
    root = scratch('web-server');
    const scene = path.join(root, 'scene');
    fs.mkdirSync(path.join(scene, 'img'), {recursive: true});
    fs.writeFileSync(path.join(scene, 'index.html'), '<!doctype html><title>x</title>');
    fs.writeFileSync(path.join(scene, 'img', 'a.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
    fs.writeFileSync(path.join(root, 'secret.txt'), 'outside the scene');
    fs.symlinkSync(path.join(root, 'secret.txt'), path.join(scene, 'link.txt'));
    fs.writeFileSync(path.join(root, 'face.woff2'), 'not really a font');
    const bundler = {bundle: async () => ({code: Buffer.from('export {};')})};
    server = await startServer({mounts: {'/': scene, '/_slate/film/': FILM, '/_slate/brand/': BRAND}, bundler});
  });

  afterAll(async () => {
    await server.close();
    fs.rmSync(root, {recursive: true, force: true});
  });

  it('listens on 127.0.0.1 only, on a port the OS picked', () => {
    expect(server.origin).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
    expect(server.port).toBeGreaterThan(0);
  });

  it('serves the scene, the film and the brand, and hashes what it served', async () => {
    expect((await get(`${server.origin}/`)).status).toBe(200);
    expect((await get(`${server.origin}/img/a.svg`)).status).toBe(200);
    expect((await get(`${server.origin}/_slate/film/film.json`)).body).toContain('web-fixture');
    expect((await get(`${server.origin}/_slate/brand/brand.json`)).status).toBe(200);
    expect(Object.keys(server.served())).toEqual(expect.arrayContaining(['/', '/img/a.svg', '/_slate/film/film.json']));
  });

  it('never lets a request walk out of its folders', async () => {
    for (const p of ['/../secret.txt', '/%2e%2e/secret.txt', '/img/../../secret.txt', '/link.txt', '/_slate/film/../../secret.txt', '/.hidden']) {
      expect((await get(`${server.origin}${p}`)).status, p).toBe(404);
    }
    expect((await get(`${server.origin}/`, 'POST')).status).toBe(405);
    // font files by absolute path, and nothing else by absolute path
    expect((await get(`${server.origin}${fontUrl(path.join(root, 'face.woff2'))}`)).status).toBe(200);
    const b64 = Buffer.from(path.join(root, 'secret.txt')).toString('base64url');
    expect((await get(`${server.origin}/_slate/font/${b64}.txt`)).status).toBe(404);
  });
});
