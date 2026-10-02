// The headless browser Remotion renders with: chrome-headless-shell at the version Remotion 4.0.521 was tested
// against (149.0.7790.0), downloaded by Remotion into <engine>/node_modules/.remotion. It is only ever pointed at
// Remotion's own local bundle server; nothing here browses the web.
//
// GL: 'angle' (the GPU through ANGLE, Metal on a Mac) is what the films rendered with. 'swangle' (software) is
// bit-exact run to run, so pixel contracts and golden stills use it: the GPU raster differs by about one level
// between runs (~66 dB), which is enough to fail a bit-identical check.
import fs from 'node:fs';
import path from 'node:path';
import {ensureBrowser} from '@remotion/renderer';
import {NODE_MODULES, REMOTION_DIR, log, usage} from './lib/common.mjs';

export const EXPECTED_MAJOR = 149;
export const GL_MODES = ['angle', 'swangle', 'egl', 'swiftshader', 'vulkan', 'angle-egl'];
export const DEFAULT_GL = 'angle';

export function chromiumOptions(gl = DEFAULT_GL) {
  if (!GL_MODES.includes(gl)) throw usage(`Unknown GL mode ${JSON.stringify(gl)}. Use one of: ${GL_MODES.join(', ')}.`);
  return {gl};
}

/** Three minutes: the kit's font gate (kit/fonts.tsx) holds its first frame for the same time. */
export const DEFAULT_RENDER_TIMEOUT_MS = 180000;

/**
 * How long Remotion waits for a page to be ready: the component's first render and every delayRender handle that
 * doesn't set its own. Every selectComposition, renderFrames and renderStill passes it. Remotion's own default is 30 s,
 * which a busy Mac blows through while the font gate loads and measures the brand's faces: `slate demo` failed exactly
 * that way at load 600 on 2026-10-01. SLATE_RENDER_TIMEOUT_MS overrides it (whole milliseconds, at least 1,000).
 */
export function renderTimeoutMs(env = process.env) {
  const raw = env.SLATE_RENDER_TIMEOUT_MS;
  if (raw === undefined || raw === '') return DEFAULT_RENDER_TIMEOUT_MS;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1000) throw usage(`SLATE_RENDER_TIMEOUT_MS is ${JSON.stringify(raw)}; give whole milliseconds, at least 1000.`);
  return n;
}

/** The flags that make Chrome finish every tile and decode every image before it draws a frame (chrome-complete.sh). */
export const COMPLETE_FLAGS = ['--run-all-compositor-stages-before-draw', '--disable-checker-imaging'];
/** Remotion's headless shell with COMPLETE_FLAGS: Remotion's openBrowser takes no extra flags, so they ride on the executable. */
export const COMPLETE_SHELL = path.join(REMOTION_DIR, 'chrome-complete.sh');

let warned = false;

/**
 * The browser executable every Remotion render, still and contract still passes: chrome-complete.sh, so no tile is
 * left out of a sub-frame (a one-frame grey veil, the films' lesson L-kit-S008). Null, so Remotion falls back to its
 * own shell as it is, only while the shell isn't downloaded yet (Remotion then says what's missing) or when the wrapper
 * isn't executable (said once on stderr: a render without it can show the veil).
 */
export function browserExecutable() {
  if (!browserStatus().present) return null;
  try {
    fs.accessSync(COMPLETE_SHELL, fs.constants.X_OK);
    return COMPLETE_SHELL;
  } catch {
    if (!warned) {
      warned = true;
      log(`warning: ${COMPLETE_SHELL} isn't executable, so Chrome may leave unfinished tiles out of a sub-frame (chmod +x it, or run \`slate engine install\` again)`);
    }
    return null;
  }
}

/** What's on disk, without downloading anything: {present, version, path}. */
export function browserStatus() {
  const root = path.join(NODE_MODULES, '.remotion', 'chrome-headless-shell');
  const versionFile = path.join(root, 'VERSION');
  const version = fs.existsSync(versionFile) ? fs.readFileSync(versionFile, 'utf8').trim() : null;
  let exe = null;
  if (fs.existsSync(root)) {
    for (const platform of fs.readdirSync(root)) {
      const candidate = path.join(root, platform, `chrome-headless-shell-${platform}`, 'chrome-headless-shell');
      if (fs.existsSync(candidate)) exe = candidate;
    }
  }
  const major = version ? Number(version.split('.')[0]) : null;
  return {present: Boolean(exe), version, major, expected_major: EXPECTED_MAJOR, matches: major === EXPECTED_MAJOR, path: exe};
}

/** The browser-ensure command: download chrome-headless-shell if it isn't there (Remotion's own ensureBrowser). */
export async function ensureHeadlessShell() {
  const before = browserStatus();
  let lastPct = -10;
  const status = await ensureBrowser({
    chromeMode: 'headless-shell',
    logLevel: 'warn',
    onBrowserDownload: ({chromeMode}) => {
      log(`downloading ${chromeMode} for Remotion (about 100 MB, once)`);
      return {
        version: null,
        onProgress: ({percent}) => {
          const pct = Math.floor(percent * 100);
          if (pct >= lastPct + 10) {
            lastPct = pct;
            log(`  ${pct}%`);
          }
        },
      };
    },
  });
  const after = browserStatus();
  return {browser: after, downloaded: !before.present && after.present, remotion_status: status.type};
}
