// Fonts by reference, and the gate that fails on substitution.
//
// A brand pack names its families and where each comes from: a font installed on this Mac, Google Fonts (through
// @remotion/google-fonts), a CSS kit URL (a type service's stylesheet), or a font file URL. Font files never live in
// git. When a render starts, the kit loads every declared face and then proves each one really rendered: headless
// Chrome falls back silently when it can't see a face (it couldn't see the films' Adobe Fonts copy), and
// document.fonts.check() says "true" for any family it has never heard of. So the gate measures: the probe text in
// "Family, <generic>" against the generic alone, for three generics. A face that is really there changes the width
// against at least two of them; a missing one changes none. Any failure fails the render, loudly, with a report the
// engine turns into the `fonts` gate result.
//
// This file is the pure part (plan, check, report); fonts.tsx runs it in the page.
import {brandPack} from './brand';
import type {BrandPack, FamilyInput, FontSourceInput} from './types';
import {KIT_VERSION} from './version';

export type FontSource =
  | {kind: 'installed'; name: string}
  | {kind: 'google'; family: string}
  | {kind: 'url'; url: string}
  | {kind: 'file'; url: string; format?: string};

export type FaceDecl = {
  role: string;
  family: string;
  weights: number[];
  styles: ('normal' | 'italic')[];
  source: FontSource;
  licence: string | null;
  fallback: 'sans-serif' | 'serif' | 'monospace';
};

export type FontPlan = {faces: FaceDecl[]; problems: string[]};

const FONT_FILE = /\.(woff2?|otf|ttf)(\?.*)?$/i;

