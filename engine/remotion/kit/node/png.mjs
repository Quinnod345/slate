// PNG decoding and PSNR with nothing but Node's zlib, so golden stills and render proofs need no image library.
// Reads 8-bit, non-interlaced greyscale, grey + alpha, RGB and RGBA (what Remotion and ffmpeg write).
import fs from 'node:fs';
import zlib from 'node:zlib';

const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4};

/** Decode a PNG buffer: {width, height, channels, data} with one byte per channel, rows top to bottom. */
export function decodePng(buf) {
  if (!Buffer.isBuffer(buf)) buf = Buffer.from(buf);
  if (buf.length < 8 || !buf.subarray(0, 8).equals(SIGNATURE)) throw new Error('not a PNG file');
  let pos = 8;
  let width = 0;
  let height = 0;
  let depth = 0;
  let colorType = -1;
  let interlace = 0;
  const idat = [];
  while (pos + 8 <= buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString('ascii', pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      depth = data[8];
      colorType = data[9];
      interlace = data[12];
    } else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    pos += 12 + len;
  }
  const channels = CHANNELS[colorType];
  if (!channels) throw new Error(`PNG colour type ${colorType} isn't supported (greyscale, RGB and RGBA are)`);
  if (depth !== 8) throw new Error(`PNG bit depth ${depth} isn't supported (8-bit only)`);
  if (interlace) throw new Error('interlaced PNGs aren’t supported');
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  if (raw.length < height * (stride + 1)) throw new Error('the PNG’s image data is shorter than its size says');
  const out = new Uint8Array(height * stride);
  let prev = new Uint8Array(stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const cur = out.subarray(y * stride, (y + 1) * stride);
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? cur[x - channels] : 0;
      const b = prev[x];
      const c = x >= channels ? prev[x - channels] : 0;
      let v = line[x];
      switch (filter) {
        case 0:
          break;
        case 1:
          v += a;
          break;
        case 2:
          v += b;
          break;
        case 3:
          v += (a + b) >> 1;
          break;
        case 4: {
          const p = a + b - c;
          const pa = Math.abs(p - a);
          const pb = Math.abs(p - b);
          const pc = Math.abs(p - c);
          v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
          break;
        }
        default:
          throw new Error(`unknown PNG filter ${filter} on row ${y}`);
      }
      cur[x] = v & 255;
    }
    prev = cur;
  }
  return {width, height, channels, data: out};
}

export function readPng(file) {
  return decodePng(fs.readFileSync(file));
}

function rgbAt(img, i) {
  const o = i * img.channels;
  if (img.channels >= 3) return [img.data[o], img.data[o + 1], img.data[o + 2]];
  return [img.data[o], img.data[o], img.data[o]];
}

/**
 * PSNR in dB between two images over their RGB channels (alpha ignored). Infinity when they are identical. Throws when
 * their sizes differ (a different size is never "close").
 */
export function psnr(a, b) {
  if (a.width !== b.width || a.height !== b.height) throw new Error(`sizes differ: ${a.width}×${a.height} and ${b.width}×${b.height}`);
  const n = a.width * a.height;
  let se = 0;
  for (let i = 0; i < n; i++) {
    const p = rgbAt(a, i);
    const q = rgbAt(b, i);
    for (let k = 0; k < 3; k++) {
      const d = p[k] - q[k];
      se += d * d;
    }
  }
  if (se === 0) return Infinity;
  const mse = se / (n * 3);
  return 10 * Math.log10((255 * 255) / mse);
}

/** PSNR between two PNG files. */
export function psnrFiles(fileA, fileB) {
  return psnr(readPng(fileA), readPng(fileB));
}
