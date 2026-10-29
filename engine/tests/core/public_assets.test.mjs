// Exercise the exact pinned static-file handler. Link existence does not prove an asset can be served.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {film, ok, scratch} from './helpers.mjs';
import {publicDir, sourceHash} from '../../remotion/bundle.mjs';
import {ensureEntry} from '../../remotion/entry.mjs';
import {loadFilm} from '../../remotion/lib/film.mjs';

const require = createRequire(import.meta.url);
const rendererDir = path.dirname(require.resolve('@remotion/renderer/package.json'));
const {serveHandler} = require(path.join(rendererDir, 'dist', 'serve-handler', 'index.js'));

let root;
beforeAll(() => { root = scratch('public-assets'); });
afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

function put(file, bytes) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, bytes);
}

function link(target, file) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.symlinkSync(target, file);
}

/** Include metadata that a hard link or source rewrite would change; reading bytes is allowed. */
function snapshotTree(dir) {
  const result = {};
  const visit = (file) => {
    const stat = fs.lstatSync(file, {bigint: true});
    const row = {
      dev: String(stat.dev), ino: String(stat.ino), size: String(stat.size), mode: String(stat.mode),
      mtime_ns: String(stat.mtimeNs), ctime_ns: String(stat.ctimeNs), nlink: String(stat.nlink),
    };
    if (stat.isSymbolicLink()) row.link = fs.readlinkSync(file);
    else if (stat.isFile()) row.sha256 = createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    result[path.relative(dir, file) || '.'] = row;
    if (stat.isDirectory()) for (const name of fs.readdirSync(file).sort()) visit(path.join(file, name));
  };
  visit(dir);
  return result;
}

