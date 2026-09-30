// Render stills of kit compositions with @remotion/renderer directly: one bundle, one browser, then both are gone.
// Used by the golden stills and by brand onboarding's review sheet.
//
// The browser is always passed explicitly and never downloaded here: headless Chrome is set up by the installer
// (asking first) or the doctor. Child processes get BROWSER=none, and the browser only ever loads our own bundle.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
export const KIT_DIR = path.resolve(here, '..');
export const ENGINE_DIR = path.resolve(KIT_DIR, '../..');
export const REVIEW_ENTRY = path.join(KIT_DIR, 'review/entry.tsx');
export const GATE_MARKER = 'SLATE_FONT_GATE';

process.env.BROWSER = 'none';

/** Where Remotion keeps its headless shell for a platform, inside an engine folder. */
function managedShell(engineDir) {
  const base = path.join(engineDir, 'node_modules/.remotion/chrome-headless-shell');
  return [
    path.join(base, 'mac-arm64/chrome-headless-shell-mac-arm64/chrome-headless-shell'),
    path.join(base, 'mac-x64/chrome-headless-shell-mac-x64/chrome-headless-shell'),
    path.join(base, 'linux64/chrome-headless-shell-linux64/chrome-headless-shell'),
  ];
}

/** The headless browser to use: an explicit path, SLATE_CHROME, or the engine's managed one. Null when there is none. */
export function findBrowser(explicit) {
  const candidates = [explicit, process.env.SLATE_CHROME, ...managedShell(ENGINE_DIR)].filter(Boolean);
  for (const c of candidates) if (fs.existsSync(c)) return c;
  return null;
}

/** Find the font gate's report in a line of browser output or an error message. */
export function findGateReport(text) {
  const s = String(text ?? '');
  const i = s.indexOf(GATE_MARKER);
  if (i < 0) return null;
  const start = s.indexOf('{', i);
  if (start < 0) return null;
  let depth = 0;
  let inStr = false;
  for (let j = start; j < s.length; j++) {
    const ch = s[j];
    if (inStr) {
      if (ch === '\\') j++;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) {
      try {
        return JSON.parse(s.slice(start, j + 1));
      } catch {
        return null;
      }
    }
  }
  return null;
}

/**
 * Bundle an entry with `@slate/kit` and `@film/data` aliased. Returns the bundle's folder (serveUrl); pass it to
 * removeBundle() when done (each bundle is a few hundred MB).
 */
export async function bundleKit({entry = REVIEW_ENTRY, filmData, alias = {}} = {}) {
  if (!filmData) throw new Error('bundleKit needs filmData: the path of the module `@film/data` points at');
  const {bundle} = await import('@remotion/bundler');
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-kit-bundle-'));
  return bundle({
    entryPoint: path.resolve(entry),
    outDir,
    enableCaching: false,
    onProgress: () => undefined,
    webpackOverride: (config) => ({
      ...config,
      resolve: {
        ...config.resolve,
        alias: {
          ...(config.resolve?.alias ?? {}),
          '@slate/kit': KIT_DIR,
          '@film/data$': path.resolve(filmData),
          ...alias,
        },
      },
    }),
  });
}

export function removeBundle(serveUrl) {
  if (serveUrl && fs.existsSync(serveUrl)) fs.rmSync(serveUrl, {recursive: true, force: true});
}

/**
 * Render stills [{id, frame, out}] from a bundle in one browser. Each result says whether it rendered, and carries the
 * font gate's report when the page logged one (or failed on one).
 */
export async function renderKitStills({serveUrl, stills, scale = 1, gl = 'swangle', browserExecutable, inputProps = {}}) {
  if (!browserExecutable) throw new Error('renderKitStills needs a browserExecutable (it never downloads one)');
  const {openBrowser, renderStill, selectComposition} = await import('@remotion/renderer');
  const chromiumOptions = {gl};
  const browser = await openBrowser('chrome', {browserExecutable, chromiumOptions, logLevel: 'error'});
  const results = [];
  try {
    for (const s of stills) {
      const logs = [];
      const onBrowserLog = (log) => logs.push(String(log?.text ?? ''));
      const started = Date.now();
      try {
        const composition = await selectComposition({serveUrl, id: s.id, inputProps, puppeteerInstance: browser, browserExecutable, chromiumOptions, logLevel: 'error', onBrowserLog});
        fs.mkdirSync(path.dirname(s.out), {recursive: true});
        await renderStill({composition, serveUrl, output: s.out, frame: s.frame, scale, imageFormat: 'png', inputProps, puppeteerInstance: browser, browserExecutable, chromiumOptions, overwrite: true, logLevel: 'error', onBrowserLog, timeoutInMilliseconds: 90000});
        const gate = logs.map(findGateReport).find(Boolean) ?? null;
        results.push({id: s.id, frame: s.frame, out: s.out, ok: true, ms: Date.now() - started, fontGate: gate});
      } catch (e) {
        const message = String(e?.message ?? e);
        const gate = findGateReport(message) ?? logs.map(findGateReport).find(Boolean) ?? null;
        results.push({id: s.id, frame: s.frame, out: s.out, ok: false, ms: Date.now() - started, error: message.split('\n')[0].slice(0, 600), fontGate: gate});
      }
    }
  } finally {
    await browser.close({silent: true}).catch(() => undefined);
  }
  return results;
}
