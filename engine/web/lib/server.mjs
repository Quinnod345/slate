// The only web server a web scene ever sees: bound to 127.0.0.1 on a port the OS picks, alive for one render.
//
//   /                  the scene folder (index.html, its scripts, its assets)
//   /_slate/film/…     the film folder, read-only (captures, plates, timing JSON)
//   /_slate/brand/…    the brand pack's folder, read-only (logos, local font files)
//   /_slate/font/<id>  a brand font file named by absolute path in the pack (font files only)
//
// Scripts are compiled on request (lib/bundle.mjs); a script ending in .min.js, or asked for with ?raw, is served
// as it is. Only GET and HEAD; nothing outside the mounted folders is reachable (paths are resolved and their real
// locations checked, so neither ".." nor a symlink can walk out). Every body served is hashed: the render's
// bundle_hash is what the page actually loaded.
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {SCRIPT_EXTS, describeBuildError} from './bundle.mjs';

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.htm': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.jsx': 'text/javascript; charset=utf-8',
  '.ts': 'text/javascript; charset=utf-8',
  '.tsx': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.avif': 'image/avif',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.bin': 'application/octet-stream',
  '.hdr': 'application/octet-stream',
  '.exr': 'application/octet-stream',
  '.ktx2': 'image/ktx2',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.mov': 'video/quicktime',
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg',
  '.m4a': 'audio/mp4',
  '.ogg': 'audio/ogg',
  '.glsl': 'text/plain; charset=utf-8',
  '.wgsl': 'text/plain; charset=utf-8',
};
const FONT_EXTS = new Set(['.woff', '.woff2', '.ttf', '.otf']);

const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex');
const b64url = (s) => Buffer.from(s, 'utf8').toString('base64url');

function realOrNull(p) {
  try {
    return fs.realpathSync(p);
  } catch {
    return null;
  }
}

function inside(root, file) {
  const rel = path.relative(root, file);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

/** The URL path of a font file served by absolute path. */
export const fontUrl = (absPath) => `/_slate/font/${b64url(path.resolve(absPath))}${path.extname(absPath).toLowerCase()}`;

/**
 * Start the server. `mounts` maps a URL prefix ('/', '/_slate/film/', '/_slate/brand/') to a folder. `bundler` is
 * lib/bundle.mjs's bundler. Returns {origin, urlFor(absPath), served(), problems, close()}.
 */
export async function startServer({mounts, bundler, recordSource = null}) {
  const table = Object.entries(mounts)
    .filter(([, dir]) => dir)
    .map(([prefix, dir]) => ({prefix: prefix.endsWith('/') ? prefix : `${prefix}/`, dir: realOrNull(dir) ?? path.resolve(dir)}))
    .sort((a, b) => b.prefix.length - a.prefix.length);
  const served = new Map();
  const problems = [];
  let requests = 0;

  /** The file a URL path names, or null. */
  function fileFor(urlPath) {
    if (urlPath.includes('\0')) return null;
    if (urlPath.startsWith('/_slate/font/')) {
      const m = /^\/_slate\/font\/([A-Za-z0-9_-]+)(\.[a-z0-9]+)?$/.exec(urlPath);
      if (!m) return null;
      const abs = Buffer.from(m[1], 'base64url').toString('utf8');
      if (!path.isAbsolute(abs) || !FONT_EXTS.has(path.extname(abs).toLowerCase())) return null;
      const real = realOrNull(abs);
      return real && FONT_EXTS.has(path.extname(real).toLowerCase()) ? real : null;
    }
    for (const {prefix, dir} of table) {
      if (!(urlPath === prefix.slice(0, -1) || urlPath.startsWith(prefix))) continue;
      const rel = urlPath.slice(prefix.length);
      if (rel.split('/').some((seg) => seg === '..' || (seg.startsWith('.') && seg.length > 1))) return null;
      let file = path.resolve(dir, `.${path.sep}${rel}`);
      if (!inside(dir, file)) return null;
      let real = realOrNull(file);
      if (real && fs.statSync(real).isDirectory()) {
        file = path.join(real, 'index.html');
        real = realOrNull(file);
      }
      if (!real || !inside(dir, real)) return null;
      return real;
    }
    return null;
  }

  function send(res, status, type, body, key = null) {
    if (key) served.set(key, sha(body));
    res.writeHead(status, {'content-type': type, 'cache-control': 'no-store', 'content-length': body.length});
    res.end(body);
  }

  const server = http.createServer(async (req, res) => {
    requests++;
    try {
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        send(res, 405, 'text/plain; charset=utf-8', Buffer.from('Only GET and HEAD.\n'));
        return;
      }
      const url = new URL(req.url, 'http://127.0.0.1');
      let urlPath;
      try {
        urlPath = decodeURIComponent(url.pathname);
      } catch {
        send(res, 400, 'text/plain; charset=utf-8', Buffer.from('Bad path.\n'));
        return;
      }
      const file = fileFor(urlPath);
      if (!file) {
        send(res, 404, 'text/plain; charset=utf-8', Buffer.from(`Not found: ${urlPath}\n`));
        return;
      }
      const ext = path.extname(file).toLowerCase();
      const raw = url.searchParams.has('raw') || file.endsWith('.min.js');
      if (SCRIPT_EXTS.includes(ext) && !raw) {
        try {
          const {code} = await bundler.bundle(file);
          send(res, 200, TYPES['.js'], code, urlPath);
        } catch (err) {
          const text = describeBuildError(err);
          problems.push({kind: 'bundle', file, message: text});
          send(res, 500, TYPES['.js'], Buffer.from(`throw new Error(${JSON.stringify(`Slate couldn't compile ${path.basename(file)}:\n${text}`)});\n`));
        }
        return;
      }
      const body = fs.readFileSync(file);
      recordSource?.(file, body);
      send(res, 200, TYPES[ext] ?? 'application/octet-stream', body, urlPath);
    } catch (err) {
      problems.push({kind: 'server', message: String(err?.message ?? err)});
      if (!res.headersSent) send(res, 500, 'text/plain; charset=utf-8', Buffer.from('Server error.\n'));
      else res.destroy();
    }
  });
  server.keepAliveTimeout = 1000;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const {port} = server.address();
  const origin = `http://127.0.0.1:${port}`;

  return {
    origin,
    port,
    problems,
    requests: () => requests,
    /** The URL path a file is served at, or null when no mount holds it. */
    urlFor(absPath) {
      const real = realOrNull(absPath) ?? path.resolve(absPath);
      if (FONT_EXTS.has(path.extname(real).toLowerCase()) && !table.some(({dir}) => inside(dir, real))) return fontUrl(real);
      for (const {prefix, dir} of table) {
        if (inside(dir, real)) {
          const rel = path.relative(dir, real).split(path.sep).map(encodeURIComponent).join('/');
          return `${prefix}${rel}`;
        }
      }
      return null;
    },
    /** {urlPath: sha256} of every body served so far. */
    served: () => Object.fromEntries([...served.entries()].sort(([a], [b]) => a.localeCompare(b))),
    close: () =>
      new Promise((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections?.();
      }),
  };
}
