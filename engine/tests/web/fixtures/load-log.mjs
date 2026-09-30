// Preloaded with `node --import` in a test: writes down every module the process resolves, so the test can prove the
// web renderer ran without loading a single Remotion package (docs/PLAN.md decision 5).
import fs from 'node:fs';
import {registerHooks} from 'node:module';

const log = process.env.SLATE_TEST_LOAD_LOG;
registerHooks({
  resolve(specifier, context, next) {
    const resolved = next(specifier, context);
    if (log) fs.appendFileSync(log, `${resolved.url}\n`);
    return resolved;
  },
});
