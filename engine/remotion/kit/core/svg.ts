// Brand SVG → geometry the logo build can use: every shape as one absolute path (M, L, C, Z only), with its fill, its
// exact bounding box and its corner vertices. Pure and dependency-free, so it runs in the page, in Node and in tests.
//
// Handles path, rect, circle, ellipse, line, polyline and polygon; transforms on shapes and groups (matrix, translate,
// scale, rotate, skewX, skewY); fills from attributes, inline styles, parent groups and simple class rules in a <style>
// block. Arcs become cubic beziers, so a transform never distorts them. Anything it can't read is listed in `problems`.

export type Pt = {x: number; y: number};
export type Box = {x0: number; y0: number; x1: number; y1: number};
export type Seg = {kind: 'M' | 'L' | 'C' | 'Z'; to: Pt; c1?: Pt; c2?: Pt};

export type SvgShape = {
  index: number;
  tag: string;
  id: string | null;
  cls: string | null;
  fill: string | null;
  stroke: string | null;
  /** the shape as an absolute path in the SVG's user space (transforms applied) */
  d: string;
  segs: Seg[];
  bbox: Box;
  /** every on-curve point */
  vertices: Pt[];
  /** the vertices where the outline turns sharply (the marks a construction drawing puts on) */
  corners: Pt[];
};

export type ParsedSvg = {viewBox: [number, number, number, number]; shapes: SvgShape[]; problems: string[]};

type Mat = [number, number, number, number, number, number]; // a b c d e f
const I: Mat = [1, 0, 0, 1, 0, 0];

const mul = (m: Mat, n: Mat): Mat => [
  m[0] * n[0] + m[2] * n[1],
  m[1] * n[0] + m[3] * n[1],
  m[0] * n[2] + m[2] * n[3],
  m[1] * n[2] + m[3] * n[3],
  m[0] * n[4] + m[2] * n[5] + m[4],
  m[1] * n[4] + m[3] * n[5] + m[5],
];
const apply = (m: Mat, p: Pt): Pt => ({x: m[0] * p.x + m[2] * p.y + m[4], y: m[1] * p.x + m[3] * p.y + m[5]});

const NUM = /[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g;
const nums = (s: string): number[] => (s.match(NUM) ?? []).map(Number);

/** Parse an SVG transform attribute into one matrix. */
export function parseTransform(s: string | null | undefined): Mat {
  if (!s) return I;
  let m: Mat = I;
  const re = /(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)/g;
  let hit: RegExpExecArray | null;
  while ((hit = re.exec(s))) {
    const v = nums(hit[2]);
    let t: Mat = I;
    switch (hit[1]) {
      case 'matrix':
        if (v.length === 6) t = v as Mat;
        break;
      case 'translate':
        t = [1, 0, 0, 1, v[0] ?? 0, v[1] ?? 0];
        break;
      case 'scale':
        t = [v[0] ?? 1, 0, 0, v[1] ?? v[0] ?? 1, 0, 0];
        break;
      case 'rotate': {
        const a = ((v[0] ?? 0) * Math.PI) / 180;
        const r: Mat = [Math.cos(a), Math.sin(a), -Math.sin(a), Math.cos(a), 0, 0];
        t = v.length >= 3 ? mul(mul([1, 0, 0, 1, v[1], v[2]], r), [1, 0, 0, 1, -v[1], -v[2]]) : r;
        break;
      }
      case 'skewX':
        t = [1, 0, Math.tan(((v[0] ?? 0) * Math.PI) / 180), 1, 0, 0];
        break;
      case 'skewY':
        t = [1, Math.tan(((v[0] ?? 0) * Math.PI) / 180), 0, 1, 0, 0];
        break;
    }
    m = mul(m, t);
  }
  return m;
}

// ------------------------------------------------------------------------------------------------------ path data
type Tok = {cmd: string} | {num: number; raw: string};

function tokenize(d: string): Tok[] {
  const out: Tok[] = [];
  const re = /([MmZzLlHhVvCcSsQqTtAa])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(d))) out.push(m[1] ? {cmd: m[1]} : {num: Number(m[2]), raw: m[2]});
  return out;
}

