// The headless browser for web scenes, and the wall around it.
//
// One headless Chrome for the whole engine: chrome-headless-shell, which Remotion downloads into
// engine/node_modules/.remotion (engine/remotion/browser.mjs knows where). puppeteer's own Chrome download is skipped on
// purpose, and puppeteer only ever drives that shell against our own 127.0.0.1 server, never a website.
//
// Remotion's packages aren't needed to use the shell: point SLATE_WEB_BROWSER (or the `browser` arg) at any
// chrome-headless-shell and the web engine never loads a Remotion module (PLAN decision 5).
//
// The network is closed three ways, and every attempt to cross it is reported:
//   1. request interception: anything that isn't the local server's origin (or data:/blob:) is aborted and listed;
//   2. a wall: every non-loopback connection goes to a proxy on 127.0.0.1 that writes down where it was headed and
//      hangs up (WebSockets, service workers, new windows: whatever interception doesn't see);
//   3. host resolution: no name resolves except the loopback address.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {childEnv, missing, usage} from '../../remotion/lib/common.mjs';
import {installPageClock} from './page-clock.mjs';

/** The GL flags Remotion 4.0.521 passes for each of its modes (open-browser.js getOpenGlRenderer, v4 behaviour). */
export const GL_FLAGS = {
  angle: ['--use-gl=angle'],
  swangle: ['--use-gl=angle', '--use-angle=swiftshader'],
  'angle-egl': ['--use-gl=angle', '--use-angle=gl-egl'],
  egl: ['--use-gl=egl'],
  swiftshader: ['--use-gl=swiftshader'],
  vulkan: ['--use-angle=vulkan', '--use-vulkan=native', '--disable-vulkan-fallback-to-gl-for-testing', '--disable-vulkan-surface', '--ignore-gpu-blocklist', '--enable-gpu'],
};
export const GL_MODES = Object.keys(GL_FLAGS);
/**
 * The GPU through ANGLE (Metal on a Mac), as the films rendered. 'swangle' (software) is bit-exact run to run; the GPU
 * raster can differ by about one level between runs, so pixel contracts and determinism proofs use swangle.
 */
export const DEFAULT_GL = 'angle';

export function glFlags(gl) {
  if (!GL_FLAGS[gl]) throw usage(`Unknown GL mode ${JSON.stringify(gl)}. Use one of: ${GL_MODES.join(', ')}.`);
  // Chrome 149 only runs WebGL on SwiftShader when asked to by name (Remotion's software modes pass the same flag).
  return gl === 'swangle' || gl === 'swiftshader' ? [...GL_FLAGS[gl], '--enable-unsafe-swiftshader'] : GL_FLAGS[gl];
}

/**
 * The rest of the command line: Remotion 4.0.521's own flags where they matter to a frame (an sRGB colour profile, no
 * throttling of pages in the background, hidden scrollbars, no Keychain prompts on macOS), minus its proxy bypass,
 * because here the proxy is the wall.
 */
export const CHROME_FLAGS = [
  '--force-color-profile=srgb',
  '--hide-scrollbars',
  '--mute-audio',
  '--font-render-hinting=none',
  '--disable-background-timer-throttling',
  '--disable-backgrounding-occluded-windows',
  '--disable-renderer-backgrounding',
  '--disable-ipc-flooding-protection',
  '--disable-hang-monitor',
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-domain-reliability',
  '--disable-client-side-phishing-detection',
  '--disable-default-apps',
  '--disable-extensions',
  '--disable-sync',
  '--disable-breakpad',
  '--disable-features=Translate,BackForwardCache,IntensiveWakeUpThrottling,MediaRouter,OptimizationHints',
  '--metrics-recording-only',
  '--no-first-run',
  '--no-pings',
  '--no-default-browser-check',
  '--password-store=basic',
  '--use-mock-keychain',
  '--disable-checker-imaging',
  '--run-all-compositor-stages-before-draw',
  '--autoplay-policy=no-user-gesture-required',
  // WebRTC may only use the proxy (the wall), never a raw UDP socket to somewhere else
  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--webrtc-ip-handling-policy=disable_non_proxied_udp',
];

