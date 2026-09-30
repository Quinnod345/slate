// The brand-token adapter: a brand pack's palette and type become typed tokens (and CSS variables) that every kit
// component draws with. The kit holds no brand colours or fonts of its own: roles come from the pack (palette.roles,
// or tokens named like the role), and anything a pack leaves out is derived from what it does give, by mixing and by
// contrast, and listed in `problems` so onboarding can fill it in.
import {brandPack, memoOnBrand} from './brand';
import {BLACK, WHITE, bestOn, contrast, ensureContrast, isColor, luminance, mix, rgbTriplet, saturation, toHex} from './color';
import type {BrandPack, BrandType, FamilyInput, PaletteTree} from './types';

export type ToneName = 'ok' | 'warn' | 'risk' | 'neutral';
export type Tone = {bg: string; ink: string; dot: string};

export type ColorTokens = {
  page: string;
  surface: string;
  ink: string;
  ink2: string;
  ink3: string;
  hairline: string;
  accent: string;
  accentInk: string;
  onAccent: string;
  softAccent: string;
  inverse: string;
  inverseDeep: string;
  onInverse: string;
  /** the one accent line a film may carry */
  line: string;
  /** "r,g,b" of the ink, for rgba() shadows */
  shadow: string;
  gray: string[];
  tones: Record<ToneName, Tone>;
  /** every token the pack defines, flattened ("gray.50") */
  all: Record<string, string>;
};

export type FontFamilyToken = {role: string; family: string; css: string; weights: number[]; fallback: 'sans-serif' | 'serif' | 'monospace'; declared: boolean};

export type TypeStyle = {
  role: string;
  /** family role: sans, serif, mono, display, or one the brand names */
  font: string;
  weight: number;
  /** size as a fraction of the frame's short side */
  size: number;
  lineHeight: number;
  /** letter spacing in em */
  tracking: number;
  transform: 'none' | 'uppercase';
  tabular: boolean;
};

export type Tokens = {
  color: ColorTokens;
  fonts: Record<string, FontFamilyToken>;
  type: Record<string, TypeStyle>;
  /** corner radii as fractions of the short side */
  radius: {card: number; control: number; pill: number};
  problems: string[];
};

// ------------------------------------------------------------------------------------------------------- palette
const RESERVED = new Set(['roles', 'tones', 'notes', 'source', 'sources']);

/** Flatten a palette tree to {"gray.50": "#…"}; non-colour leaves are ignored. */
export function flattenPalette(tree: PaletteTree | undefined, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  if (!tree || typeof tree !== 'object') return out;
  for (const [k, v] of Object.entries(tree)) {
    if (!prefix && RESERVED.has(k)) continue;
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') {
      // opaque hex normalises to #RRGGBB; anything with an alpha keeps its own spelling
      if (isColor(v)) out[key] = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v.trim()) ? toHex(v) : v.trim();
    } else if (v && typeof v === 'object') Object.assign(out, flattenPalette(v as PaletteTree, key));
  }
  return out;
}

const squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');

const ROLE_NAMES: Record<string, string[]> = {
  page: ['page', 'background', 'bg', 'canvas', 'paper', 'ground', 'base'],
  surface: ['surface', 'card', 'panel', 'raised', 'sheet'],
  ink: ['ink', 'text', 'foreground', 'fg', 'body', 'textprimary', 'inkprimary'],
  ink2: ['ink2', 'textsecondary', 'inksecondary', 'secondary'],
  ink3: ['ink3', 'texttertiary', 'inktertiary', 'muted', 'subtle'],
  hairline: ['hairline', 'border', 'divider', 'rule', 'outline'],
  accent: ['accent', 'primary', 'brand', 'highlight', 'key'],
  accentInk: ['accentink', 'inkbrand', 'brandink', 'primaryink', 'accentdark', 'primarydark'],
  softAccent: ['softaccent', 'accentsoft', 'primarysoft', 'brandsoft', 'accentbg', 'tint'],
  inverse: ['inverse', 'dark', 'deep', 'night', 'bginverse'],
  inverseDeep: ['inversedeep', 'deeper', 'deepest', 'darker'],
  line: ['line', 'accentline', 'stroke'],
};

function lookup(all: Record<string, string>, ref: string | undefined): string | undefined {
  if (!ref) return undefined;
  if (isColor(ref)) return toHex(ref);
  if (all[ref]) return all[ref];
  const want = squash(ref);
  const hit = Object.keys(all).find((k) => squash(k) === want);
  return hit ? all[hit] : undefined;
}

