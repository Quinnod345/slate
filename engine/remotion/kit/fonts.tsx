// Fonts in the page: load every face the brand declares, prove each rendered as itself, and fail the render loudly
// when one didn't (core/fontplan.ts explains the test). The result goes to the console as one line the engine reads
// (`SLATE_FONT_GATE {…}`) and onto window.__SLATE_FONT_GATE__, so the render's own logs carry the `fonts` gate.
//
// Google Fonts load through @remotion/google-fonts, one static import per family: the engine's generated entry
// imports the brand's families and registers them here (registerGoogleFont), so a bundle never pulls in the whole
// catalogue. Installed faces need no loading: the width test proves they are there. Kit URLs (a type service's
// stylesheet) and font files load in the page.
import React, {useEffect, useState} from 'react';
import {cancelRender, continueRender, delayRender} from 'remotion';
import {checkPlan, fontPlan, gateReport, reportLine, type FaceDecl, type FontGateReport, type FontPlan, type Measure} from './core/fontplan';

type Loader = (face: FaceDecl) => unknown;

/** How long one stylesheet or font file may take to load before the gate calls it a failure. */
const LOAD_TIMEOUT_MS = 30000;
const loaders = new Map<string, Loader>();

/** Register a loader for a family (any source kind that needs one). */
export function registerFontLoader(family: string, loader: Loader): void {
  loaders.set(family.trim().toLowerCase(), loader);
}

type GoogleLoadFont = (style?: string, options?: {weights?: string[]; subsets?: string[]}) => {waitUntilDone?: () => Promise<unknown>} | unknown;

/**
 * Register a family's `loadFont` from @remotion/google-fonts/<Family>: the generated entry does
 * `import {loadFont} from '@remotion/google-fonts/Inter'; registerGoogleFont('Inter', loadFont);`.
 */
export function registerGoogleFont(family: string, loadFont: GoogleLoadFont, subsets: string[] = ['latin']): void {
  registerFontLoader(family, async (face) => {
    for (const style of face.styles) {
      const r = loadFont(style, {weights: face.weights.map(String), subsets}) as {waitUntilDone?: () => Promise<unknown>} | undefined;
      if (r && typeof r.waitUntilDone === 'function') await r.waitUntilDone();
    }
  });
}

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const id = setTimeout(() => reject(new Error(`${what} took longer than ${Math.round(ms / 1000)} s`)), ms);
    p.then(
      (v) => {
        clearTimeout(id);
        resolve(v);
      },
      (e) => {
        clearTimeout(id);
        reject(e);
      },
    );
  });
}