function isExecutable(p) {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

/**
 * The headless shell to launch: the `browser` arg, then SLATE_WEB_BROWSER, then Remotion's download. Returns
 * {path, version, source}. Never downloads anything: a missing browser is a missing prerequisite with the fix.
 */
export async function findBrowser(explicit = null) {
  const given = explicit ?? process.env.SLATE_WEB_BROWSER ?? null;
  if (given) {
    const p = path.resolve(given);
    if (!isExecutable(p)) throw missing(`There's no headless browser at ${p}.`, 'Point it at a chrome-headless-shell binary, or leave it unset to use the engine\'s own.');
    return {path: p, version: null, source: explicit ? 'args' : 'SLATE_WEB_BROWSER'};
  }
  const {browserStatus} = await import('../../remotion/browser.mjs');
  const status = browserStatus();
  if (!status.present) {
    throw missing(
      "The engine's headless browser (chrome-headless-shell) isn't installed.",
      'Run `node engine/cli.mjs browser-ensure` (or `slate doctor --install`), which downloads it once.',
    );
  }
  return {path: status.path, version: status.version, source: 'remotion'};
}

/** The wall: a proxy on 127.0.0.1 that records where each connection was going, then refuses it. */
export async function startWall() {
  const refused = [];
  const sockets = new Set();
  const server = net.createServer((sock) => {
    sockets.add(sock);
    sock.on('close', () => sockets.delete(sock));
    sock.on('error', () => {});
    sock.setTimeout(2000, () => sock.destroy());
    sock.once('data', (d) => {
      const line = d.toString('latin1').split('\r\n')[0].slice(0, 300);
      const [method, target] = line.split(' ');
      refused.push({method: method ?? null, target: target ?? line, at: new Date().toISOString()});
      sock.end('HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\nconnection: close\r\n\r\n');
    });
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return {
    port: server.address().port,
    refused,
    close: () =>
      new Promise((resolve) => {
        for (const s of sockets) s.destroy();
        server.close(() => resolve());
      }),
  };
}

/** Launch the headless shell. Signals are Slate's to handle (cli.mjs turns them into one JSON result). */
export async function launchBrowser({executablePath, gl, width, height, wallPort, timeoutMs = 60000}) {
  const puppeteer = (await import('puppeteer')).default;
  const args = [
    ...glFlags(gl),
    // every connection that isn't to 127.0.0.1 goes to the wall (Chrome sends loopback direct by itself), and no name
    // resolves but the loopback address
    `--proxy-server=http://127.0.0.1:${wallPort}`,
    '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1',
    ...CHROME_FLAGS,
  ];
  return puppeteer.launch({
    executablePath,
    headless: 'shell',
    pipe: true,
    args,
    defaultViewport: {width, height, deviceScaleFactor: 1},
    env: childEnv(),
    timeout: timeoutMs,
    protocolTimeout: Math.max(timeoutMs, 180000),
    handleSIGINT: false,
    handleSIGTERM: false,
    handleSIGHUP: false,
  });
}

/**
 * Open one page of the scene: interception, the clock and the scene globals go in before the page's own scripts.
 * Returns {page, cdp, errors, consoleLines, blocked}. Nothing is loaded until `goto`.
 */
export async function preparePage(browser, {origin, clock, globals, width, height, frameRef}) {
  const page = await browser.newPage();
  await page.setViewport({width, height, deviceScaleFactor: 1});
  await page.emulateMediaFeatures([
    {name: 'prefers-reduced-motion', value: 'no-preference'},
    {name: 'prefers-color-scheme', value: 'light'},
  ]);
  const blocked = [];
  const errors = [];
  const consoleLines = [];
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    const url = req.url();
    if (url === origin || url.startsWith(`${origin}/`) || url.startsWith('data:') || url.startsWith('blob:')) {
      req.continue().catch(() => {});
      return;
    }
    blocked.push({url: url.slice(0, 500), type: req.resourceType(), method: req.method(), frame: frameRef ? frameRef() : null});
    req.abort('blockedbyclient').catch(() => {});
  });
  page.on('pageerror', (err) => {
    errors.push({message: String(err?.message ?? err).slice(0, 2000), stack: String(err?.stack ?? '').split('\n').slice(0, 6).join('\n'), frame: frameRef ? frameRef() : null});
  });
  page.on('console', (msg) => {
    const type = msg.type();
    if (type !== 'error' && type !== 'warn' && type !== 'warning') return;
    if (consoleLines.length < 50) consoleLines.push({type, text: msg.text().slice(0, 500)});
  });
  page.on('popup', (p) => {
    p.close().catch(() => {});
  });
  await page.evaluateOnNewDocument(installPageClock, clock);
  await page.evaluateOnNewDocument((g) => {
    Object.defineProperty(window, '__SLATE__', {value: Object.freeze(g), configurable: false, enumerable: false, writable: false});
  }, globals);
  const cdp = await page.createCDPSession();
  return {page, cdp, errors, consoleLines, blocked};
}

/**
 * Close any page a scene opens by itself (window.open): only the renderer's own pages stay. A page with an opener
 * came from a scene; the renderer's pages have none.
 */
export function guardTargets(browser, onStray) {
  browser.on('targetcreated', async (target) => {
    if (target.type() !== 'page' || !target.opener()) return;
    onStray?.(target.url());
    try {
      const p = await target.page();
      await p?.close();
    } catch {
      // gone already
    }
  });
}
