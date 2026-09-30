// The looks library: named bundles of the kit's defaults (ground, cards, camera, type builds, the line), so one brand
// can be papery and another flat without a component changing. A brand chooses a look at onboarding (brand.look.id,
// with optional overrides), or onboarding builds it its own from these. Paper 2.5D is used only where a brand's own
// design language is papery; the default for a brand that hasn't chosen is flat.
//
// After Effects looks are the AE bridge's motion-design skills. They are listed here by name only, so a look can be
// chosen in one place whatever the engine; the AE module builds them.
import {brandPack, memoOnBrand} from './brand';
import type {BrandPack} from './types';

export type Look = {
  id: string;
  engine: 'remotion';
  name: string;
  summary: string;
  /** true when the look draws paper: cards with thickness, grain, pages behind */
  papery: boolean;
  ground: {grain: number; grainBlend: 'multiply' | 'soft-light' | 'none'; bloom: number; vignette: number; grid: number};
  card: {
    /** silhouettes stacked behind the face (paper thickness); 0 is a flat card */
    layers: number;
    /** gap between layers, fraction of the short side */
    layerGap: number;
    /** corner radius, fraction of the short side */
    radius: number;
    shadow: 'layered' | 'soft' | 'none';
    border: 'hairline' | 'none' | 'rule';
    pages: boolean;
  };
  camera: {
    /** perspective as a multiple of the frame's long side */
    perspective: number;
    tilt: boolean;
  };
  type: {hero: 'per-char' | 'per-word' | 'line'; line: 'per-word' | 'line'; list: 'per-line' | 'block'};
  /** stroke weight of the accent line, fraction of the short side */
  line: number;
  /** motion roles a look leans on (names of roles, resolved to the brand's eases) */
  motion: {entrance: string; carry: string; state: string; exit: string};
};

export type AeLook = {id: string; engine: 'ae'; name: string; skill: string; builtBy: 'ae'};

/**
 * The engine a brand's scenes are built in. A kit look draws the same way in a Remotion act and in a web scene (the
 * open web renderer's runtime reads the same tokens, eases and look), so a brand picks the engine with `look.engine`:
 * "remotion" (the default), "web" (no Remotion licence needed) or "ae" (an After Effects look implies it).
 */
export type SceneEngine = 'remotion' | 'web' | 'ae';
export const SCENE_ENGINES: readonly SceneEngine[] = ['remotion', 'web', 'ae'];

const base = {
  camera: {perspective: 0.78, tilt: true},
  motion: {entrance: 'entrance', carry: 'move', state: 'state', exit: 'exit'},
} as const;

/** The Remotion looks. */
export const LOOKS: Record<string, Look> = {
  'paper-2.5d': {
    id: 'paper-2.5d',
    engine: 'remotion',
    name: 'Paper 2.5D',
    summary: 'Paper cards with real thickness and pages behind them under a gentle perspective camera, soft layered shadows, paper grain. Only for brands whose own design language is papery.',
    papery: true,
    ground: {grain: 0.05, grainBlend: 'multiply', bloom: 0.9, vignette: 0.45, grid: 0},
    card: {layers: 6, layerGap: 0.0015, radius: 0.017, shadow: 'layered', border: 'hairline', pages: true},
    camera: {...base.camera},
    type: {hero: 'per-char', line: 'per-word', list: 'per-line'},
    line: 0.0033,
    motion: {...base.motion},
  },
  flat: {
    id: 'flat',
    engine: 'remotion',
    name: 'Flat',
    summary: 'Flat surfaces with hairline borders and one soft shadow, no grain, no thickness. The default until a brand chooses.',
    papery: false,
    ground: {grain: 0, grainBlend: 'none', bloom: 0, vignette: 0, grid: 0},
    card: {layers: 0, layerGap: 0, radius: 0.022, shadow: 'soft', border: 'hairline', pages: false},
    camera: {perspective: 0.9, tilt: false},
    type: {hero: 'per-word', line: 'per-word', list: 'per-line'},
    line: 0.0028,
    motion: {...base.motion},
  },
  editorial: {
    id: 'editorial',
    engine: 'remotion',
    name: 'Editorial',
    summary: 'Type-led: big kinetic type on an open page, rules instead of boxes, no cards or shadows. For statement films, kinetic type and logo stings.',
    papery: false,
    ground: {grain: 0, grainBlend: 'none', bloom: 0, vignette: 0, grid: 0},
    card: {layers: 0, layerGap: 0, radius: 0, shadow: 'none', border: 'rule', pages: false},
    camera: {perspective: 1.2, tilt: false},
    type: {hero: 'per-char', line: 'per-word', list: 'per-line'},
    line: 0.0022,
    motion: {...base.motion},
  },
  blueprint: {
    id: 'blueprint',
    engine: 'remotion',
    name: 'Blueprint',
    summary: 'Construction-line drawing: a faint grid, dashed guides and vertex marks, shapes drawn then filled. The logo build as a whole visual language.',
    papery: false,
    ground: {grain: 0, grainBlend: 'none', bloom: 0, vignette: 0, grid: 0.07},
    card: {layers: 0, layerGap: 0, radius: 0.008, shadow: 'none', border: 'hairline', pages: false},
    camera: {perspective: 1.0, tilt: false},
    type: {hero: 'per-char', line: 'per-word', list: 'per-line'},
    line: 0.0022,
    motion: {...base.motion},
  },
};

