// The kit's own rules, used from Node so the engine never keeps a second copy of them.
//
// The kit's React-free core (engine/remotion/kit/core) decides which of a brand's type families come from Google Fonts
// (fontPlan) and what the font gate's report means (parseReportLine, toGateResult). The engine needs both: the
// generated entry registers a loader for every Google family (the kit's FontGate refuses a Google face nobody
// registered), and renders and stills turn the gate's console line into the `fonts` gate result. This module builds
// the core once per process with esbuild and hands those functions over; without a kit it returns null and the
// engine carries on without them.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {KIT_DIR, NODE_MODULES, log} from './lib/common.mjs';

const NEUTRAL_DATA_FILE = path.join(path.dirname(KIT_DIR), 'data', 'neutral-film-data.ts');
let cached; // undefined: not tried; null: no kit core; else the module

/** The kit's core, evaluated in Node (its '@film/data' is the neutral film: the functions used here take a pack). */
export async function kitCore() {
  if (cached !== undefined) return cached;
  const index = ['index.ts', 'index.tsx', 'index.js'].map((f) => path.join(KIT_DIR, 'core', f)).find((f) => fs.existsSync(f));
  if (!index) return (cached = null);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'slate-kitcore-'));
  try {
    const entry = path.join(tmp, 'entry.mjs');
    fs.writeFileSync(entry, "export * from '@slate/kit/core';\n");
    const outfile = path.join(tmp, 'core.mjs');
    await build({
      entryPoints: [entry],
      bundle: true,
      platform: 'node',
      format: 'esm',
      target: 'node20',
      outfile,
      loader: {'.json': 'json'},
      alias: {'@slate/kit': KIT_DIR, '@film/data': NEUTRAL_DATA_FILE},
      nodePaths: [NODE_MODULES],
      logLevel: 'silent',
    });
    cached = await import(pathToFileURL(outfile).href);
  } catch (e) {
    log(`the kit's core didn't load in Node (${String(e?.message ?? e).split('\n')[0]}); carrying on without it`);
    cached = null;
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
  }
  return cached;
}

/** The @remotion/google-fonts module for a family ("JetBrains Mono" → JetBrainsMono), or null when it has none. */
export function googleFontModule(family) {
  const name = String(family).replace(/[^A-Za-z0-9]/g, '');
  return name && fs.existsSync(path.join(NODE_MODULES, '@remotion', 'google-fonts', 'dist', 'esm', `${name}.mjs`)) ? `@remotion/google-fonts/${name}` : null;
}

/** The brand's Google Fonts families, by the kit's own font plan: [{family, module}] (module null when unknown). */
export async function googleFamilies(brand) {
  if (!brand) return [];
  const core = await kitCore();
  if (!core?.fontPlan) return [];
  const seen = new Set();
  const out = [];
  for (const face of core.fontPlan(brand).faces ?? []) {
    if (face.source?.kind !== 'google' || seen.has(face.source.family)) continue;
    seen.add(face.source.family);
    out.push({family: face.source.family, module: googleFontModule(face.source.family)});
  }
  return out;
}

/**
 * The `fonts` gate result from what a page printed (browser log lines, or a render error's message), by the kit's
 * own parseReportLine and toGateResult. The last report wins. Null when no page reported one.
 */
export async function fontGate(texts) {
  const core = await kitCore();
  if (!core?.parseReportLine || !core?.toGateResult) return null;
  let report = null;
  for (const t of texts) {
    const r = core.parseReportLine(String(t ?? ''));
    if (r) report = r;
  }
  return report ? core.toGateResult(report) : null;
}

/** A Remotion onBrowserLog handler that keeps the font gate's lines (everything else still goes to stderr). */
export function browserLogCollector() {
  const lines = [];
  return {
    lines,
    onBrowserLog: (entry) => {
      const text = String(entry?.text ?? '');
      if (text.includes('SLATE_FONT_GATE')) lines.push(text);
    },
  };
}
