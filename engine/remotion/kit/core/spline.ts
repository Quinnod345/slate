// Smooth paths through timed keys that never overshoot, and value chains.
//
// A Catmull-Rom through unevenly spaced keys overshoots and backtracks (a camera once drifted to −274 px between two
// marks). The monotone cubic here (Fritsch–Carlson tangents, Butland's weighted harmonic mean) never leaves the range
// of the two keys it is between, and is at rest at the first and last key. Mirrored in src/slate/motion/eases.py and
// checked against tests/vectors/eases.json ("splines").

export type SKey = readonly [number, number];
export type VKey = readonly [number, readonly number[]];

function sortedKeys<K extends readonly [number, unknown]>(keys: readonly K[]): K[] {
  const out = keys.filter((k) => Number.isFinite(k[0])).slice();
  out.sort((a, b) => a[0] - b[0]);
  // two keys at one time: the later one wins (it is the value from then on)
  const dedup: K[] = [];
  for (const k of out) {
    if (dedup.length && dedup[dedup.length - 1][0] === k[0]) dedup[dedup.length - 1] = k;
    else dedup.push(k);
  }
  return dedup;
}

/** The tangents at each key: 0 at both ends and wherever the data turns, Butland's mean elsewhere. */
function tangents(ts: number[], vs: number[]): number[] {
  const n = ts.length;
  const h: number[] = [];
  const d: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    h.push(ts[i + 1] - ts[i]);
    d.push((vs[i + 1] - vs[i]) / h[i]);
  }
  const m: number[] = new Array(n).fill(0);
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] * d[i] <= 0) continue;
    const w1 = 2 * h[i] + h[i - 1];
    const w2 = h[i] + 2 * h[i - 1];
    m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
  }
  return m;
}

/** A value through timed keys [t, v], monotone between keys, at rest at both ends. Exact at every key. */
export function monoSpline(t: number, keys: readonly SKey[]): number {
  const k = sortedKeys(keys);
  const n = k.length;
  if (!n) return 0;
  if (n === 1 || t <= k[0][0]) return k[0][1];
  if (t >= k[n - 1][0]) return k[n - 1][1];
  const ts = k.map((x) => x[0]);
  const vs = k.map((x) => x[1]);
  const m = tangents(ts, vs);
  let i = 0;
  while (i < n - 2 && t >= ts[i + 1]) i++;
  const h = ts[i + 1] - ts[i];
  const u = (t - ts[i]) / h;
  const h00 = 2 * u * u * u - 3 * u * u + 1;
  const h10 = u * u * u - 2 * u * u + u;
  const h01 = -2 * u * u * u + 3 * u * u;
  const h11 = u * u * u - u * u;
  return h00 * vs[i] + h10 * h * m[i] + h01 * vs[i + 1] + h11 * h * m[i + 1];
}

/** The same through keys of several values each (a position, a camera): each dimension monotone on its own. */
export function monoSplineVec(t: number, keys: readonly VKey[]): number[] {
  if (!keys.length) return [];
  const dims = keys[0][1].length;
  const out: number[] = [];
  for (let d = 0; d < dims; d++) out.push(monoSpline(t, keys.map(([tt, v]) => [tt, v[d] ?? 0] as const)));
  return out;
}

/** Plain linear interpolation. */
export const lerp = (a: number, b: number, u: number): number => a + (b - a) * u;

/** Element-wise lerp of two same-length vectors (the shorter length wins). */
export function lerpVec(a: readonly number[], b: readonly number[], u: number): number[] {
  const n = Math.min(a.length, b.length);
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(a[i] + (b[i] - a[i]) * u);
  return out;
}

export const clamp01 = (u: number): number => (u <= 0 ? 0 : u >= 1 ? 1 : Number.isNaN(u) ? 0 : u);

/**
 * AE-style keyframes: [time, value, ease of the segment that starts at this key]. `easeAt(name, u)` evaluates a named
 * ease (the brand's); every segment starts and ends exactly on its keys.
 */
export function keyframes(t: number, keys: readonly (readonly [number, number, string])[], easeAt: (name: string, u: number) => number): number {
  if (!keys.length) return 0;
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 0; i < keys.length - 1; i++) {
    const [t0, v0, e] = keys[i];
    const [t1, v1] = keys[i + 1];
    if (t < t1) return t1 > t0 ? v0 + (v1 - v0) * easeAt(e, (t - t0) / (t1 - t0)) : v1;
  }
  return keys[keys.length - 1][1];
}

/** A step of a chain: from wherever the value is when it starts, to `to`, over [t0, t0 + dur] on an ease. */
export type Step<V> = {t0: number; dur: number; ease?: string; to: V | ((t: number) => V)};

const resolve = <V,>(v: V | ((t: number) => V), t: number): V => (typeof v === 'function' ? (v as (t: number) => V)(t) : v);

/**
 * A number moved through steps, each from wherever the last left it (a later step can start before one ends, and blends
 * from the running value). `easeAt` gives the named ease's progress with exact endpoints.
 */
export function chain(t: number, start: number, steps: readonly Step<number>[], easeAt: (name: string, u: number) => number, fallbackEase = 'move'): number {
  let v = start;
  for (const s of steps) {
    const u = s.dur > 0 ? easeAt(s.ease ?? fallbackEase, (t - s.t0) / s.dur) : t >= s.t0 ? 1 : 0;
    if (u <= 0) continue;
    v = lerp(v, resolve(s.to, t), u);
  }
  return v;
}

/** A record of numbers moved through steps (a pose, a camera, a layout), every field on the step's ease. */
export function chainObj<V extends Record<string, number>>(
  t: number,
  start: V | ((t: number) => V),
  steps: readonly Step<Partial<V>>[],
  easeAt: (name: string, u: number) => number,
  fallbackEase = 'move',
): V {
  let v: V = {...resolve(start, t)};
  for (const s of steps) {
    const u = s.dur > 0 ? easeAt(s.ease ?? fallbackEase, (t - s.t0) / s.dur) : t >= s.t0 ? 1 : 0;
    if (u <= 0) continue;
    const to = resolve(s.to, t);
    const next = {...v} as Record<string, number>;
    for (const key of Object.keys(v)) {
      const target = (to as Record<string, number | undefined>)[key];
      if (target !== undefined) next[key] = lerp(v[key], target, u);
    }
    v = next as V;
  }
  return v;
}
