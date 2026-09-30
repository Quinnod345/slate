// The engine's test runner. `npm test` (vitest run) from engine/ runs every suite:
//   tests/core   the engine boundary (this config): cli.mjs, entries, bundles, render_mb, stills, events, the check
//   tests/<x>    any suite with its own tests/<x>/vitest.config.*, under that config (the kit's, the AE lane's…)
// One suite alone: `npx --no-install vitest run --project core` (or --project kit, ae, story…).
//
// Renders and stills start headless Chrome, so test files run one at a time: the Mac stays usable, and two suites
// never render at once.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vitest/config';

const engine = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_NAMES = ['vitest.config.ts', 'vitest.config.mts', 'vitest.config.js', 'vitest.config.mjs'];

/**
 * Every tests/<x>/vitest.config.* as a project. A suite config that sets a top-level `root` (the engine folder, so
 * its globs read from there) is dropped by vitest when it's listed as a config file under this root config, so it's
 * loaded with `extends` and named after its folder; one that sets `test.root` loads as the config file itself.
 */
function suites() {
  const dir = path.join(engine, 'tests');
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const d of fs.readdirSync(dir, {withFileTypes: true})) {
    if (!d.isDirectory() || d.name === 'core') continue;
    const file = CONFIG_NAMES.map((n) => path.join(dir, d.name, n)).find((f) => fs.existsSync(f));
    if (!file) continue;
    const topLevelRoot = /^ {2}root:/m.test(fs.readFileSync(file, 'utf8'));
    out.push(topLevelRoot ? {extends: file, test: {name: d.name}} : path.relative(engine, file));
  }
  return out;
}

export default defineConfig({
  test: {
    fileParallelism: false,
    maxWorkers: 1,
    projects: [
      ...suites(),
      {
        resolve: {
          alias: [
            {find: /^@slate\/kit$/, replacement: path.join(engine, 'remotion/kit')},
            {find: /^@slate\/kit\/(.*)$/, replacement: `${path.join(engine, 'remotion/kit')}/$1`},
            {find: /^@film\/data$/, replacement: path.join(engine, 'remotion/data/neutral-film-data.ts')},
          ],
        },
        test: {
          name: 'core',
          root: engine,
          include: ['tests/core/**/*.test.mjs'],
          environment: 'node',
          pool: 'forks',
          testTimeout: 240_000,
          hookTimeout: 300_000,
        },
      },
    ],
  },
});
