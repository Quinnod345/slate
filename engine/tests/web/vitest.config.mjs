// The open web renderer's suite: run from engine/ with
//   npx --no-install vitest run --config tests/web/vitest.config.mjs
// (or with every suite: `npm test`). unit.test.mjs and runtime.test.ts need no browser; render.test.mjs drives the real
// renderer (headless Chrome, ffmpeg) on tiny fixture films, one test file at a time, so the Mac stays usable.
// '@film/data' is the web fixture film (25 fps, 320 x 180) for the runtime's tests in Node.
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vitest/config';

const here = path.dirname(fileURLToPath(import.meta.url));
const engine = path.resolve(here, '../..');
const kit = path.join(engine, 'remotion/kit');
const runtime = path.join(engine, 'web/runtime');

export default defineConfig({
  root: engine,
  resolve: {
    alias: [
      {find: /^@slate\/kit$/, replacement: path.join(kit, 'index.ts')},
      {find: /^@slate\/kit\/(.*)$/, replacement: `${kit}/$1`},
      {find: /^@slate\/web$/, replacement: path.join(runtime, 'index.ts')},
      {find: /^@slate\/web\/(.*)$/, replacement: `${runtime}/$1`},
      {find: /^@film\/data$/, replacement: path.join(here, 'fixtures/film-data.ts')},
    ],
  },
  test: {
    name: 'web',
    include: ['tests/web/**/*.test.mjs', 'tests/web/**/*.test.ts'],
    environment: 'node',
    pool: 'forks',
    fileParallelism: false,
    maxWorkers: 1,
    testTimeout: 240_000,
    hookTimeout: 240_000,
  },
});