/** Arc (endpoint form) to cubic segments: the standard conversion through the centre form. */
function arcToCubics(p0: Pt, rx: number, ry: number, phiDeg: number, large: boolean, sweep: boolean, p1: Pt): Seg[] {
  if (rx === 0 || ry === 0 || (p0.x === p1.x && p0.y === p1.y)) return [{kind: 'L', to: p1}];
  rx = Math.abs(rx);
  ry = Math.abs(ry);
  const phi = (phiDeg * Math.PI) / 180;
  const cos = Math.cos(phi);
  const sin = Math.sin(phi);
  const dx = (p0.x - p1.x) / 2;
  const dy = (p0.y - p1.y) / 2;
  const x1p = cos * dx + sin * dy;
  const y1p = -sin * dx + cos * dy;
  const lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
  if (lambda > 1) {
    rx *= Math.sqrt(lambda);
    ry *= Math.sqrt(lambda);
  }
  const num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
  const den = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
  let co = Math.sqrt(Math.max(0, num / den));
  if (large === sweep) co = -co;
  const cxp = (co * rx * y1p) / ry;
  const cyp = (-co * ry * x1p) / rx;
  const cx = cos * cxp - sin * cyp + (p0.x + p1.x) / 2;
  const cy = sin * cxp + cos * cyp + (p0.y + p1.y) / 2;
  const ang = (ux: number, uy: number, vx: number, vy: number) => {
    const a = Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
    return a;
  };
  const th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
  let dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry);
  if (!sweep && dth > 0) dth -= 2 * Math.PI;
  if (sweep && dth < 0) dth += 2 * Math.PI;
  const n = Math.max(1, Math.ceil(Math.abs(dth) / (Math.PI / 2) - 1e-9));
  const step = dth / n;
  const k = (4 / 3) * Math.tan(step / 4);
  const segs: Seg[] = [];
  const pt = (t: number): Pt => ({x: cx + rx * Math.cos(t) * cos - ry * Math.sin(t) * sin, y: cy + rx * Math.cos(t) * sin + ry * Math.sin(t) * cos});
  const dpt = (t: number): Pt => ({x: -rx * Math.sin(t) * cos - ry * Math.cos(t) * sin, y: -rx * Math.sin(t) * sin + ry * Math.cos(t) * cos});
  for (let i = 0; i < n; i++) {
    const a = th1 + i * step;
    const b = a + step;
    const pa = pt(a);
    const pb = i === n - 1 ? p1 : pt(b);
    const da = dpt(a);
    const db = dpt(b);
    segs.push({kind: 'C', c1: {x: pa.x + k * da.x, y: pa.y + k * da.y}, c2: {x: pb.x - k * db.x, y: pb.y - k * db.y}, to: pb});
  }
  return segs;
}