function findRole(all: Record<string, string>, roles: Record<string, string>, role: string): string | undefined {
  const explicit = lookup(all, roles[role]);
  if (explicit) return explicit;
  for (const name of ROLE_NAMES[role] ?? []) {
    const hit = Object.keys(all).find((k) => squash(k) === name);
    if (hit) return all[hit];
  }
  return undefined;
}

function grayRamp(all: Record<string, string>, page: string, ink: string): string[] {
  const group = Object.keys(all).filter((k) => /^(gray|grey|neutral|greys|grays)\./i.test(k));
  if (group.length >= 3) return group.map((k) => all[k]).sort((a, b) => luminance(b) - luminance(a));
  return [0.04, 0.08, 0.14, 0.22, 0.34, 0.48, 0.62, 0.76, 0.88].map((k) => mix(page, ink, k));
}

const TONE_NAMES: Record<Exclude<ToneName, 'neutral'>, string[]> = {
  ok: ['ok', 'success', 'positive', 'good', 'green'],
  warn: ['warn', 'warning', 'caution', 'amber', 'attention'],
  risk: ['risk', 'danger', 'error', 'negative', 'critical', 'red'],
};

function tones(pack: BrandPack, all: Record<string, string>, page: string, ink: string, gray: string[], problems: string[]): Record<ToneName, Tone> {
  const given = (pack.palette?.tones ?? {}) as Record<string, Record<string, string>>;
  const neutral: Tone = {bg: gray[1] ?? mix(page, ink, 0.08), ink: mix(ink, page, 0.45), dot: gray[4] ?? mix(page, ink, 0.34)};
  const out = {neutral: given.neutral ? toneFrom(given.neutral, page, ink) ?? neutral : neutral} as Record<ToneName, Tone>;
  const missing: string[] = [];
  for (const tone of ['ok', 'warn', 'risk'] as const) {
    const g = given[tone] ?? Object.entries(given).find(([k]) => TONE_NAMES[tone].includes(squash(k)))?.[1];
    let t = g ? toneFrom(g, page, ink) : null;
    if (!t) {
      const key = Object.keys(all).find((k) => TONE_NAMES[tone].includes(squash(k)));
      if (key) t = toneFrom({dot: all[key]}, page, ink);
    }
    if (!t) missing.push(tone);
    out[tone] = t ?? neutral;
  }
  if (missing.length) problems.push(`the palette has no ${missing.join('/')} tone colours; those chips draw neutral (add palette.tones)`);
  return out;
}

function toneFrom(g: Record<string, string> | string, page: string, ink: string): Tone | null {
  const src = typeof g === 'string' ? {dot: g} : g;
  // a tone needs its signal colour: the dot, or the ink when only that is given
  const dot = isColor(src.dot) ? toHex(src.dot) : isColor(src.ink) ? toHex(src.ink) : null;
  if (!dot) return null;
  const bg = isColor(src.bg) ? toHex(src.bg) : mix(dot, page, 0.88);
  const inkC = isColor(src.ink) ? toHex(src.ink) : ensureContrast(mix(dot, ink, 0.3), bg, 4.5, ink);
  return {bg, ink: inkC, dot};
}