/** Read a family's source from a brand pack, forgivingly. */
export function parseSource(raw: FontSourceInput | undefined, family: string): FontSource | null {
  if (raw == null) return null;
  if (typeof raw === 'string') {
    const s = raw.trim();
    const k = s.toLowerCase();
    if (k === 'installed' || k === 'local' || k === 'system') return {kind: 'installed', name: family};
    if (k === 'google' || k === 'google-fonts' || k === 'googlefonts' || k === '@remotion/google-fonts') return {kind: 'google', family};
    if (/^(https?:)?\/\//i.test(s) || s.startsWith('/') || s.startsWith('file:')) return FONT_FILE.test(s) ? {kind: 'file', url: s} : {kind: 'url', url: s};
    return null;
  }
  const o = raw as Record<string, unknown>;
  const kind = String(o.kind ?? o.type ?? '').toLowerCase();
  const url = String(o.url ?? o.href ?? '');
  if (kind === 'installed' || kind === 'local' || kind === 'system') return {kind: 'installed', name: String(o.name ?? family)};
  if (kind === 'google' || kind === 'google-fonts' || kind === 'googlefonts') return {kind: 'google', family: String(o.family ?? o.name ?? family)};
  if (kind === 'file') return url ? {kind: 'file', url, format: o.format ? String(o.format) : undefined} : null;
  if (kind === 'url' || kind === 'kit' || kind === 'css' || kind === 'stylesheet') {
    if (!url) return null;
    return FONT_FILE.test(url) ? {kind: 'file', url} : {kind: 'url', url};
  }
  if (url) return parseSource(url, family);
  return null;
}

function families(pack: BrandPack): FamilyInput[] {
  const f = pack.type?.families;
  if (!f) return [];
  if (Array.isArray(f)) return f;
  return Object.entries(f).map(([role, v]) => ({role, ...(v as FamilyInput)}));
}

/** Every face a brand declares, with where it comes from. */
export function fontPlan(pack?: BrandPack): FontPlan {
  const p = pack ?? brandPack();
  const problems: string[] = [];
  const faces: FaceDecl[] = [];
  const seen = new Set<string>();
  for (const f of families(p)) {
    const family = String(f.family ?? f.name ?? '').trim();
    const role = String(f.role ?? f.id ?? family).toLowerCase();
    if (!family) {
      problems.push(`type family "${role}" has no family name`);
      continue;
    }
    if (seen.has(family.toLowerCase())) continue; // one family declared for two roles loads once
    seen.add(family.toLowerCase());
    const source = parseSource(f.source, family);
    if (!source) {
      problems.push(`${family}: no usable source (installed, google, a CSS kit URL or a font file URL); it can't be loaded or checked`);
      continue;
    }
    const weights = (f.weights ?? [400]).map((w) => Number(w)).filter((w) => Number.isFinite(w) && w > 0);
    const styles = (f.styles ?? ['normal']).map((s) => (String(s).toLowerCase() === 'italic' ? 'italic' : 'normal')) as ('normal' | 'italic')[];
    const licence = (f.licence ?? f.license ?? null) as string | null;
    if (!licence) problems.push(`${family}: no licence noted (brand packs record the licence of every family)`);
    const fb = String(f.fallback ?? (role === 'mono' ? 'monospace' : role === 'serif' ? 'serif' : 'sans-serif'));
    faces.push({
      role,
      family,
      weights: weights.length ? [...new Set(weights)].sort((a, b) => a - b) : [400],
      styles: styles.length ? [...new Set(styles)] : ['normal'],
      source,
      licence,
      fallback: fb === 'serif' || fb === 'monospace' ? fb : 'sans-serif',
    });
  }
  return {faces, problems};
}

// --------------------------------------------------------------------------------------------------------- checking
/** Text with wide, narrow and numeric glyphs, so two different faces almost never measure the same. */
export const PROBE_TEXT = 'Hamburgefonstiv WMmwil 0123456789 &?!';
export const GENERICS = ['monospace', 'serif', 'sans-serif'] as const;
export const PROBE_SIZE = 72;

export type Measure = (font: string, text: string) => number;

export type FaceCheck = {
  role: string;
  family: string;
  weight: number;
  style: 'normal' | 'italic';
  ok: boolean;
  /** true when the weight looks synthesised (same advance widths as the regular); a warning, not a failure */
  synthetic: boolean;
  reason: string;
  /** per generic: [width with the family first, width of the generic alone] */
  widths: Record<string, [number, number]>;
};

const q = (family: string) => `"${family.replace(/"/g, '')}"`;

/** The width test for one face. Fails when the family changes nothing against every generic: it wasn't there. */
export function checkFace(face: Pick<FaceDecl, 'role' | 'family' | 'fallback'>, weight: number, style: 'normal' | 'italic', measure: Measure): FaceCheck {
  const widths: Record<string, [number, number]> = {};
  let differs = 0;
  for (const g of GENERICS) {
    const withFamily = measure(`${style} ${weight} ${PROBE_SIZE}px ${q(face.family)}, ${g}`, PROBE_TEXT);
    const generic = measure(`${style} ${weight} ${PROBE_SIZE}px ${g}`, PROBE_TEXT);
    widths[g] = [withFamily, generic];
    if (Math.abs(withFamily - generic) > 0.05) differs++;
  }
  const ok = differs > 0;
  let synthetic = false;
  if (ok && weight !== 400 && face.fallback !== 'monospace') {
    const regular = measure(`${style} 400 ${PROBE_SIZE}px ${q(face.family)}, ${face.fallback}`, PROBE_TEXT);
    const w = measure(`${style} ${weight} ${PROBE_SIZE}px ${q(face.family)}, ${face.fallback}`, PROBE_TEXT);
    synthetic = Math.abs(regular - w) <= 0.05;
  }
  const reason = ok
    ? synthetic
      ? `${face.family} ${weight} measures exactly like its 400: the browser may be faking this weight`
      : `${face.family} ${weight}${style === 'italic' ? ' italic' : ''} rendered`
    : `${face.family} ${weight}${style === 'italic' ? ' italic' : ''} did not render: every probe measured the same as the generic fallback, so the browser substituted another face`;
  return {role: face.role, family: face.family, weight, style, ok, synthetic, reason, widths};
}

/** Check every face of a plan. */
export function checkPlan(plan: FontPlan, measure: Measure): FaceCheck[] {
  const out: FaceCheck[] = [];
  for (const face of plan.faces) for (const style of face.styles) for (const w of face.weights) out.push(checkFace(face, w, style, measure));
  return out;
}

// ------------------------------------------------------------------------------------------------------------ report
export const GATE_MARKER = 'SLATE_FONT_GATE';

export type FontGateReport = {
  gate: 'fonts';
  status: 'PASS' | 'FAIL';
  summary: string;
  faces: FaceCheck[];
  failures: string[];
  warnings: string[];
  engine: {name: string; version: string};
};

/** The gate's verdict: PASS only when every declared face loaded and measured as itself. */
export function gateReport(checks: FaceCheck[], loadErrors: string[] = [], planProblems: string[] = []): FontGateReport {
  const failures = [...loadErrors, ...checks.filter((c) => !c.ok).map((c) => c.reason)];
  const warnings = [...planProblems, ...checks.filter((c) => c.ok && c.synthetic).map((c) => c.reason)];
  const families = [...new Set(checks.map((c) => c.family))];
  const status = failures.length ? 'FAIL' : 'PASS';
  const summary = failures.length
    ? `${failures.length} face${failures.length > 1 ? 's' : ''} didn't render as declared: ${failures[0]}`
    : checks.length
      ? `${checks.length} face${checks.length > 1 ? 's' : ''} of ${families.join(', ')} rendered as declared`
      : 'the brand declares no faces to check';
  return {gate: 'fonts', status, summary, faces: checks, failures, warnings, engine: {name: 'slate kit font gate (document.fonts + measured width)', version: KIT_VERSION}};
}

/** One log line the engine can find in the browser's console. */
export const reportLine = (r: FontGateReport): string => `${GATE_MARKER} ${JSON.stringify(r)}`;

/** Find the font gate's report in a log line (or a thrown error's message). */
export function parseReportLine(text: string): FontGateReport | null {
  const i = text.indexOf(GATE_MARKER);
  if (i < 0) return null;
  const rest = text.slice(i + GATE_MARKER.length).trim();
  const start = rest.indexOf('{');
  if (start < 0) return null;
  // the JSON runs to its matching brace
  let depth = 0;
  let inStr = false;
  for (let j = start; j < rest.length; j++) {
    const ch = rest[j];
    if (inStr) {
      if (ch === '\\') j++;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) {
      try {
        return JSON.parse(rest.slice(start, j + 1)) as FontGateReport;
      } catch {
        return null;
      }
    }
  }
  return null;
}

/** The report as a Slate gate result (docs/ARCHITECTURE.md §6), for the engine to hand back to Python. */
export function toGateResult(r: FontGateReport, checkedAt = new Date().toISOString()) {
  return {
    gate: 'fonts',
    status: r.status,
    summary: r.summary,
    evidence: r.faces.map((f) => `${f.family} ${f.weight}${f.style === 'italic' ? 'i' : ''}: ${f.ok ? (f.synthetic ? 'rendered (weight may be synthetic)' : 'rendered') : 'SUBSTITUTED'}`),
    engine: r.engine,
    inputs: {},
    checked_at: checkedAt,
    details: {failures: r.failures, warnings: r.warnings, faces: r.faces},
  };
}