/** Parse path data into absolute M/L/C/Z segments. */
export function parsePath(d: string): Seg[] {
  const toks = tokenize(d);
  const segs: Seg[] = [];
  let i = 0;
  let cur: Pt = {x: 0, y: 0};
  let start: Pt = {x: 0, y: 0};
  let lastCmd = '';
  let lastC2: Pt | null = null; // for S
  let lastQ: Pt | null = null; // for T
  const next = (): number => {
    const t = toks[i];
    if (!t || 'cmd' in t) throw new Error('number expected');
    i++;
    return t.num;
  };
  const hasNum = () => i < toks.length && 'num' in toks[i];
  const flag = (): boolean => {
    // arc flags may be run together with what follows ("a1 1 0 01 1 1", "a1 1 0 11.5 2"): take one digit
    const t = toks[i];
    if (!t || 'cmd' in t) throw new Error('flag expected');
    const s = t.raw.replace(/^\+/, '');
    if (s.length > 1 && (s[0] === '0' || s[0] === '1')) {
      const restRaw = s.slice(1);
      toks[i] = {num: Number(restRaw), raw: restRaw};
      return s[0] === '1';
    }
    i++;
    return t.num !== 0;
  };
  try {
    while (i < toks.length) {
      const t = toks[i];
      let cmd: string;
      if ('cmd' in t) {
        cmd = t.cmd;
        i++;
      } else {
        if (!lastCmd || lastCmd === 'Z' || lastCmd === 'z') throw new Error('path data starts with a number');
        cmd = lastCmd === 'M' ? 'L' : lastCmd === 'm' ? 'l' : lastCmd;
      }
      const rel = cmd === cmd.toLowerCase() && cmd !== 'z';
      const C = cmd.toUpperCase();
      const P = (x: number, y: number): Pt => (rel ? {x: cur.x + x, y: cur.y + y} : {x, y});
      if (C === 'Z') {
        segs.push({kind: 'Z', to: {...start}});
        cur = {...start};
        lastC2 = lastQ = null;
        lastCmd = cmd;
        continue;
      }
      do {
        if (C === 'M') {
          const p = P(next(), next());
          segs.push({kind: 'M', to: p});
          cur = start = p;
          lastC2 = lastQ = null;
          lastCmd = cmd;
          if (hasNum()) {
            cmd = rel ? 'l' : 'L';
            lastCmd = cmd;
            // the rest of the numbers after a moveto are implicit linetos
            while (hasNum()) {
              const q = rel ? {x: cur.x + next(), y: cur.y + next()} : {x: next(), y: next()};
              segs.push({kind: 'L', to: q});
              cur = q;
            }
          }
          break;
        }
        if (C === 'L') {
          const p = P(next(), next());
          segs.push({kind: 'L', to: p});
          cur = p;
          lastC2 = lastQ = null;
        } else if (C === 'H') {
          const x = next();
          const p = {x: rel ? cur.x + x : x, y: cur.y};
          segs.push({kind: 'L', to: p});
          cur = p;
          lastC2 = lastQ = null;
        } else if (C === 'V') {
          const y = next();
          const p = {x: cur.x, y: rel ? cur.y + y : y};
          segs.push({kind: 'L', to: p});
          cur = p;
          lastC2 = lastQ = null;
        } else if (C === 'C') {
          const c1 = P(next(), next());
          const c2 = P(next(), next());
          const p = P(next(), next());
          segs.push({kind: 'C', c1, c2, to: p});
          cur = p;
          lastC2 = c2;
          lastQ = null;
        } else if (C === 'S') {
          const c1 = lastC2 ? {x: 2 * cur.x - lastC2.x, y: 2 * cur.y - lastC2.y} : {...cur};
          const c2 = P(next(), next());
          const p = P(next(), next());
          segs.push({kind: 'C', c1, c2, to: p});
          cur = p;
          lastC2 = c2;
          lastQ = null;
        } else if (C === 'Q' || C === 'T') {
          const q: Pt = C === 'Q' ? P(next(), next()) : lastQ ? {x: 2 * cur.x - lastQ.x, y: 2 * cur.y - lastQ.y} : {...cur};
          const p = P(next(), next());
          // a quadratic as the cubic it equals
          segs.push({kind: 'C', c1: {x: cur.x + (2 / 3) * (q.x - cur.x), y: cur.y + (2 / 3) * (q.y - cur.y)}, c2: {x: p.x + (2 / 3) * (q.x - p.x), y: p.y + (2 / 3) * (q.y - p.y)}, to: p});
          cur = p;
          lastQ = q;
          lastC2 = null;
        } else if (C === 'A') {
          const rx = next();
          const ry = next();
          const rot = next();
          const large = flag();
          const sweep = flag();
          const p = P(next(), next());
          segs.push(...arcToCubics(cur, rx, ry, rot, large, sweep, p));
          cur = p;
          lastC2 = lastQ = null;
        } else throw new Error(`unknown command ${cmd}`);
        lastCmd = cmd;
      } while (hasNum());
    }
  } catch {
    // keep what parsed; the caller notes the problem if nothing did
  }
  return segs;
}

/** Transform segments by a matrix. */
export function transformSegs(segs: Seg[], m: Mat): Seg[] {
  if (m === I) return segs;
  return segs.map((s) => ({kind: s.kind, to: apply(m, s.to), ...(s.c1 ? {c1: apply(m, s.c1)} : {}), ...(s.c2 ? {c2: apply(m, s.c2)} : {})}));
}

const f = (n: number) => +n.toFixed(4);