export function colorTokens(pack: BrandPack, problems: string[]): ColorTokens {
  const all = flattenPalette(pack.palette as PaletteTree | undefined);
  const roles = (pack.palette?.roles ?? {}) as Record<string, string>;
  const colors = Object.values(all);
  if (!colors.length) problems.push('the brand has no palette; the kit draws in plain greys until onboarding adds one');

  let page = findRole(all, roles, 'page');
  let ink = findRole(all, roles, 'ink');
  if (!page) {
    page = colors.length ? colors.reduce((a, b) => (luminance(b) > luminance(a) ? b : a)) : WHITE;
    if (colors.length) problems.push('no page colour named (palette.roles.page); using the lightest token');
  }
  if (!ink) {
    ink = colors.length ? colors.reduce((a, b) => (contrast(b, page!) > contrast(a, page!) ? b : a)) : mix(BLACK, WHITE, 0.07);
    if (colors.length) problems.push('no ink colour named (palette.roles.ink); using the token with the most contrast');
  }
  let accent = findRole(all, roles, 'accent');
  if (!accent) {
    const chroma = colors.filter((c) => saturation(c) > 0.2);
    accent = chroma.length ? chroma.reduce((a, b) => (saturation(b) > saturation(a) ? b : a)) : ink;
    if (colors.length) problems.push('no accent colour named (palette.roles.accent); using the most saturated token');
  }
  const light = luminance(page) > 0.5;
  const surface = findRole(all, roles, 'surface') ?? (light ? mix(page, WHITE, 0.7) : mix(page, WHITE, 0.06));
  const ink2 = findRole(all, roles, 'ink2') ?? ensureContrast(mix(ink, page, 0.2), page, 7, ink);
  const ink3 = findRole(all, roles, 'ink3') ?? ensureContrast(mix(ink, page, 0.42), page, 4.5, ink);
  const hairline = findRole(all, roles, 'hairline') ?? mix(ink, page, 0.86);
  const accentInk = findRole(all, roles, 'accentInk') ?? ensureContrast(mix(accent, ink, 0.25), page, 4.5, ink);
  const softAccent = findRole(all, roles, 'softAccent') ?? mix(accent, page, 0.9);
  const inverse = findRole(all, roles, 'inverse') ?? (light ? mix(accent, BLACK, 0.55) : mix(page, WHITE, 0.1));
  const inverseDeep = findRole(all, roles, 'inverseDeep') ?? mix(inverse, BLACK, 0.3);
  const onInverse = bestOn(inverse, [page, surface, WHITE], 4.5);
  const onAccent = bestOn(accent, [surface, page, ink, WHITE, BLACK], 3);
  const line = findRole(all, roles, 'line') ?? accent;
  const gray = grayRamp(all, page, ink);
  return {
    page,
    surface,
    ink,
    ink2,
    ink3,
    hairline,
    accent,
    accentInk,
    onAccent,
    softAccent,
    inverse,
    inverseDeep,
    onInverse,
    line,
    shadow: rgbTriplet(ink),
    gray,
    tones: tones(pack, all, page, ink, gray, problems),
    all,
  };
}

// ---------------------------------------------------------------------------------------------------------- type
const GENERIC: Record<string, FontFamilyToken['fallback']> = {mono: 'monospace', code: 'monospace', serif: 'serif'};

function familyList(t: BrandType | undefined): FamilyInput[] {
  const f = t?.families;
  if (!f) return [];
  if (Array.isArray(f)) return f;
  return Object.entries(f).map(([role, v]) => ({role, ...(v as FamilyInput)}));
}

function weightsOf(f: FamilyInput): number[] {
  const w = (f.weights ?? [400, 700]).map((x) => (typeof x === 'string' ? ({regular: 400, normal: 400, medium: 500, semibold: 600, bold: 700, black: 900} as Record<string, number>)[x.toLowerCase()] ?? Number(x) : x));
  const clean = w.filter((x) => Number.isFinite(x) && x >= 1 && x <= 1000);
  return clean.length ? [...new Set(clean)].sort((a, b) => a - b) : [400];
}

const cssQuote = (name: string) => (/^[a-z-]+$/i.test(name) && ['serif', 'sans-serif', 'monospace', 'system-ui', 'cursive', 'fantasy'].includes(name) ? name : `"${name.replace(/"/g, '')}"`);

export function fontTokens(pack: BrandPack, problems: string[]): Record<string, FontFamilyToken> {
  const list = familyList(pack.type);
  const out: Record<string, FontFamilyToken> = {};
  for (const f of list) {
    const family = String(f.family ?? f.name ?? '').trim();
    const role = String(f.role ?? f.id ?? family).toLowerCase();
    if (!family) {
      problems.push(`type family "${role}" has no family name`);
      continue;
    }
    const fallback = (f.fallback === 'serif' || f.fallback === 'monospace' || f.fallback === 'sans-serif' ? f.fallback : GENERIC[role] ?? 'sans-serif') as FontFamilyToken['fallback'];
    out[role] = {role, family, css: `${cssQuote(family)}, ${fallback}`, weights: weightsOf(f), fallback, declared: true};
  }
  // aliases: body/text → sans, heading/headline → display; display falls back to sans, sans to the first family
  const alias = (to: string, from: string[]) => {
    if (out[to]) return;
    const hit = from.find((r) => out[r]);
    if (hit) out[to] = {...out[hit], role: to};
  };
  alias('sans', ['body', 'text', 'ui', 'primary', 'regular']);
  alias('display', ['heading', 'headline', 'title', 'hero', 'sans']);
  alias('sans', ['display']);
  if (!out.sans && list.length) {
    const first = Object.values(out)[0];
    if (first) out.sans = {...first, role: 'sans'};
  }
  if (!out.sans) {
    problems.push('the brand has no type families; the kit draws in the generic sans-serif until onboarding adds them');
    out.sans = {role: 'sans', family: 'sans-serif', css: 'sans-serif', weights: [400, 700], fallback: 'sans-serif', declared: false};
  }
  alias('display', ['sans']);
  if (!out.mono) out.mono = {role: 'mono', family: 'monospace', css: 'monospace', weights: [400, 700], fallback: 'monospace', declared: false};
  if (!out.serif) out.serif = {...out.display, role: 'serif'};
  return out;
}

