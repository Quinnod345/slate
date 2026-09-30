// The Node helpers: the PNG decoder and PSNR, and the act collector the engine's emit-events and check call.
import path from 'node:path';
import zlib from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';
import {decodePng, psnr} from '../../remotion/kit/node/png.mjs';
import {collectAct} from '../../remotion/kit/node/collect.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const FILM = path.join(here, 'fixtures/film-data.ts');

/** A tiny PNG encoder for the test: every row uses the given filter type, so the decoder sees all five. */
function encodePng(width: number, height: number, rgba: Uint8Array, filterFor: (y: number) => number): Buffer {
  const stride = width * 4;
  const raw = Buffer.alloc(height * (stride + 1));
  let prev = new Uint8Array(stride);
  for (let y = 0; y < height; y++) {
    const f = filterFor(y);
    const cur = rgba.subarray(y * stride, (y + 1) * stride);
    raw[y * (stride + 1)] = f;
    for (let x = 0; x < stride; x++) {
      const a = x >= 4 ? cur[x - 4] : 0;
      const b = prev[x];
      const c = x >= 4 ? prev[x - 4] : 0;
      let pred = 0;
      if (f === 1) pred = a;
      else if (f === 2) pred = b;
      else if (f === 3) pred = (a + b) >> 1;
      else if (f === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        pred = pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      raw[y * (stride + 1) + 1 + x] = (cur[x] - pred) & 255;
    }
    prev = cur;
  }
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    return Buffer.concat([len, Buffer.from(type, 'ascii'), data, Buffer.alloc(4)]); // CRC isn't checked by the decoder
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

describe('PNG and PSNR', () => {
  const w = 7;
  const h = 10;
  const px = new Uint8Array(w * h * 4).map((_, i) => (i * 37 + (i >> 3) * 11) & 255);

  it('decodes every filter type exactly', () => {
    const img = decodePng(encodePng(w, h, px, (y) => y % 5));
    expect(img.width).toBe(w);
    expect(img.height).toBe(h);
    expect(img.channels).toBe(4);
    expect(Buffer.from(img.data).equals(Buffer.from(px))).toBe(true);
  });

  it('gives infinity for identical images and a finite, sensible number otherwise', () => {
    const a = decodePng(encodePng(w, h, px, () => 0));
    expect(psnr(a, a)).toBe(Infinity);
    const nudged = px.slice();
    nudged[0] ^= 4;
    const b = decodePng(encodePng(w, h, nudged, () => 0));
    const v = psnr(a, b);
    expect(v).toBeGreaterThan(40);
    expect(Number.isFinite(v)).toBe(true);
  });

  it('refuses what it can’t compare', () => {
    expect(() => decodePng(Buffer.from('not a png'))).toThrow(/not a PNG/);
    const a = decodePng(encodePng(2, 2, new Uint8Array(16), () => 0));
    const b = decodePng(encodePng(3, 2, new Uint8Array(24), () => 0));
    expect(() => psnr(a, b)).toThrow(/sizes differ/);
  });
});

describe('the act collector (emit-events, check, the motion trace)', () => {
  it("collects a clean act's events and moves in Node, with nothing missed", async () => {
    const r = await collectAct({act: 1, beats: [path.join(here, 'fixtures/beats-act1.ts')], filmData: FILM});
    expect(r.ok).toBe(true);
    expect(r.misses).toEqual([]);
    expect((r.events as {id: string}[]).map((e) => e.id)).toEqual(['title_lands', 'parts_arrive']);
    expect(r.trace.map((m: {id: string}) => m.id)).toEqual(['title-rise', 'title-scale', 'parts-carry']);
    const rise = r.trace[0];
    expect(rise.samples[0][1]).toBe(24);
    expect(rise.samples[rise.samples.length - 1][1]).toBe(0);
    expect(rise.entrance).toBe(true);
  }, 60000);

  it('loads a half-written act anyway and lists every problem', async () => {
    const r = await collectAct({act: 2, beats: [path.join(here, 'fixtures/beats-act2-broken.ts')], filmData: FILM});
    expect(r.ok).toBe(false);
    expect(r.missLabels).toContain('K03:elephant');
    expect(r.motionProblems.join(' ')).toMatch(/"bouncy"/);
    expect(r.eventProblems.join(' ')).toMatch(/kind "kaboom"/);
  }, 60000);

  it('reports an act with no beats as silent only when given a reason', async () => {
    const quiet = await collectAct({act: 2, beats: [], filmData: FILM, silentReason: 'a held card, silent on purpose'});
    expect(quiet.events).toEqual({silent: true, reason: 'a held card, silent on purpose'});
    const empty = await collectAct({act: 2, beats: [], filmData: FILM});
    expect(empty.ok).toBe(false);
  }, 60000);
});

describe('the two-baseline rule', () => {
  it('passes a render that matches either stored baseline, and fails one that matches neither', async () => {
    const fs = await import('node:fs');
    const os = await import('node:os');
    const {compareStill} = await import('../../remotion/kit/node/golden.mjs');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-two-baselines-'));
    const w = 16;
    const h = 8;
    const img = (seed: number) => new Uint8Array(w * h * 4).map((_, i) => (i % 4 === 3 ? 255 : (i * seed) & 255));
    const write = (name: string, px: Uint8Array) => {
      const f = path.join(dir, name);
      fs.writeFileSync(f, encodePng(w, h, px, () => 0));
      return f;
    };
    const render = write('render.png', img(7));
    const far = write('a.png', img(13)); // a different raster
    const same = write('b.png', img(7)); // the GPU's other raster, identical to this run
    expect(compareStill(render, [far]).pass).toBe(false);
    const r = compareStill(render, [far, same]);
    expect(r.pass).toBe(true);
    expect(r.baselines).toBe(2);
    expect(r.psnr[1]).toBe('identical');
    expect(compareStill(render, [path.join(dir, 'missing.png')]).error).toBe('no baseline');
    fs.rmSync(dir, {recursive: true, force: true});
  });
});