/** Segments back to a path string. */
export function segsToD(segs: Seg[]): string {
  return segs
    .map((s) => (s.kind === 'Z' ? 'Z' : s.kind === 'C' ? `C${f(s.c1!.x)},${f(s.c1!.y)} ${f(s.c2!.x)},${f(s.c2!.y)} ${f(s.to.x)},${f(s.to.y)}` : `${s.kind}${f(s.to.x)},${f(s.to.y)}`))
    .join('');
}

// ---------------------------------------------------------------------------------------------------------- geometry
function cubicExtrema(p0: number, p1: number, p2: number, p3: number): number[] {
  // derivative: 3[(p1−p0)(1−t)² + 2(p2−p1)(1−t)t + (p3−p2)t²] = at² + bt + c
  const a = -p0 + 3 * p1 - 3 * p2 + p3;
  const b = 2 * (p0 - 2 * p1 + p2);
  const c = p1 - p0;
  const ts: number[] = [];
  if (Math.abs(a) < 1e-12) {
    if (Math.abs(b) > 1e-12) ts.push(-c / b);
  } else {
    const disc = b * b - 4 * a * c;
    if (disc >= 0) {
      const r = Math.sqrt(disc);
      ts.push((-b + r) / (2 * a), (-b - r) / (2 * a));
    }
  }
  return ts.filter((t) => t > 0 && t < 1);
}

const cubicAt = (p0: number, p1: number, p2: number, p3: number, t: number) => {
  const r = 1 - t;
  return r * r * r * p0 + 3 * r * r * t * p1 + 3 * r * t * t * p2 + t * t * t * p3;
};

/** The exact bounding box of M/L/C/Z segments. */
export function segsBBox(segs: Seg[]): Box {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  const add = (p: Pt) => {
    x0 = Math.min(x0, p.x);
    y0 = Math.min(y0, p.y);
    x1 = Math.max(x1, p.x);
    y1 = Math.max(y1, p.y);
  };
  let cur: Pt = {x: 0, y: 0};
  for (const s of segs) {
    if (s.kind === 'C') {
      add(s.to);
      add(cur);
      for (const t of cubicExtrema(cur.x, s.c1!.x, s.c2!.x, s.to.x)) add({x: cubicAt(cur.x, s.c1!.x, s.c2!.x, s.to.x, t), y: cubicAt(cur.y, s.c1!.y, s.c2!.y, s.to.y, t)});
      for (const t of cubicExtrema(cur.y, s.c1!.y, s.c2!.y, s.to.y)) add({x: cubicAt(cur.x, s.c1!.x, s.c2!.x, s.to.x, t), y: cubicAt(cur.y, s.c1!.y, s.c2!.y, s.to.y, t)});
    } else add(s.to);
    cur = s.to;
  }
  if (!Number.isFinite(x0)) return {x0: 0, y0: 0, x1: 0, y1: 0};
  return {x0, y0, x1, y1};
}

function dir(a: Pt, b: Pt): Pt | null {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const l = Math.hypot(dx, dy);
  return l > 1e-9 ? {x: dx / l, y: dy / l} : null;
}

/** On-curve vertices, and the corners among them (where the outline turns by more than `minTurnDeg`). */
export function segsVertices(segs: Seg[], minTurnDeg = 25): {vertices: Pt[]; corners: Pt[]} {
  const vertices: Pt[] = [];
  const corners: Pt[] = [];
  // split into subpaths
  const subs: Seg[][] = [];
  for (const s of segs) {
    if (s.kind === 'M' || !subs.length) subs.push([]);
    subs[subs.length - 1].push(s);
  }
  const cosLimit = Math.cos((minTurnDeg * Math.PI) / 180);
  for (const sub of subs) {
    if (!sub.length) continue;
    const pts: Pt[] = [];
    const inDir: (Pt | null)[] = [];
    const outDir: (Pt | null)[] = [];
    let cur = sub[0].to;
    pts.push(cur);
    inDir.push(null);
    for (let k = 1; k < sub.length; k++) {
      const s = sub[k];
      const to = s.to;
      let outD: Pt | null;
      let inD: Pt | null;
      if (s.kind === 'C') {
        outD = dir(cur, s.c1!) ?? dir(cur, s.c2!) ?? dir(cur, to);
        inD = dir(s.c2!, to) ?? dir(s.c1!, to) ?? dir(cur, to);
      } else {
        outD = dir(cur, to);
        inD = outD;
      }
      outDir[pts.length - 1] = outD;
      if (s.kind === 'Z' && Math.hypot(to.x - cur.x, to.y - cur.y) < 1e-9) {
        cur = to;
        continue;
      }
      pts.push(to);
      inDir.push(inD);
      cur = to;
    }
    const closed = sub[sub.length - 1].kind === 'Z';
    // a closed subpath's last point is its first: fold them
    if (closed && pts.length > 1) {
      const last = pts[pts.length - 1];
      if (Math.hypot(last.x - pts[0].x, last.y - pts[0].y) < 1e-9) {
        inDir[0] = inDir[pts.length - 1];
        pts.pop();
        inDir.pop();
      }
    }
    for (let k = 0; k < pts.length; k++) {
      vertices.push(pts[k]);
      const a = inDir[k];
      const b = outDir[k] ?? null;
      if (!a || !b) corners.push(pts[k]);
      else if (a.x * b.x + a.y * b.y < cosLimit) corners.push(pts[k]);
    }
  }
  return {vertices, corners};
}

