// One bundle per film per source hash, reused for every act and still, deleted after use; never deleted while another
// live process holds a lease on it. (Two webpack bundles in all: bundling is the slow part.)
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {cli, film, ok, scratch} from './helpers.mjs';
import {bundleInputs, publicDir, sourceHash} from '../../remotion/bundle.mjs';
import {ensureEntry} from '../../remotion/entry.mjs';
import {loadFilm} from '../../remotion/lib/film.mjs';

let root;
let data;
let bundles;
let dir;
let first;
beforeAll(() => {
  root = scratch('bundle');
  data = path.join(root, 'data');
  bundles = path.join(data, 'cache', 'bundles');
  dir = film(root, 'b1');
});
afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

const onDisk = () => (fs.existsSync(bundles) ? fs.readdirSync(bundles).filter((n) => !n.startsWith('.')) : []);

describe('bundles', () => {
  it('makes one and reuses it while the sources are the same', () => {
    first = ok('bundle', {film: dir}, {data});
    expect(first.reused).toBe(false);
    expect(fs.existsSync(path.join(first.serve_url, '.slate-bundle.json'))).toBe(true);
    expect(fs.existsSync(path.join(first.serve_url, 'index.html'))).toBe(true);
    expect(onDisk()).toEqual([path.basename(first.serve_url)]);
    const again = ok('bundle', {film: dir}, {data});
    expect(again).toMatchObject({reused: true, bundle_hash: first.bundle_hash, serve_url: first.serve_url});
  });

  it('hashes exactly what goes into the bundle: scenes, timing, film.json, the generated entry', async () => {
    const ctx = loadFilm(dir);
    const entry = (await ensureEntry(ctx)).entry;
    const inputs = (await bundleInputs(ctx, entry)).map((p) => path.relative(dir, p)).sort();
    expect(inputs).toEqual(['.slate/Root.tsx', '.slate/act1.tsx', '.slate/entry.tsx', '.slate/film-data.ts', '.slate/film.tsx', '.slate/label.tsx', 'scenes/act1/index.tsx', 'timing/shots.json']);
    const h0 = await sourceHash(ctx, {entry});
    expect(h0).toBe(first.bundle_hash);
    // an unrelated file in the film changes nothing
    fs.writeFileSync(path.join(dir, 'scenes', 'NOTES.md'), 'notes\n');
    expect(await sourceHash(ctx, {entry})).toBe(h0);
    // timing and film.json do
    const shots = path.join(dir, 'timing/shots.json');
    const saved = fs.readFileSync(shots, 'utf8');
    fs.writeFileSync(shots, saved.replace('"crosses the frame"', '"crosses over"'));
    expect(await sourceHash(ctx, {entry})).not.toBe(h0);
    fs.writeFileSync(shots, saved);
    const fj = path.join(dir, 'film.json');
    const savedFilm = fs.readFileSync(fj, 'utf8');
    fs.writeFileSync(fj, savedFilm.replace('"Engine fixture"', '"Engine fixture 2"'));
    expect(await sourceHash(ctx, {entry})).not.toBe(h0);
    fs.writeFileSync(fj, savedFilm);
    expect(await sourceHash(ctx, {entry})).toBe(h0);
  });

  it('never deletes a bundle another live process holds a lease on', () => {
    // this test's own process is alive: its lease must protect the bundle from a release in another process
    const lease = path.join(first.serve_url, '.leases', String(process.pid));
    fs.mkdirSync(path.dirname(lease), {recursive: true});
    fs.writeFileSync(lease, '{}');
    expect(ok('bundle', {film: dir, release: true}, {data}).released).toEqual([]);
    expect(fs.existsSync(first.serve_url)).toBe(true);
    fs.rmSync(lease);
  });

  it('makes a new bundle when a scene changes, and prunes the film\'s old one', () => {
    const scene = path.join(dir, 'scenes/act1/index.tsx');
    fs.writeFileSync(scene, fs.readFileSync(scene, 'utf8').replace('#101010', '#121212'));
    const changed = ok('bundle', {film: dir}, {data});
    expect(changed.reused).toBe(false);
    expect(changed.bundle_hash).not.toBe(first.bundle_hash);
    expect(onDisk()).toEqual([path.basename(changed.serve_url)]);
    first = changed;
  });

  it("clears dead leases and half-made bundles on cleanup, and release deletes the film's bundle", () => {
    fs.writeFileSync(path.join(first.serve_url, '.leases', '999999'), '{}');
    fs.mkdirSync(path.join(bundles, '.tmp-engine-fixture-x-999999'), {recursive: true});
    const res = ok('bundle', {cleanup: true}, {data});
    expect(res.removed).toEqual(expect.arrayContaining([path.basename(first.serve_url), '.tmp-engine-fixture-x-999999']));
    expect(fs.readdirSync(bundles)).toEqual([]);
    expect(ok('bundle', {film: dir, release: true}, {data}).released).toEqual([]);
  });

  it("links the film's public files, captures and plates into the served folder, never copying them", () => {
    const d = film(root, 'media', (f) => {
      fs.mkdirSync(path.join(f, 'public'), {recursive: true});
      fs.writeFileSync(path.join(f, 'public', 'mark.svg'), '<svg/>');
      fs.mkdirSync(path.join(f, 'captures', 'demo', 'frames'), {recursive: true});
      fs.mkdirSync(path.join(f, 'plates', 'S01', 'plate', 'frames'), {recursive: true});
    });
    const ctx = loadFilm(d);
    const pub = publicDir(ctx);
    expect(pub.dir).toBe(path.join(d, '.slate', 'public'));
    expect(pub.links).toEqual(['captures', 'mark.svg', 'plates']);
    expect(fs.readlinkSync(path.join(pub.dir, 'captures'))).toBe(path.join('..', '..', 'captures'));
    expect(fs.readlinkSync(path.join(pub.dir, 'mark.svg'))).toBe(path.join('..', '..', 'public', 'mark.svg'));
    expect(fs.existsSync(path.join(pub.dir, 'captures', 'demo', 'frames'))).toBe(true);
    // a name the film's own public/ uses wins, and says so
    fs.mkdirSync(path.join(d, 'public', 'plates'));
    const again = publicDir(ctx);
    expect(fs.readlinkSync(path.join(again.dir, 'plates'))).toBe(path.join('..', '..', 'public', 'plates'));
    expect(again.warnings).toEqual(["public/plates shadows the film's plates/ folder in staticFile() paths"]);
    // links to what's gone are removed; the film's files never are
    fs.rmSync(path.join(d, 'captures'), {recursive: true});
    expect(publicDir(ctx).links).toEqual(['mark.svg', 'plates']);
    expect(fs.existsSync(path.join(again.dir, 'captures'))).toBe(false);
    fs.rmSync(again.dir, {recursive: true});
    expect(fs.existsSync(path.join(d, 'public', 'mark.svg'))).toBe(true);
    expect(fs.existsSync(path.join(d, 'plates', 'S01', 'plate', 'frames'))).toBe(true);
  });

  it('refuses a serve_url that is not a Slate bundle', () => {
    const r = cli('stills', {film: dir, serve_url: path.join(root, 'nowhere'), specs: ['Act1:0']}, {data});
    expect(r.code).toBe(2);
    expect(r.json.error).toMatch(/No Slate bundle/);
  });
});