/** Start only a loopback HTTP server in this test process, without the renderer's browser or compositor. */
async function withServer(dir, run) {
  const server = http.createServer((request, response) => {
    serveHandler(request, response, {public: dir}).catch((error) => {
      response.writeHead(500);
      response.end(String(error));
    });
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const read = (asset, headers = {}) => new Promise((resolve, reject) => {
    const request = http.get({host: '127.0.0.1', port: server.address().port, path: asset, headers}, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.once('error', reject);
      response.once('end', () => resolve({status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks)}));
    });
    request.once('error', reject);
    request.setTimeout(10_000, () => request.destroy(new Error('local asset request timed out')));
  });
  try { return await run(read); }
  finally {
    server.closeAllConnections();
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

const ownedView = (name) => path.join(root, 'views', name);

describe('bundle public assets', () => {
  it.each(['public', '.slate/public'])('refuses an empty physical %s namespace reached through a linked film', (namespace) => {
    const dir = film(root, 'physical-namespace-' + namespace.replaceAll('/', '-'));
    const protectedDir = path.join(dir, namespace);
    fs.mkdirSync(protectedDir, {recursive: true});
    const linked = path.join(root, 'linked-namespace-' + namespace.replaceAll('/', '-'));
    link(dir, linked);
    const before = snapshotTree(dir);
    let refusal;
    try { publicDir(loadFilm(linked), protectedDir); }
    catch (error) { refusal = error; }
    expect(snapshotTree(dir)).toEqual(before);
    expect(refusal?.message).toContain('overlaps a source asset');
    expect(fs.readlinkSync(linked)).toBe(dir);
  });

  it('refuses a physical film root reached through a linked film before touching source files', () => {
    const dir = film(root, 'physical-film');
    const linked = path.join(root, 'linked-film');
    link(dir, linked);
    const before = snapshotTree(dir);
    let refusal;
    try { publicDir(loadFilm(linked), dir); }
    catch (error) { refusal = error; }
    expect(snapshotTree(dir)).toEqual(before);
    expect(refusal?.message).toContain('overlaps a source asset');
    expect(fs.readlinkSync(linked)).toBe(dir);
  });

  it('serves a regular top-level public file as exact regular bytes without changing its source', async () => {
    const dir = film(root, 'plain');
    const source = path.join(dir, 'public', 'mark.svg');
    const bytes = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1"/></svg>');
    put(source, bytes);
    const before = snapshotTree(path.dirname(source));
    const pub = publicDir(loadFilm(dir), ownedView('plain'));
    await withServer(pub.dir, async (read) => {
      const response = await read('/mark.svg');
      expect(response.status).toBe(200);
      expect(response.body).toEqual(bytes);
    });
    expect(pub.dir).toBe(ownedView('plain'));
    expect(fs.lstatSync(path.join(pub.dir, 'mark.svg')).isFile()).toBe(true);
    expect(fs.lstatSync(path.join(pub.dir, 'mark.svg')).isSymbolicLink()).toBe(false);
    expect(fs.statSync(path.join(pub.dir, 'mark.svg')).ino).not.toBe(fs.statSync(source).ino);
    expect(snapshotTree(path.dirname(source))).toEqual(before);
    fs.rmSync(pub.dir, {recursive: true});
    expect(snapshotTree(path.dirname(source))).toEqual(before);
  });

  it('serves nested file links and their regular siblings while keeping clean subtrees linked', async () => {
    const dir = film(root, 'nested');
    const cache = path.join(root, 'nested-cache');
    const font = Buffer.from([0x77, 0x4f, 0x46, 0x32, 1, 2, 3, 4]);
    const image = Buffer.from([0xff, 0xd8, 0xff, 0xdb, 5, 6, 7, 8]);
    put(path.join(cache, 'face.woff2'), font);
    put(path.join(cache, 'frame.jpg'), image);
    link(path.join(cache, 'face.woff2'), path.join(dir, 'public', 'fonts', 'face.woff2'));
    put(path.join(dir, 'public', 'fonts', 'license.txt'), 'Neutral font fixture\n');
    link(path.join(cache, 'frame.jpg'), path.join(dir, 'public', 'images', 'frame.jpg'));
    put(path.join(dir, 'public', 'images', 'clean', 'mark.svg'), '<svg/>');
    const before = {public: snapshotTree(path.join(dir, 'public')), cache: snapshotTree(cache)};
    const pub = publicDir(loadFilm(dir), ownedView('nested'));
    await withServer(pub.dir, async (read) => {
      for (const [asset, bytes] of [
        ['/fonts/face.woff2', font], ['/fonts/license.txt', Buffer.from('Neutral font fixture\n')],
        ['/images/frame.jpg', image], ['/images/clean/mark.svg', Buffer.from('<svg/>')],
      ]) {
        const response = await read(asset);
        expect(response.status, asset).toBe(200);
        expect(response.body, asset).toEqual(bytes);
      }
    });
    expect(fs.lstatSync(path.join(pub.dir, 'fonts', 'face.woff2')).isFile()).toBe(true);
    expect(fs.lstatSync(path.join(pub.dir, 'images', 'frame.jpg')).isFile()).toBe(true);
    expect(fs.lstatSync(path.join(pub.dir, 'images', 'clean')).isSymbolicLink()).toBe(true);
    expect({public: snapshotTree(path.join(dir, 'public')), cache: snapshotTree(cache)}).toEqual(before);
  });

  it('finds file links beneath directory-link targets rather than forwarding an unservable subtree', async () => {
    const dir = film(root, 'directory-links');
    const cache = path.join(root, 'directory-cache');
    const bytes = Buffer.from('nested cached image');
    put(path.join(cache, 'image.jpg'), bytes);
    link(path.join(cache, 'image.jpg'), path.join(cache, 'branch', 'nested', 'image.jpg'));
    link(path.join(cache, 'branch', 'nested'), path.join(cache, 'branch', 'alias'));
    link(path.join(cache, 'branch'), path.join(dir, 'public', 'screens'));
    const before = {public: snapshotTree(path.join(dir, 'public')), cache: snapshotTree(cache)};
    const pub = publicDir(loadFilm(dir), ownedView('directory-links'));
    await withServer(pub.dir, async (read) => {
      for (const asset of ['/screens/nested/image.jpg', '/screens/alias/image.jpg']) {
        const response = await read(asset);
        expect(response.status, asset).toBe(200);
        expect(response.body).toEqual(bytes);
      }
    });
    expect(fs.lstatSync(path.join(pub.dir, 'screens')).isDirectory()).toBe(true);
    expect(fs.lstatSync(path.join(pub.dir, 'screens', 'alias', 'image.jpg')).isFile()).toBe(true);
    expect({public: snapshotTree(path.join(dir, 'public')), cache: snapshotTree(cache)}).toEqual(before);
  });

  it('keeps capture and plate trees in place and preserves ordinary directory aliases on cleanup', async () => {
    const dir = film(root, 'media-aliases');
    const capture = path.join(dir, 'captures', 'demo', 'frames', 'f00000.jpg');
    const plate = path.join(dir, 'plates', 'S01', 'plate', 'frames', 'f00000.jpg');
    const regular = path.join(dir, 'public', 'images', 'mark.svg');
    put(capture, Buffer.from([1, 2, 3, 4]));
    put(plate, Buffer.from([5, 6, 7, 8]));
    put(regular, '<svg/>');
    const before = {captures: snapshotTree(path.join(dir, 'captures')), plates: snapshotTree(path.join(dir, 'plates')), public: snapshotTree(path.join(dir, 'public'))};
    const pub = publicDir(loadFilm(dir), ownedView('media-aliases'));
    for (const name of ['captures', 'plates', 'images']) expect(fs.lstatSync(path.join(pub.dir, name)).isSymbolicLink()).toBe(true);
    expect(fs.statSync(path.join(pub.dir, 'captures', 'demo', 'frames', 'f00000.jpg')).ino).toBe(fs.statSync(capture).ino);
    expect(fs.statSync(path.join(pub.dir, 'plates', 'S01', 'plate', 'frames', 'f00000.jpg')).ino).toBe(fs.statSync(plate).ino);
    await withServer(pub.dir, async (read) => {
      expect(await read('/captures/demo/frames/f00000.jpg')).toMatchObject({status: 200, body: Buffer.from([1, 2, 3, 4])});
      expect(await read('/plates/S01/plate/frames/f00000.jpg', {Range: 'bytes=1-2'})).toMatchObject({status: 206, body: Buffer.from([6, 7])});
    });
    fs.rmSync(pub.dir, {recursive: true});
    expect({captures: snapshotTree(path.join(dir, 'captures')), plates: snapshotTree(path.join(dir, 'plates')), public: snapshotTree(path.join(dir, 'public'))}).toEqual(before);
  });

  it('keeps unmanaged legacy collisions and source bytes, while omitting stale generated aliases from the owned view', async () => {
    const dir = film(root, 'legacy');
    const legacy = path.join(dir, '.slate', 'public');
    const external = path.join(root, 'legacy-external', 'extra.svg');
    put(path.join(dir, 'public', 'mark.svg'), 'new public mark');
    put(path.join(dir, 'public', 'plates', 'public.jpg'), 'public plate');
    put(path.join(dir, 'plates', 'S01', 'plate', 'frames', 'f00000.jpg'), 'film plate');
    put(path.join(legacy, 'mark.svg'), 'preserved legacy mark');
    put(path.join(legacy, 'manual.txt'), 'preserved unmanaged file');
    put(external, '<svg/>');
    link(external, path.join(legacy, 'extra.svg'));
    link(path.join('..', '..', 'public', 'removed.svg'), path.join(legacy, 'removed.svg'));
    const before = {legacy: snapshotTree(legacy), public: snapshotTree(path.join(dir, 'public')), plates: snapshotTree(path.join(dir, 'plates')), external: snapshotTree(path.dirname(external))};
    const pub = publicDir(loadFilm(dir), ownedView('legacy'));
    await withServer(pub.dir, async (read) => {
      expect(await read('/mark.svg')).toMatchObject({status: 200, body: Buffer.from('preserved legacy mark')});
      expect(await read('/manual.txt')).toMatchObject({status: 200, body: Buffer.from('preserved unmanaged file')});
      expect(await read('/extra.svg')).toMatchObject({status: 200, body: Buffer.from('<svg/>')});
      expect(await read('/plates/public.jpg')).toMatchObject({status: 200, body: Buffer.from('public plate')});
      expect((await read('/removed.svg')).status).toBe(404);
    });
    expect(pub.warnings).toContain("public/plates shadows the film's plates/ folder in staticFile() paths");
    expect({legacy: snapshotTree(legacy), public: snapshotTree(path.join(dir, 'public')), plates: snapshotTree(path.join(dir, 'plates')), external: snapshotTree(path.dirname(external))}).toEqual(before);
    fs.rmSync(pub.dir, {recursive: true});
    expect(snapshotTree(legacy)).toEqual(before.legacy);
  });

  it('refreshes copied leaves and removes deleted entries only from its owned view', async () => {
    const dir = film(root, 'refresh');
    const asset = path.join(dir, 'public', 'mark.svg');
    const targetA = path.join(root, 'refresh-cache', 'a.woff2');
    const targetB = path.join(root, 'refresh-cache', 'b.woff2');
    const leaf = path.join(dir, 'public', 'fonts', 'face.woff2');
    put(asset, 'first mark');
    put(targetA, 'first font');
    put(targetB, 'second font');
    link(targetA, leaf);
    const ctx = loadFilm(dir);
    let pub = publicDir(ctx, ownedView('refresh'));
    put(asset, 'second mark');
    fs.unlinkSync(leaf);
    link(targetB, leaf);
    pub = publicDir(ctx, ownedView('refresh'));
    await withServer(pub.dir, async (read) => {
      expect(await read('/mark.svg')).toMatchObject({status: 200, body: Buffer.from('second mark')});
      expect(await read('/fonts/face.woff2')).toMatchObject({status: 200, body: Buffer.from('second font')});
    });
    fs.unlinkSync(asset);
    fs.unlinkSync(leaf);
    pub = publicDir(ctx, ownedView('refresh'));
    await withServer(pub.dir, async (read) => {
      expect((await read('/mark.svg')).status).toBe(404);
      expect((await read('/fonts/face.woff2')).status).toBe(404);
    });
    expect(fs.readFileSync(targetA, 'utf8')).toBe('first font');
    expect(fs.readFileSync(targetB, 'utf8')).toBe('second font');
  });

  it('hashes copied public bytes and effective alias structure, including retargets and legacy overrides', async () => {
    const dir = film(root, 'public-hash');
    const a = path.join(root, 'hash-cache', 'a.woff2');
    const b = path.join(root, 'hash-cache', 'b.woff2');
    const leaf = path.join(dir, 'public', 'fonts', 'face.woff2');
    put(a, 'same font');
    put(b, 'same font');
    link(a, leaf);
    put(path.join(dir, 'public', 'mark.svg'), 'first mark');
    put(path.join(dir, 'public', 'clean', 'nested', 'image.jpg'), 'live image');
    const ctx = loadFilm(dir);
    const {entry} = await ensureEntry(ctx);
    const hash = () => sourceHash(ctx, {entry});
    const first = await hash();
    put(a, 'changed font');
    const changedBytes = await hash();
    expect(changedBytes).not.toBe(first);
    put(a, 'same font');
    expect(await hash()).toBe(first);
    fs.unlinkSync(leaf);
    link(b, leaf);
    const retargeted = await hash();
    expect(retargeted).not.toBe(first);
    fs.unlinkSync(leaf);
    put(leaf, 'same font');
    expect(await hash()).not.toBe(retargeted);
    fs.unlinkSync(leaf);
    link(b, leaf);
    expect(await hash()).toBe(retargeted);
    // Linked regular leaves stay live; their bytes do not force another webpack bundle.
    put(path.join(dir, 'public', 'clean', 'nested', 'image.jpg'), 'updated live image');
    expect(await hash()).toBe(retargeted);
    put(path.join(dir, 'public', 'clean', 'added.jpg'), 'new live image');
    const added = await hash();
    expect(added).not.toBe(retargeted);
    fs.unlinkSync(path.join(dir, 'public', 'clean', 'added.jpg'));
    expect(await hash()).toBe(retargeted);
    const legacy = path.join(dir, '.slate', 'public', 'mark.svg');
    put(legacy, 'legacy collision');
    const overridden = await hash();
    expect(overridden).not.toBe(retargeted);
    put(path.join(dir, 'public', 'mark.svg'), 'shadowed mark update');
    expect(await hash()).toBe(overridden);
    fs.unlinkSync(legacy);
    expect(await hash()).not.toBe(overridden);
  });

  it('hashes nested directory-link target changes even when their regular leaf bytes are equal', async () => {
    const dir = film(root, 'directory-hash');
    const a = path.join(root, 'directory-hash-cache', 'a');
    const b = path.join(root, 'directory-hash-cache', 'b');
    put(path.join(a, 'image.jpg'), 'same image');
    put(path.join(b, 'image.jpg'), 'same image');
    link(a, path.join(dir, 'public', 'screens', 'nested'));
    const ctx = loadFilm(dir);
    const {entry} = await ensureEntry(ctx);
    const first = await sourceHash(ctx, {entry});
    fs.unlinkSync(path.join(dir, 'public', 'screens', 'nested'));
    link(b, path.join(dir, 'public', 'screens', 'nested'));
    expect(await sourceHash(ctx, {entry})).not.toBe(first);
  });

  it('serves the actual bundle, reuses unchanged assets, and invalidates a copied public-file update', async () => {
    const dir = film(root, 'bundle-public');
    const asset = path.join(dir, 'public', 'mark.svg');
    const capture = path.join(dir, 'captures', 'demo', 'frames', 'f00000.jpg');
    put(asset, '<svg/>');
    put(capture, 'capture stays in place');
    const sourceBefore = snapshotTree(path.join(dir, 'captures'));
    const data = path.join(root, 'bundle-public-data');
    const first = ok('bundle', {film: dir}, {data});
    await withServer(first.serve_url, async (read) => {
      expect(await read('/public/mark.svg')).toMatchObject({status: 200, body: Buffer.from('<svg/>')});
      expect(await read('/public/captures/demo/frames/f00000.jpg')).toMatchObject({status: 200, body: Buffer.from('capture stays in place')});
    });
    expect(fs.lstatSync(path.join(first.serve_url, 'public', 'mark.svg')).isFile()).toBe(true);
    expect(fs.lstatSync(path.join(first.serve_url, 'public', 'captures')).isSymbolicLink()).toBe(true);
    expect(ok('bundle', {film: dir}, {data})).toMatchObject({reused: true, bundle_hash: first.bundle_hash});
    put(asset, '<svg><rect/></svg>');
    const changed = ok('bundle', {film: dir}, {data});
    expect(changed.reused).toBe(false);
    expect(changed.bundle_hash).not.toBe(first.bundle_hash);
    await withServer(changed.serve_url, async (read) => {
      expect(await read('/public/mark.svg')).toMatchObject({status: 200, body: Buffer.from('<svg><rect/></svg>')});
    });
    const cache = path.join(data, 'cache', 'bundles');
    expect(fs.readdirSync(cache).filter((name) => name.startsWith('.tmp-'))).toEqual([]);
    ok('bundle', {film: dir, release: true}, {data});
    expect(fs.readdirSync(cache)).toEqual([]);
    expect(snapshotTree(path.join(dir, 'captures'))).toEqual(sourceBefore);
    expect(fs.readFileSync(asset, 'utf8')).toBe('<svg><rect/></svg>');
  });
});
