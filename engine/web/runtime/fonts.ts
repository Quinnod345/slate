// The web font gate: every face the brand declares must render as itself, or the render fails, as it does in Remotion.
//
// The kit's React-free core decides the plan (which faces, from where) and the verdict (the width test against three
// generic families: headless Chrome substitutes a missing face silently, and document.fonts.check() says yes to
// families it has never heard of). This file only loads faces the web renderer's way and reports the result in the
// kit's own format (`SLATE_FONT_GATE {…}` on the console and window.__SLATE_FONT_GATE__), so the engine turns it into
// the same `fonts` gate result.
//
// The web renderer never touches the network, so a face must be on this Mac: installed, a font file the renderer
// serves locally (the brand folder, or a file named by absolute path), or, for a family the pack serves from Google
// Fonts or a type service, a local copy of its files in Slate's fonts folder (<SLATE_DATA>/fonts/<Family>/, or the
// brand folder's fonts/<Family>/; weights and styles are read from the file names). Anything else can't load here,
// and the gate says so, with the fix.
import {checkPlan, fontPlan, gateReport, reportLine, type FaceDecl, type FontGateReport, type FontPlan, type Measure} from '../../remotion/kit/core/index';
import {sceneGlobals} from './env';

export type WebFontGateReport = FontGateReport;

const LOAD_TIMEOUT_MS = 15000;

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

/** The local URL the renderer serves a pack's font source at (it maps every file source before the page loads). */
function localUrl(url: string): string | null {
  const map = sceneGlobals()?.fontUrls ?? {};
  if (map[url]) return map[url];
  if (url.startsWith('/_slate/')) return url;
  return null;
}

const bare = (family: string) => family.replace(/^["']|["']$/g, '').trim().toLowerCase();

function assertInFontSet(face: FaceDecl): void {
  const want = bare(face.family);
  let loaded = 0;
  document.fonts.forEach((f) => {
    if (bare(f.family) === want && f.status === 'loaded') loaded++;
  });
  if (!loaded) throw new Error(`${face.family} loaded, but document.fonts holds no loaded face of that family`);
}

const remote = (url: string) => /^(https?:)?\/\//i.test(url);

const offline = (family: string, where: string) => {
  const dir = sceneGlobals()?.fontDir;
  const folder = dir ? `${dir}/${family}/` : `Slate's fonts folder (<SLATE_DATA>/fonts/${family}/)`;
  if (where !== 'Google Fonts' && !remote(where)) {
    return `the brand pack names ${where} for ${family}, and there's no such font file on this Mac. Fix the path in the ` +
      `pack, install ${family} (type source "installed"), or put its font files in ${folder}.`;
  }
  return `${family} comes from ${where}, and the web renderer never touches the network. Install ${family} on this Mac ` +
    `(type source "installed"), or put its font files in ${folder}.`;
};

type LocalFile = {url: string; weight: string; style: 'normal' | 'italic'};

/** Local copies of a family's files, which the renderer found in a fonts folder. */
const localFiles = (family: string): LocalFile[] => sceneGlobals()?.fontFiles?.[family] ?? [];

async function loadLocalFiles(face: FaceDecl, files: LocalFile[]): Promise<void> {
  for (const f of files) {
    const ff = new FontFace(face.family, `url(${JSON.stringify(f.url)})`, {weight: f.weight, style: f.style});
    await withTimeout(ff.load(), LOAD_TIMEOUT_MS, `loading ${f.url}`);
    document.fonts.add(ff);
  }
  assertInFontSet(face);
}

async function loadFace(face: FaceDecl): Promise<void> {
  const src = face.source;
  switch (src.kind) {
    case 'installed':
      return; // nothing to load: the width test proves it's there
    case 'google': {
      const files = localFiles(face.family);
      if (files.length) return loadLocalFiles(face, files);
      throw new Error(offline(face.family, 'Google Fonts'));
    }
    case 'url': {
      const url = localUrl(src.url);
      if (!url) {
        const files = localFiles(face.family);
        if (files.length) return loadLocalFiles(face, files);
        throw new Error(offline(face.family, src.url));
      }
      let link = document.querySelector<HTMLLinkElement>(`link[data-slate-font="${url}"]`);
      if (!link) {
        link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = url;
        link.dataset.slateFont = url;
        const loaded = new Promise<void>((resolve, reject) => {
          link!.onload = () => resolve();
          link!.onerror = () => reject(new Error(`the stylesheet ${src.url} didn't load`));
        });
        document.head.appendChild(link);
        await withTimeout(loaded, LOAD_TIMEOUT_MS, `loading ${src.url}`);
      }
      for (const style of face.styles)
        for (const w of face.weights) await withTimeout(document.fonts.load(`${style} ${w} 72px "${face.family}"`), LOAD_TIMEOUT_MS, `loading ${face.family} ${w}`);
      assertInFontSet(face);
      return;
    }
    case 'file': {
      const url = localUrl(src.url);
      if (!url) {
        const files = localFiles(face.family);
        if (files.length) return loadLocalFiles(face, files);
        throw new Error(offline(face.family, src.url));
      }
      const weight = face.weights.length > 1 ? `${face.weights[0]} ${face.weights[face.weights.length - 1]}` : String(face.weights[0] ?? 400);
      for (const style of face.styles) {
        const ff = new FontFace(face.family, `url(${JSON.stringify(url)})${src.format ? ` format(${JSON.stringify(src.format)})` : ''}`, {weight, style});
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

let running: Promise<WebFontGateReport> | null = null;

/** Load every declared face and run the gate, once per page. The result goes to the console for the engine. */
export function runWebFontGate(plan: FontPlan = fontPlan()): Promise<WebFontGateReport> {
  if (running) return running;
  running = (async () => {
    const loadErrors: string[] = [];
    for (const face of plan.faces) {
      try {
        await loadFace(face);
      } catch (e) {
        loadErrors.push(`${face.family}: ${(e as Error).message}`);
      }
    }
    const report = gateReport(checkPlan(plan, canvasMeasure), loadErrors, plan.problems);
    report.engine = {name: `${report.engine.name}, web renderer`, version: report.engine.version};
    (window as unknown as {__SLATE_FONT_GATE__?: WebFontGateReport}).__SLATE_FONT_GATE__ = report;
    if (report.status === 'PASS') console.log(reportLine(report));
    else console.error(reportLine(report));
    return report;
  })();
  return running;
}
