// Golden stills (Quality test 4): the kit's compositions on software GL, each within 45 dB PSNR of its baseline in
// tests/kit/golden/, and the font gate failing a face that exists nowhere. Renders take a minute or more, so this runs
// only where headless Chrome is installed (SLATE_CHROME, or the engine's managed copy); elsewhere it says why it
// skipped. SLATE_SKIP_GOLDEN=1 skips it on purpose.
import {spawnSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';
import {findBrowser} from '../../remotion/kit/node/stills.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const engine = path.resolve(here, '../..');
const browser = process.env.SLATE_SKIP_GOLDEN === '1' ? null : findBrowser();

describe('golden stills', () => {
  it.skipIf(!browser)(
    'render every kit still within 45 dB of its baseline on software GL, and fail the render on a missing face',
    () => {
      const run = spawnSync(process.execPath, [path.join(engine, 'remotion/kit/node/golden.mjs'), '--json'], {cwd: engine, env: {...process.env, BROWSER: 'none'}, encoding: 'utf8', timeout: 900000, maxBuffer: 64 * 1024 * 1024});
      const line = run.stdout
        .trim()
        .split('\n')
        .reverse()
        .find((l) => l.startsWith('{"pass"') || l.startsWith('{"skipped"'));
      expect(line, run.stderr.slice(-2000)).toBeTruthy();
      const r = JSON.parse(line!);
      expect(r.gl).toBe('swangle');
      for (const s of r.stills) {
        expect(s.rendered, `${s.id}@${s.frame}: ${s.error}`).toBe(true);
        expect(s.pass, `${s.id}@${s.frame}: ${JSON.stringify(s.psnr)}`).toBe(true);
      }
      expect(r.stills.length).toBeGreaterThanOrEqual(8);
      expect(r.fontGate.pass, JSON.stringify(r.fontGate)).toBe(true);
      expect(r.fontGate.status).toBe('FAIL');
      expect(r.pass).toBe(true);
    },
    900000,
  );

  it('says why when it can’t run here', () => {
    if (browser) return;
    expect(findBrowser('/nowhere/chrome')).toBeNull();
  });
});
