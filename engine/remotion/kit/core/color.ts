// Colour maths for the token adapter: parse, mix, alpha, WCAG luminance and contrast. Pure and brand-free: the only
// literals are the two ends of the scale, white and black, which a mix or a contrast check needs as anchors.

export type RGBA = {r: number; g: number; b: number; a: number};

export const WHITE = '#FFFFFF';
export const BLACK = '#000000';

/** Parse #RGB, #RRGGBB, #RRGGBBAA, rgb() or rgba(). Returns null for anything else. */
export function parseColor(input: string): RGBA | null {
  if (typeof input !== 'string') return null;
  const s = input.trim();
  let m = s.match(/^#([0-9a-f]{3,8})$/i);
  if (m) {
    const h = m[1];
    if (h.length === 3 || h.length === 4) {
      const [r, g, b, a] = [...h].map((c) => parseInt(c + c, 16));
      return {r, g, b, a: h.length === 4 ? a / 255 : 1};
    }
    if (h.length === 6 || h.length === 8) {
      const v = (i: number) => parseInt(h.slice(i, i + 2), 16);
      return {r: v(0), g: v(2), b: v(4), a: h.length === 8 ? v(6) / 255 : 1};
    }
    return null;
  }
  m = s.match(/^rgba?\(([^)]+)\)$/i);
  if (m) {
    const parts = m[1].split(/[\s,/]+/).filter(Boolean).map((p) => (p.endsWith('%') ? (parseFloat(p) / 100) * 255 : parseFloat(p)));
    if (parts.length < 3 || parts.slice(0, 3).some((x) => !Number.isFinite(x))) return null;
    const a = parts.length > 3 ? (m[1].includes('%') && parts[3] > 1 ? parts[3] / 255 : parts[3]) : 1;
    return {r: parts[0], g: parts[1], b: parts[2], a: Number.isFinite(a) ? a : 1};
  }
  return null;
}

export const isColor = (s: unknown): s is string => typeof s === 'string' && parseColor(s) !== null;

const hex2 = (n: number) => Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, '0').toUpperCase();

/** #RRGGBB (alpha dropped). */
export function toHex(c: RGBA | string): string {
  const v = typeof c === 'string' ? parseColor(c) : c;
  if (!v) return typeof c === 'string' ? c : BLACK;
  return `#${hex2(v.r)}${hex2(v.g)}${hex2(v.b)}`;
}

/** Mix two colours in sRGB, u of the way from a to b. */
export function mix(a: string, b: string, u: number): string {
  const A = parseColor(a);
  const B = parseColor(b);
  if (!A || !B) return A ? toHex(A) : B ? toHex(B) : a;
  const k = Math.min(1, Math.max(0, u));
  return toHex({r: A.r + (B.r - A.r) * k, g: A.g + (B.g - A.g) * k, b: A.b + (B.b - A.b) * k, a: 1});
}

/** A colour at an alpha, as rgba(). */
export function alpha(c: string, a: number): string {
  const v = parseColor(c);
  if (!v) return c;
  return `rgba(${Math.round(v.r)},${Math.round(v.g)},${Math.round(v.b)},${+Math.min(1, Math.max(0, a)).toFixed(4)})`;
}

/** "r,g,b" for building rgba() shadows from a token. */
export function rgbTriplet(c: string): string {
  const v = parseColor(c) ?? {r: 0, g: 0, b: 0, a: 1};
  return `${Math.round(v.r)},${Math.round(v.g)},${Math.round(v.b)}`;
}

/** WCAG relative luminance (0 black … 1 white). */
export function luminance(c: string): number {
  const v = parseColor(c);
  if (!v) return 0;
  const ch = (x: number) => {
    const s = x / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(v.r) + 0.7152 * ch(v.g) + 0.0722 * ch(v.b);
}

/** WCAG contrast ratio between two colours (1 … 21). */
export function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** HSL saturation (0 … 1), to find a palette's most colourful token. */
export function saturation(c: string): number {
  const v = parseColor(c);
  if (!v) return 0;
  const r = v.r / 255;
  const g = v.g / 255;
  const b = v.b / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return 0;
  const d = max - min;
  return l > 0.5 ? d / (2 - max - min) : d / (max + min);
}

/** The candidate with the best contrast against a background (the first one that reaches `min` wins ties in order). */
export function bestOn(bg: string, candidates: string[], min = 4.5): string {
  const valid = candidates.filter(isColor);
  const first = valid.find((c) => contrast(c, bg) >= min);
  if (first) return first;
  return valid.reduce((best, c) => (contrast(c, bg) > contrast(best, bg) ? c : best), valid[0] ?? BLACK);
}

/** Push `fg` toward `toward` until it reaches `min` contrast on `bg` (or as far as it goes). */
export function ensureContrast(fg: string, bg: string, min: number, toward: string): string {
  if (contrast(fg, bg) >= min) return toHex(fg);
  for (let k = 0.05; k <= 1.0001; k += 0.05) {
    const c = mix(fg, toward, k);
    if (contrast(c, bg) >= min) return c;
  }
  return toHex(toward);
}