/** The AE bridge's motion-design skills, as AE looks (by name only; the AE module builds them). */
export const AE_LOOKS: AeLook[] = [
  ['ae-clean-rig', 'Clean rig (the bridge’s entry point)'],
  ['use-after-effects', 'Native After Effects authoring'],
  ['ae-mcp-realities', 'Bridge realities'],
  ['ae-build-orchestration', 'Build orchestration'],
  ['ae-animation-principles', 'Animation principles'],
  ['ae-design-first', 'Design first'],
  ['ae-ui-mastery', 'UI mastery'],
  ['ae-depth-space', 'Depth and space'],
  ['ae-liquid-glass', 'Liquid glass'],
  ['ae-transition-kit', 'Transition kit'],
  ['ae-cleanup', 'Cleanup'],
  ['ae-matte-painting', 'Matte painting'],
  ['ae-figma-transfer', 'Figma transfer'],
].map(([skill, name]) => ({id: skill, engine: 'ae' as const, name, skill, builtBy: 'ae' as const}));

/** The look new brands get until onboarding chooses one. */
export const DEFAULT_LOOK = 'flat';

function merge<T>(a: T, b: unknown): T {
  if (!b || typeof b !== 'object' || Array.isArray(b)) return a;
  const out = {...(a as Record<string, unknown>)};
  for (const [k, v] of Object.entries(b as Record<string, unknown>)) {
    const cur = out[k];
    out[k] = cur && typeof cur === 'object' && !Array.isArray(cur) && v && typeof v === 'object' && !Array.isArray(v) ? merge(cur, v) : v;
  }
  return out as T;
}

/** The engine the brand asked for with `look.engine` (null when it didn't say), noting a name that isn't an engine. */
function engineAsked(raw: BrandPack['look'], problems: string[]): SceneEngine | null {
  const asked = raw && typeof raw === 'object' ? raw.engine : undefined;
  if (asked === undefined || asked === null || asked === '') return null;
  if (typeof asked === 'string' && (SCENE_ENGINES as readonly string[]).includes(asked)) return asked as SceneEngine;
  problems.push(`look.engine ${JSON.stringify(asked)} isn't an engine (${SCENE_ENGINES.join(', ')}); building in "remotion"`);
  return null;
}

/**
 * The look a brand chose (its overrides applied) and the engine its scenes are built in, or the defaults with a
 * problem noted.
 */
export const lookOf = memoOnBrand((pack: BrandPack): {look: Look; ae: AeLook | null; engine: SceneEngine; problems: string[]} => {
  const problems: string[] = [];
  const raw = pack.look;
  const id = typeof raw === 'string' ? raw : raw?.id;
  const asked = engineAsked(raw, problems);
  if (!id) {
    problems.push(`the brand hasn't chosen a look; drawing "${DEFAULT_LOOK}" (onboarding sets brand.look.id)`);
    return {look: LOOKS[DEFAULT_LOOK], ae: null, engine: asked ?? 'remotion', problems};
  }
  const ae = AE_LOOKS.find((l) => l.id === id) ?? null;
  if (ae) {
    if (asked && asked !== 'ae') problems.push(`look "${id}" is an After Effects look, so its scenes are built in "ae", not "${asked}"`);
    problems.push(`look "${id}" is an After Effects look; Remotion scenes draw "${DEFAULT_LOOK}" and the AE module builds the look`);
    return {look: LOOKS[DEFAULT_LOOK], ae, engine: 'ae', problems};
  }
  if (asked === 'ae') problems.push(`look.engine is "ae" but "${id}" is a kit look; choose one of the AE bridge's looks for an After Effects build`);
  const engine: SceneEngine = asked === 'web' ? 'web' : 'remotion';
  const found = LOOKS[id];
  if (!found) {
    problems.push(`look "${id}" isn't in the library (${Object.keys(LOOKS).join(', ')}); drawing "${DEFAULT_LOOK}"`);
    return {look: LOOKS[DEFAULT_LOOK], ae: null, engine, problems};
  }
  const overrides = typeof raw === 'object' && raw ? raw.overrides : undefined;
  return {look: overrides ? merge(found, overrides) : found, ae: null, engine, problems};
});

/** The engine the brand in force builds its scenes in (`look.engine`, "remotion" unless it says otherwise). */
export function sceneEngine(pack?: BrandPack): SceneEngine {
  return lookOf(pack ?? brandPack()).engine;
}

/** The look of the brand in force. */
export function look(pack?: BrandPack): Look {
  return lookOf(pack ?? brandPack()).look;
}

/** Every look, both engines, for pickers and onboarding. */
export function listLooks(): {remotion: Look[]; ae: AeLook[]} {
  return {remotion: Object.values(LOOKS), ae: AE_LOOKS};
}
