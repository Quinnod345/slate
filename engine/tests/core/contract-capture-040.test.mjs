// Contract source integrity stays strict even when persisted freshness excludes bookkeeping.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {afterEach, describe, expect, test} from 'vitest';
import {contractsGate} from '../../remotion/check.mjs';
import {loadFilm} from '../../remotion/lib/film.mjs';
import {canonicalSource} from '../../remotion/lib/shot_scenes.mjs';
import {sha256Buffer} from '../../remotion/lib/common.mjs';

const roots = [];
afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, {recursive: true, force: true});
});
function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-contract-capture-'));
  roots.push(root);
  const put = (rel, value) => {
    const file = path.join(root, rel);
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, typeof value === 'string' ? value : JSON.stringify(value));
    return file;
  };
  const film = put('film.json', {id: 'neutral-contract', width: 640, height: 360,
    fps_num: 30000, fps_den: 1001, pins: {}, pin_log: []});
  const shots = put('timing/shots.json', {shots: [
    {id: 'N01', act: 1, frame_in: 0, frame_out: 120},
  ]});
  const scene = put('scenes/act1/index.tsx', 'export default function Neutral(){return null;}\n');
  return {root, film, shots, scene, ctx: loadFilm(root)};
}

describe('contract atomic raw capture', () => {
  test('retains the actual raw film digest before Python persists semantic freshness', async () => {
    const f = fixture();
    const out = await contractsGate(f.ctx, 1, {scene: {}}, 'handin.act1.contracts', {});
    expect(out.gate.status).toBe('PASS');
    expect(out.gate.inputs[canonicalSource(f.film)]).toBe(sha256Buffer(fs.readFileSync(f.film)));
  });
  test.each(['film', 'shots', 'scene'])('refuses %s tampering during boundary discovery', async field => {
    const f = fixture();
    const scene = {
      get contracts() {
        fs.appendFileSync(f[field], ' ');
        return {};
      },
    };
    await expect(contractsGate(f.ctx, 1, {scene}, 'handin.act1.contracts', {}))
      .rejects.toThrow(/Source changed after mixed-act read/);
  });
});
