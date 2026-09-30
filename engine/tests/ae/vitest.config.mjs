// The After Effects lane's suite: run from engine/ with
//   npx --no-install vitest run --config tests/ae/vitest.config.mjs
// (or with every suite: `npm test`). It runs Slate's ExtendScript in node: the ease vectors against the shared
// tests/vectors files, the ES3 lint, and the build, audit, queue, trace and smoke scripts against a mock of AE's
// scripting DOM (engine/ae/tests/ae_mock.mjs). Nothing here launches After Effects.
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vitest/config';

const here = path.dirname(fileURLToPath(import.meta.url));
const engine = path.resolve(here, '../..');

export default defineConfig({
  root: engine,
  test: {
    name: 'ae',
    include: ['tests/ae/**/*.test.mjs'],
    environment: 'node',
    testTimeout: 60000,
    pool: 'forks',
  },
});
