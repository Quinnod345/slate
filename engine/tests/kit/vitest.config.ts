// The kit's own vitest config: run from engine/ with
//   npx --no-install vitest run --config tests/kit/vitest.config.ts
// `@slate/kit` points at the kit, `@film/data` at the neutral fixture film (25 fps, 960 × 540).
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vitest/config';

const here = path.dirname(fileURLToPath(import.meta.url));
const engine = path.resolve(here, '../..');
const kit = path.join(engine, 'remotion/kit');

export default defineConfig({
  root: engine,
  resolve: {
    alias: [
      {find: /^@slate\/kit$/, replacement: path.join(kit, 'index.ts')},
      {find: /^@slate\/kit\/(.*)$/, replacement: `${kit}/$1`},
      {find: /^@film\/data$/, replacement: path.join(here, 'fixtures/film-data.ts')},
    ],
  },
  oxc: {jsx: {runtime: 'automatic'}},
  test: {
    include: ['tests/kit/**/*.test.ts', 'tests/kit/**/*.test.tsx'],
    environment: 'node',
    testTimeout: 30000,
    pool: 'forks',
  },
});
