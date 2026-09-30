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
import {NODE_MODULES, log, usage} from './lib/common.mjs';

export const EXPECTED_MAJOR = 149;
export const GL_MODES = ['angle', 'swangle', 'egl', 'swiftshader', 'vulkan', 'angle-egl'];
export const DEFAULT_GL = 'angle';

export function chromiumOptions(gl = DEFAULT_GL) {
  if (!GL_MODES.includes(gl)) throw usage(`Unknown GL mode ${JSON.stringify(gl)}. Use one of: ${GL_MODES.join(', ')}.`);
  return {gl};
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
