// Geometry for a spine-hinged book. No clock or rendering state: callers animate angles with the film's own eases.
import {clamp01, lerp} from './spline';

export type BookSize = {
  w: number;
  h: number;
  /** The cover extends this far beyond each page's free edge and each end of the spine. */
  overhang?: number;
  pageThickness?: number;
  coverThickness?: number;
  pageRadius?: number;
  coverRadius?: number;
};

const positive = (n: number, name: string) => {
  if (!Number.isFinite(n) || n <= 0) throw new Error(`${name} must be finite and positive`);
  return n;
};
const nonnegative = (n: number, name: string) => {
  if (!Number.isFinite(n) || n < 0) throw new Error(`${name} must be finite and nonnegative`);
  return n;
};

/** A turn runs from 0 (right) to -180 degrees (left); overshooting animation curves stop at the paper's rests. */
export function pageTurnAngle(theta: number): number {
  if (!Number.isFinite(theta)) throw new Error('A page angle must be finite');
  return Math.max(-180, Math.min(0, theta));
}

/** The free edge leads most at mid-turn; the leaf is exactly flat at either endpoint. */
export function pageStripAngles(theta: number, strips = 10, curl = 1): number[] {
  if (!Number.isInteger(strips) || strips < 1 || strips > 100) throw new Error('A leaf needs 1 to 100 whole strips');
  nonnegative(curl, 'Page curl');
  const angle = pageTurnAngle(theta);
  const bend = Math.sin(Math.PI * clamp01(-angle / 180)) * 26 * curl;
  return Array.from({length: strips + 1}, (_, i) => Math.max(-180, angle - bend * (i / strips) ** 1.6));
}

/** Strip transforms and their physical vertices. The next strip starts at this one's free edge. */
export function pageStripGeometry(w: number, theta: number, strips = 10, curl = 1, overlap = 0.6) {
  positive(w, 'Page width');
  nonnegative(overlap, 'Strip overlap');
  const angles = pageStripAngles(theta, strips, curl);
  const sw = w / strips;
  let x = 0, z = 0, previous = 0;
  return Array.from({length: strips}, (_, i) => {
    const angle = (angles[i] + angles[i + 1]) / 2;
    const radians = angle * Math.PI / 180;
    const endX = x + sw * Math.cos(radians), endZ = z - sw * Math.sin(radians);
    const item = {i, w: sw, faceW: sw + overlap, angle, relative: angle - previous,
      x, z, endX, endZ, rectoX: -i * sw,
      // A back face mirrors about its own centre. +overlap places its content at x-w when the leaf reaches -180.
      versoX: -(strips - 1 - i) * sw + overlap,
      frontShade: angles.slice(i, i + 2).map(a => 0.3 * (1 - clamp01(Math.cos(a * Math.PI / 180)))),
      backShade: angles.slice(i, i + 2).map(a => 0.3 * (1 - clamp01(-Math.cos(a * Math.PI / 180))))};
    x = endX; z = endZ; previous = angle;
    return item;
  });
}

/** A page's projected shadow shrinks to zero at upright before appearing on the other side. */
export function pageTurnShadow(theta: number, side: 1 | -1, w: number) {
  positive(w, 'Page width');
  const angle = pageTurnAngle(theta), a = Math.abs(angle) * Math.PI / 180;
  const up = angle === 0 || angle === -180 ? 0 : Math.sin(a), lean = Math.abs(Math.cos(a));
  const strength = (side === 1 ? angle > -90 : angle < -90) ? up * clamp01(lean * 2) : 0;
  const maxWidth = w * 12 / 31;
  const width = maxWidth * (0.25 + 0.75 * up);
  const reach = w * lean;
  return {strength, opacity: 0.2 * strength, width, maxWidth, x: side === 1 ? reach : -reach - width};
}

/** Thickness-derived stacks stay correctly ordered for any number of leaves, including an empty book. */
export function bookGeometry(size: BookSize, leaves: number) {
  const w = positive(size.w, 'Page width'), h = positive(size.h, 'Page height');
  if (!Number.isInteger(leaves) || leaves < 0) throw new Error('The leaf count must be a nonnegative integer');
  const unit = positive(size.pageThickness ?? w * 0.002, 'Page thickness');
  const coverThickness = positive(size.coverThickness ?? unit * 3, 'Cover thickness');
  const overhang = nonnegative(size.overhang ?? w * 0.02, 'Cover overhang');
  const pageRadius = nonnegative(size.pageRadius ?? w * 0.013, 'Page radius');
  const coverRadius = nonnegative(size.coverRadius ?? w * 0.035, 'Cover radius');
  const base = -(leaves + 1) * unit, back = base - 4 * unit;
  // The closed board's lining must clear the right stack; its outer face is a full board thickness farther forward.
  const coverRight = coverThickness, coverLeft = back - coverThickness - unit;
  const right = (i: number) => base + (leaves - i) * unit;
  const left = (i: number) => coverLeft + coverThickness + (i + 1) * unit;
  return {w, h, cw: w + overhang, ch: h + 2 * overhang, unit, coverThickness, pageRadius, coverRadius,
    base, back, coverRight, coverLeft, right, left,
    leafZ: (i: number, theta: number) => lerp(right(i), left(i), -pageTurnAngle(theta) / 180),
    coverZ: (theta: number) => lerp(coverRight, coverLeft, -pageTurnAngle(theta) / 180)};
}