const bare = (family: string) => family.replace(/^["']|["']$/g, '').trim().toLowerCase();

/**
 * document.fonts' half of the gate, for faces the page loads (Google Fonts, kit URLs, font files): after loading, the
 * family must be in document.fonts as a loaded face. Installed faces never appear there (the browser finds them
 * itself), so for them the width test is the proof.
 */
function assertInFontSet(face: FaceDecl): void {
  const want = bare(face.family);
  let loaded = 0;
  document.fonts.forEach((f) => {
    if (bare(f.family) === want && f.status === 'loaded') loaded++;
  });
  if (!loaded) throw new Error(`${face.family} loaded, but document.fonts holds no loaded face of that family`);
  for (const style of face.styles)
    for (const w of face.weights)
      if (!document.fonts.check(`${style} ${w} 72px "${face.family}"`)) throw new Error(`document.fonts says ${face.family} ${w}${style === 'italic' ? ' italic' : ''} isn't ready`);
}

async function loadFace(face: FaceDecl): Promise<void> {
  const src = face.source;
  const custom = loaders.get(face.family.toLowerCase());
  if (custom) {
    await withTimeout(Promise.resolve(custom(face)), 45000, `loading ${face.family}`);
    if (src.kind !== 'installed') assertInFontSet(face);
    return;
  }
  switch (src.kind) {
    case 'installed':
      return; // nothing to load: the width test proves it's there
    case 'google':
      throw new Error(`${face.family} comes from Google Fonts, but no loader is registered for it. The engine's generated entry registers one per brand family (registerGoogleFont).`);
    case 'url': {
      let link = document.querySelector<HTMLLinkElement>(`link[data-slate-font="${src.url}"]`);
      if (!link) {
        link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = src.url;
        link.dataset.slateFont = src.url;
        const loaded = new Promise<void>((resolve, reject) => {
          link!.onload = () => resolve();
          link!.onerror = () => reject(new Error(`the stylesheet ${src.url} didn't load`));
        });
        document.head.appendChild(link);
        await withTimeout(loaded, LOAD_TIMEOUT_MS, `loading ${src.url}`);
      }
      for (const style of face.styles)
        for (const w of face.weights) {
          const faces = await withTimeout(document.fonts.load(`${style} ${w} 72px "${face.family}"`), LOAD_TIMEOUT_MS, `loading ${face.family} ${w}`);
          if (!faces.length) throw new Error(`${src.url} loaded but declares no "${face.family}" face at weight ${w}${style === 'italic' ? ' italic' : ''}`);
        }
      assertInFontSet(face);
      return;
    }
    case 'file': {
      const weight = face.weights.length > 1 ? `${face.weights[0]} ${face.weights[face.weights.length - 1]}` : String(face.weights[0] ?? 400);
      for (const style of face.styles) {
        const ff = new FontFace(face.family, `url(${JSON.stringify(src.url)})${src.format ? ` format(${JSON.stringify(src.format)})` : ''}`, {weight, style});
        await withTimeout(ff.load(), LOAD_TIMEOUT_MS, `loading ${src.url}`);
        document.fonts.add(ff);
      }
      assertInFontSet(face);
      return;
    }
  }
}

let ctx: CanvasRenderingContext2D | null = null;

/** Width of text in a CSS font, measured on a canvas (the page's real font stack). */
export const canvasMeasure: Measure = (font, text) => {
  if (!ctx) ctx = document.createElement('canvas').getContext('2d');
  if (!ctx) return 0;
  ctx.font = font;
  return ctx.measureText(text).width;
};

const gates = new Map<string, Promise<FontGateReport>>();

/** Load every declared face and run the gate (once per page for each plan). */
export function runFontGate(plan: FontPlan = fontPlan()): Promise<FontGateReport> {
  const key = JSON.stringify(plan.faces);
  const hit = gates.get(key);
  if (hit) return hit;
  const gate = (async () => {
    const loadErrors: string[] = [];
    for (const face of plan.faces) {
      try {
        await loadFace(face);
      } catch (e) {
        loadErrors.push(`${face.family}: ${(e as Error).message}`);
      }
    }
    const report = gateReport(checkPlan(plan, canvasMeasure), loadErrors, plan.problems);
    (window as unknown as {__SLATE_FONT_GATE__?: FontGateReport}).__SLATE_FONT_GATE__ = report;
    if (report.status === 'PASS') console.log(reportLine(report));
    else console.error(reportLine(report));
    return report;
  })();
  gates.set(key, gate);
  return gate;
}

/** Forget the gate's results (a review sheet that switches brands in one page). */
export function resetFontGate(): void {
  gates.clear();
  holds.clear();
}

/** The page's hold on its first frame for one font plan. `done` says whether the faces passed. */
export type FirstFrameHold = {handle: number; settled: boolean; ok: boolean | null; done: Promise<boolean>};

type HoldDeps = {
  delayRender: (label: string, opts: {timeoutInMilliseconds: number}) => number;
  continueRender: (handle: number) => void;
  cancelRender: (err: unknown) => void;
  run: (plan: FontPlan) => Promise<FontGateReport>;
};

const holds = new Map<string, FirstFrameHold>();

/** Three minutes, the renderer's own wait (DEFAULT_RENDER_TIMEOUT_MS in remotion/browser.mjs). */
export const FONT_HOLD_MS = 180000;

/**
 * Hold the page's first frame until the plan's faces pass, once per page for each plan: whichever FontGate renders
 * first takes the hold, and the gate's own result releases it (or fails the render, loudly, with the report). Nothing
 * about a component's life can strand it. A FontGate that took its hold in a useState initializer left the
 * 2026-10-01 stability bench waiting three minutes on a hold its gate had already passed: React may run a render it
 * then throws away, or mount a tree twice, and a hold taken there was released by no effect.
 */
export function holdFirstFrame(plan: FontPlan, deps?: Partial<HoldDeps>): FirstFrameHold {
  const key = JSON.stringify(plan.faces);
  const have = holds.get(key);
  if (have) return have;
  const d: HoldDeps = {delayRender, continueRender, cancelRender, run: runFontGate, ...deps};
  const handle = d.delayRender("Slate's font gate: loading and checking the brand's faces", {timeoutInMilliseconds: FONT_HOLD_MS});
  const hold: FirstFrameHold = {handle, settled: false, ok: null, done: Promise.resolve(false)};
  hold.done = d.run(plan).then(
    (r) => {
      hold.settled = true;
      hold.ok = r.status === 'PASS';
      if (hold.ok) d.continueRender(handle);
      else d.cancelRender(new Error(`Font gate failed: ${r.summary}. The brand's fonts must render as declared (fonts by reference, never substituted). ${reportLine(r)}`));
      return hold.ok;
    },
    (e) => {
      hold.settled = true;
      hold.ok = false;
      d.cancelRender(e);
      return false;
    },
  );
  holds.set(key, hold);
  return hold;
}

/**
 * Holds the first frame until every declared face has loaded and measured as itself; fails the render, loudly and
 * with the report, when one didn't. The hold is the page's (holdFirstFrame), so mounting twice takes it once.
 */
export function FontGate({children, enabled = true, plan}: {children?: React.ReactNode; enabled?: boolean; plan?: FontPlan}) {
  const hold = enabled ? holdFirstFrame(plan ?? fontPlan()) : null;
  const [ready, setReady] = useState(() => hold === null || hold.ok === true);
  useEffect(() => {
    if (hold === null || ready) return;
    let live = true;
    hold.done.then((ok) => {
      if (live && ok) setReady(true);
    });
    return () => {
      live = false;
    };
  }, [hold, ready]);
  return ready ? <>{children}</> : null;
}