// ----------------------------------------------------------------------------------------------------------- shapes
function attrs(s: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) out[m[1]] = m[3] ?? m[4] ?? '';
  return out;
}

function styleProp(style: string | undefined, prop: string): string | undefined {
  if (!style) return undefined;
  const m = style.match(new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`));
  return m ? m[1].trim() : undefined;
}

function shapeD(tag: string, a: Record<string, string>): string | null {
  const n = (k: string, d = 0) => (a[k] !== undefined ? Number(a[k]) : d);
  switch (tag) {
    case 'path':
      return a.d ?? null;
    case 'rect': {
      const x = n('x');
      const y = n('y');
      const w = n('width');
      const h = n('height');
      let rx = a.rx !== undefined ? n('rx') : a.ry !== undefined ? n('ry') : 0;
      let ry = a.ry !== undefined ? n('ry') : rx;
      rx = Math.min(rx, w / 2);
      ry = Math.min(ry, h / 2);
      if (!(w > 0 && h > 0)) return null;
      if (!rx && !ry) return `M${x},${y}H${x + w}V${y + h}H${x}Z`;
      return `M${x + rx},${y}H${x + w - rx}A${rx},${ry} 0 0 1 ${x + w},${y + ry}V${y + h - ry}A${rx},${ry} 0 0 1 ${x + w - rx},${y + h}H${x + rx}A${rx},${ry} 0 0 1 ${x},${y + h - ry}V${y + ry}A${rx},${ry} 0 0 1 ${x + rx},${y}Z`;
    }
    case 'circle':
    case 'ellipse': {
      const cx = n('cx');
      const cy = n('cy');
      const rx = tag === 'circle' ? n('r') : n('rx');
      const ry = tag === 'circle' ? n('r') : n('ry');
      if (!(rx > 0 && ry > 0)) return null;
      return `M${cx - rx},${cy}A${rx},${ry} 0 1 0 ${cx + rx},${cy}A${rx},${ry} 0 1 0 ${cx - rx},${cy}Z`;
    }
    case 'line':
      return `M${n('x1')},${n('y1')}L${n('x2')},${n('y2')}`;
    case 'polyline':
    case 'polygon': {
      const v = nums(a.points ?? '');
      if (v.length < 4) return null;
      let d = `M${v[0]},${v[1]}`;
      for (let k = 2; k + 1 < v.length; k += 2) d += `L${v[k]},${v[k + 1]}`;
      return tag === 'polygon' ? d + 'Z' : d;
    }
  }
  return null;
}

const SHAPES = new Set(['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon']);

/** Parse an SVG document's shapes (in paint order). */
export function parseSvg(svg: string): ParsedSvg {
  const problems: string[] = [];
  const shapes: SvgShape[] = [];
  const text = String(svg ?? '');
  // class → fill from simple <style> rules
  const classFill = new Map<string, string>();
  for (const block of text.match(/<style[^>]*>([\s\S]*?)<\/style>/gi) ?? []) {
    const re = /\.([\w-]+)\s*\{([^}]*)\}/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(block))) {
      const fill = styleProp(m[2], 'fill');
      if (fill) classFill.set(m[1], fill);
    }
  }
  const root = text.match(/<svg\b([^>]*)>/i);
  const ra = root ? attrs(root[1]) : {};
  let viewBox: [number, number, number, number] = [0, 0, 0, 0];
  const vb = nums(ra.viewBox ?? ra.viewbox ?? '');
  if (vb.length === 4) viewBox = vb as [number, number, number, number];
  else if (ra.width && ra.height) viewBox = [0, 0, parseFloat(ra.width), parseFloat(ra.height)];
  else problems.push('the SVG has no viewBox (or width and height)');

  type Frame = {m: Mat; fill: string | null; stroke: string | null};
  const stack: Frame[] = [{m: I, fill: null, stroke: null}];
  const tagRe = /<(\/?)([\w:-]+)([^>]*?)(\/?)>/g;
  let t: RegExpExecArray | null;
  let inDefs = 0;
  // skip comments, CDATA and style bodies
  const body = text.replace(/<!--[\s\S]*?-->/g, '').replace(/<style[\s\S]*?<\/style>/gi, '');
  while ((t = tagRe.exec(body))) {
    const [, close, rawTag, rest, selfClose] = t;
    const tag = rawTag.toLowerCase();
    if (tag === 'defs' || tag === 'clippath' || tag === 'mask' || tag === 'symbol' || tag === 'pattern' || tag === 'lineargradient' || tag === 'radialgradient') {
      if (close) inDefs = Math.max(0, inDefs - 1);
      else if (!selfClose) inDefs++;
      continue;
    }
    if (inDefs) continue;
    if (tag === 'g' || tag === 'svg' || tag === 'a') {
      if (close) {
        if (stack.length > 1) stack.pop();
        continue;
      }
      const a = attrs(rest);
      const top = stack[stack.length - 1];
      const fr: Frame = {
        m: tag === 'svg' ? top.m : mul(top.m, parseTransform(a.transform)),
        fill: a.fill ?? styleProp(a.style, 'fill') ?? (a.class ? classFill.get(a.class.split(/\s+/)[0]) : undefined) ?? top.fill,
        stroke: a.stroke ?? styleProp(a.style, 'stroke') ?? top.stroke,
      };
      if (!selfClose) stack.push(fr);
      continue;
    }
    if (close || !SHAPES.has(tag)) {
      if (!close && (tag === 'text' || tag === 'use' || tag === 'image')) problems.push(`<${tag}> isn't read (outline text and inline <use> in the brand SVG first)`);
      continue;
    }
    const a = attrs(rest);
    const d = shapeD(tag, a);
    if (!d) {
      problems.push(`a <${tag}> has no geometry`);
      continue;
    }
    const top = stack[stack.length - 1];
    const m = mul(top.m, parseTransform(a.transform));
    const segs = transformSegs(parsePath(d), m);
    if (!segs.length) {
      problems.push(`a <${tag}>'s path data couldn't be read`);
      continue;
    }
    const cls = a.class ? a.class.split(/\s+/)[0] : null;
    const fillRaw = a.fill ?? styleProp(a.style, 'fill') ?? (cls ? classFill.get(cls) : undefined) ?? top.fill ?? (tag === 'line' || tag === 'polyline' ? 'none' : '#000000');
    const {vertices, corners} = segsVertices(segs);
    shapes.push({
      index: shapes.length,
      tag,
      id: a.id ?? null,
      cls,
      fill: fillRaw === 'none' ? null : fillRaw,
      stroke: a.stroke ?? styleProp(a.style, 'stroke') ?? top.stroke,
      d: segsToD(segs),
      segs,
      bbox: segsBBox(segs),
      vertices,
      corners,
    });
  }
  if (!shapes.length) problems.push('the SVG has no shapes the logo build can draw');
  return {viewBox, shapes, problems};
}

/** The union of shape boxes. */
export function unionBox(boxes: Box[]): Box {
  if (!boxes.length) return {x0: 0, y0: 0, x1: 0, y1: 0};
  return boxes.reduce((u, b) => ({x0: Math.min(u.x0, b.x0), y0: Math.min(u.y0, b.y0), x1: Math.max(u.x1, b.x1), y1: Math.max(u.y1, b.y1)}));
}
