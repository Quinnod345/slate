// The story/timing suite: the shared spoken-form vectors (tests/vectors/spoken.json) checked from TypeScript.
// Runs with the engine's other suites (`npm test`), or alone: `npx --no-install vitest run --project story`.
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {defineConfig} from 'vitest/config';

const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  test: {
    name: 'story',
    root: here,
    include: ['**/*.test.ts'],
    environment: 'node',
  },
});