/** The kit's type roles, sizes as fractions of the frame's short side (body ≈ 2.8% reads at ≥ 28 px on 1080 lines). */
const DEFAULT_SCALE: Record<string, Omit<TypeStyle, 'role'>> = {
  hero: {font: 'display', weight: 700, size: 0.089, lineHeight: 1.04, tracking: -0.02, transform: 'none', tabular: false},
  statement: {font: 'display', weight: 400, size: 0.078, lineHeight: 1.08, tracking: -0.015, transform: 'none', tabular: false},
  h1: {font: 'display', weight: 700, size: 0.059, lineHeight: 1.1, tracking: -0.012, transform: 'none', tabular: false},
  h2: {font: 'display', weight: 700, size: 0.041, lineHeight: 1.15, tracking: 0, transform: 'none', tabular: false},
  body: {font: 'sans', weight: 400, size: 0.028, lineHeight: 1.4, tracking: 0, transform: 'none', tabular: false},
  small: {font: 'sans', weight: 400, size: 0.026, lineHeight: 1.35, tracking: 0, transform: 'none', tabular: false},
  eyebrow: {font: 'sans', weight: 700, size: 0.022, lineHeight: 1.2, tracking: 0.08, transform: 'uppercase', tabular: false},
  mono: {font: 'mono', weight: 400, size: 0.022, lineHeight: 1.3, tracking: 0.12, transform: 'uppercase', tabular: true},
  numeral: {font: 'display', weight: 700, size: 0.2, lineHeight: 0.9, tracking: -0.02, transform: 'none', tabular: true},
  caption: {font: 'sans', weight: 700, size: 0.037, lineHeight: 1.25, tracking: 0, transform: 'none', tabular: false},
  label: {font: 'sans', weight: 700, size: 0.024, lineHeight: 1.2, tracking: 0, transform: 'none', tabular: false},
};

function nearestWeight(want: number, have: number[]): number {
  return have.reduce((best, w) => (Math.abs(w - want) < Math.abs(best - want) ? w : best), have[0] ?? want);
}

export function typeTokens(pack: BrandPack, fonts: Record<string, FontFamilyToken>, problems: string[]): Record<string, TypeStyle> {
  const scale = pack.type?.scale ?? {};
  const reference = Number(pack.type?.reference ?? 0);
  const out: Record<string, TypeStyle> = {};
  const roles = new Set([...Object.keys(DEFAULT_SCALE), ...Object.keys(scale)]);
  for (const role of roles) {
    const base = DEFAULT_SCALE[role] ?? DEFAULT_SCALE.body;
    const s = (scale as Record<string, Record<string, unknown>>)[role] ?? {};
    const font = String(s.family ?? s.role ?? base.font).toLowerCase();
    const fam = fonts[font] ?? fonts.sans;
    if (!fonts[font]) problems.push(`type style "${role}" uses family "${font}", which the brand doesn't declare (drawn in ${fam.role})`);
    let size = Number(s.size ?? base.size);
    if (size > 1) {
      if (reference > 0) size = size / reference;
      else {
        problems.push(`type style "${role}" gives size ${size} px with no type.reference short side; read as a fraction of 1000`);
        size = size / 1000;
      }
    }
    const weight = nearestWeight(Number(s.weight ?? base.weight), fam.weights);
    const tracking = Number(s.tracking ?? s.letterSpacing ?? base.tracking);
    const lineHeight = Number(s.lineHeight ?? s.line ?? base.lineHeight);
    const tf = String(s.transform ?? s.case ?? base.transform).toLowerCase();
    out[role] = {
      role,
      font: fam.role === font ? font : fam.role,
      weight,
      size: Number.isFinite(size) && size > 0 ? size : base.size,
      lineHeight: Number.isFinite(lineHeight) ? lineHeight : base.lineHeight,
      tracking: Number.isFinite(tracking) ? tracking : 0,
      transform: tf === 'uppercase' || tf === 'upper' ? 'uppercase' : 'none',
      tabular: s.numeric !== undefined ? String(s.numeric).includes('tabular') : base.tabular,
    };
  }
  return out;
}

// -------------------------------------------------------------------------------------------------------- tokens
/** Every token of a brand pack. Memoised per pack. */
export const brandTokens = memoOnBrand((pack: BrandPack): Tokens => {
  const problems: string[] = [];
  const color = colorTokens(pack, problems);
  const fonts = fontTokens(pack, problems);
  const type = typeTokens(pack, fonts, problems);
  const r = (pack as {radius?: Record<string, number>}).radius ?? {};
  const radius = {card: Number(r.card ?? 0.022), control: Number(r.control ?? 0.015), pill: 0.5};
  return {color, fonts, type, radius, problems};
});

/** The tokens of the brand in force. */
export function tokens(pack?: BrandPack): Tokens {
  return brandTokens(pack ?? brandPack());
}

export type ResolvedTypeStyle = {
  fontFamily: string;
  fontWeight: number;
  fontSize: number;
  lineHeight: number;
  letterSpacing: string;
  textTransform: 'none' | 'uppercase';
  fontVariantNumeric?: 'tabular-nums';
  color: string;
};

/** A type role as CSS values in px for a frame whose short side is `short` px. */
export function typeStyle(t: Tokens, role: string, short: number, color?: string): ResolvedTypeStyle {
  const s = t.type[role] ?? t.type.body;
  const fam = t.fonts[s.font] ?? t.fonts.sans;
  const inkFor: Record<string, string> = {small: t.color.ink3, eyebrow: t.color.ink3, mono: t.color.ink3, body: t.color.ink2};
  return {
    fontFamily: fam.css,
    fontWeight: s.weight,
    fontSize: +(s.size * short).toFixed(3),
    lineHeight: s.lineHeight,
    letterSpacing: `${s.tracking}em`,
    textTransform: s.transform,
    ...(s.tabular ? {fontVariantNumeric: 'tabular-nums' as const} : {}),
    color: color ?? inkFor[role] ?? t.color.ink,
  };
}

/** Every token as a CSS custom property (--slate-color-page, --slate-font-sans, --slate-type-body-size …). */
export function cssVars(t: Tokens, short: number): Record<string, string> {
  const out: Record<string, string> = {};
  const c = t.color;
  for (const k of ['page', 'surface', 'ink', 'ink2', 'ink3', 'hairline', 'accent', 'accentInk', 'onAccent', 'softAccent', 'inverse', 'inverseDeep', 'onInverse', 'line'] as const) {
    out[`--slate-color-${k.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`)}`] = c[k];
  }
  out['--slate-shadow-rgb'] = c.shadow;
  c.gray.forEach((g, i) => (out[`--slate-gray-${i}`] = g));
  for (const [name, tone] of Object.entries(c.tones)) for (const [part, v] of Object.entries(tone)) out[`--slate-tone-${name}-${part}`] = v;
  for (const [role, f] of Object.entries(t.fonts)) out[`--slate-font-${role}`] = f.css;
  for (const [role, s] of Object.entries(t.type)) {
    out[`--slate-type-${role}-size`] = `${+(s.size * short).toFixed(2)}px`;
    out[`--slate-type-${role}-weight`] = String(s.weight);
    out[`--slate-type-${role}-line`] = String(s.lineHeight);
  }
  out['--slate-radius-card'] = `${+(t.radius.card * short).toFixed(2)}px`;
  out['--slate-radius-control'] = `${+(t.radius.control * short).toFixed(2)}px`;
  return out;
}

/** A contrast pair list for accessibility checks (the Python brand module's contrast_pairs shape). */
export function contrastPairs(t: Tokens): {fg: string; bg: string; role: 'body' | 'large'; ratio: number}[] {
  const c = t.color;
  const pairs: [string, string, 'body' | 'large'][] = [
    [c.ink, c.page, 'body'],
    [c.ink2, c.page, 'body'],
    [c.ink3, c.page, 'body'],
    [c.ink, c.surface, 'body'],
    [c.accentInk, c.page, 'body'],
    [c.onAccent, c.accent, 'large'],
    [c.onInverse, c.inverse, 'body'],
  ];
  return pairs.map(([fg, bg, role]) => ({fg, bg, role, ratio: +contrast(fg, bg).toFixed(2)}));
}

