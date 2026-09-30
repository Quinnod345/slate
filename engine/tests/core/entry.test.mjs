// Entry generation: one composition per act plus the whole film, '@film/data' in the kit's shape, a tsconfig that
// type-checks from the film folder, placeholders for acts not built yet, and no deep relative imports anywhere.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {ENGINE, cli, film, kitCore, ok, readJson, scratch, twoActs, writeJson} from './helpers.mjs';
import {filmDataSource, writeNeutralData} from '../../remotion/entry.mjs';
import {loadFilm} from '../../remotion/lib/film.mjs';
import {typecheck} from '../../remotion/check.mjs';

let root;
let data;
beforeAll(() => {
  root = scratch('entry');
  data = path.join(root, 'data');
});
afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

describe('entry', () => {
  it('writes .slate/ and a tsconfig: Act1 and Film, from shots.json and scenes/', () => {
    const dir = film(root, 'basic');
    const r = ok('entry', {film: dir}, {data});
    expect(r.compositions).toEqual(['Act1', 'Film']);
    expect(r.acts).toEqual([{act: 1, comp: 'Act1', scene: 'scenes/act1/index.tsx', frame_in: 0, frames: 30}]);
    for (const f of ['.slate/entry.tsx', '.slate/Root.tsx', '.slate/film-data.ts', '.slate/placeholder.tsx', '.slate/label.tsx', 'tsconfig.json', '.slate/tsconfig.act1.json']) {
      expect(fs.existsSync(path.join(dir, f)), f).toBe(true);
    }
    const rootTsx = fs.readFileSync(path.join(dir, '.slate/Root.tsx'), 'utf8');
    expect(rootTsx).toMatch(/<Composition id="Act1"/);
    expect(rootTsx).toMatch(/<Composition id="Film"/);
    expect(rootTsx).toMatch(/Do not edit by hand/);
    // sizes and rates are read from film.json through @film/data, never written into the Root
    expect(rootTsx).not.toMatch(/width=\{\d/);
    expect(rootTsx).toMatch(/fps=\{FPS\}/);
  });

  it('never imports up out of the film: scenes reach the kit and data by alias', () => {
    const dir = film(root, 'aliases');
    ok('entry', {film: dir}, {data});
    for (const f of ['.slate/Root.tsx', '.slate/entry.tsx', '.slate/film-data.ts', '.slate/placeholder.tsx', '.slate/label.tsx']) {
      const text = fs.readFileSync(path.join(dir, f), 'utf8');
      expect(text, f).not.toMatch(/from '(\.\.\/){2,}/);
    }
    const cfg = fs.readFileSync(path.join(dir, 'tsconfig.json'), 'utf8');
    const json = JSON.parse(cfg.split('\n').filter((l) => !l.startsWith('//')).join('\n'));
    expect(json.compilerOptions.paths['@slate/kit']).toEqual([path.join(ENGINE, 'remotion', 'kit')]);
    expect(json.compilerOptions.paths['@film/data']).toEqual(['./.slate/film-data.ts']);
    expect(json.compilerOptions.paths['*'][0]).toBe(`${path.join(ENGINE, 'node_modules')}/@types/*`);
  });

  it('rewrites only what changed', () => {
    const dir = film(root, 'stable');
    expect(ok('entry', {film: dir}, {data}).changed.length).toBeGreaterThan(0);
    expect(ok('entry', {film: dir}, {data}).changed).toEqual([]);
  });

  it('type-checks from the film folder (strict), against the engine packages', () => {
    const dir = film(root, 'types');
    ok('entry', {film: dir}, {data});
    const g = typecheck(loadFilm(dir), 1, 'handin.act1.typecheck');
    expect(g.status, g.evidence.join('\n')).toBe('PASS');
    expect(g.engine).toEqual({name: 'typescript', version: '5.9.3'});
  });

  it('registers an act that is not built yet as a placeholder, so the film renders from day one', () => {
    const dir = film(root, 'placeholder', twoActs);
    const r = ok('entry', {film: dir}, {data});
    expect(r.compositions).toEqual(['Act1', 'Act2', 'Film']);
    expect(r.acts[1]).toMatchObject({act: 2, scene: null, frame_in: 15, frames: 15});
    const rootTsx = fs.readFileSync(path.join(dir, '.slate/Root.tsx'), 'utf8');
    // the kit's placeholder (in the brand's look, inside its ActRoot) when the kit is there; the engine's own otherwise
    const use = fs.existsSync(path.join(ENGINE, 'remotion', 'kit', 'index.ts')) ? 'KitPlaceholder' : 'Placeholder';
    expect(rootTsx).toContain(`<${use} sub={S(sub)} act={2} firstFrame={15} />`);
    if (use === 'KitPlaceholder') expect(rootTsx).toContain("import {ActRoot, PlaceholderAct} from '@slate/kit';");
    expect(typecheck(loadFilm(dir), 1, 'x').status).toBe('PASS');
  });

  it('refuses an act without a default export', () => {
    const dir = film(root, 'nodefault', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8').replace('export default function Act1', 'export function Act1'));
    });
    const r = cli('entry', {film: dir}, {data});
    expect(r.code).toBe(2);
    expect(r.json.error).toMatch(/no default export/);
  });

  it('refuses timing built at another frame rate than film.json', () => {
    const dir = film(root, 'rate', (d) => {
      const f = path.join(d, 'film.json');
      writeJson(f, {...readJson(f), fps_num: 25});
    });
    const r = cli('entry', {film: dir}, {data});
    expect(r.code).toBe(2);
    expect(r.json.error).toMatch(/30\/1 fps but film\.json says 25\/1/);
  });

  it("gives @film/data's default export the kit's FilmData keys (snake_case), with the files the film has", () => {
    const dir = film(root, 'data-keys', (d) => {
      writeJson(path.join(d, 'timing/vo_words.json'), {shots: {S01: {line: 'A square', words: [{w: 'A', start: 0, end: 0.1}]}}});
      writeJson(path.join(d, 'timing/vo_cues.json'), {cues: [{shot: 'S01', at: 0.3}]});
      writeJson(path.join(d, '.slate/spoken.json'), {'64': [['sixty-four']]});
    });
    ok('entry', {film: dir}, {data});
    const text = fs.readFileSync(path.join(dir, '.slate/film-data.ts'), 'utf8');
    expect(text).toMatch(/const data = \{film, shots, vo_words: voWords, vo_cues: voCues, spoken, fps, acts, brandDir\};/);
    expect(text).toMatch(/import spokenJson from '\.\/spoken\.json';/);
    expect(text).toMatch(/export const timeline: unknown = null;/);
  });

  it('inlines the brand pack with its logo SVGs as text', () => {
    const brandDir = path.join(root, 'brand-x');
    fs.mkdirSync(brandDir, {recursive: true});
    fs.writeFileSync(path.join(brandDir, 'mark.svg'), '<svg viewBox="0 0 10 10"><rect width="10" height="10"/></svg>');
    writeJson(path.join(brandDir, 'brand.json'), {name: 'brand-x', palette: {ink: '#111111'}, logos: {mark: {path: 'mark.svg', build: {}}, outside: {path: '../escape.svg'}}});
    const dir = film(root, 'brand');
    ok('entry', {film: dir, brand_path: brandDir}, {data});
    const text = fs.readFileSync(path.join(dir, '.slate/film-data.ts'), 'utf8');
    expect(text).toMatch(/"svg":"<svg viewBox=\\"0 0 10 10\\">/);
    // a logo path that climbs out of the brand folder is never read
    expect(text).toMatch(/"outside":\{"path":"\.\.\/escape\.svg"\}/);
    expect(text).toMatch(/const data = \{film, shots, brand, fps, acts, brandDir\};/);
  });

  it.skipIf(!kitCore)("registers a loader for each of the brand's Google Fonts families, by the kit's own font plan", () => {
    const brandDir = path.join(root, 'brand-fonts');
    fs.mkdirSync(brandDir, {recursive: true});
    writeJson(path.join(brandDir, 'brand.json'), {
      name: 'brand-fonts',
      type: {families: [
        {role: 'sans', family: 'Inter', source: 'google', weights: [400, 700], licence: 'OFL'},
        {role: 'mono', family: 'JetBrains Mono', source: {kind: 'google'}, licence: 'OFL'},
        {role: 'display', family: 'Not A Real Google Family', source: 'google', licence: 'OFL'},
        {role: 'serif', family: 'Some Installed Face', source: 'installed', licence: 'commercial'},
      ]},
    });
    const dir = film(root, 'fonts');
    const r = ok('entry', {film: dir, brand_path: brandDir}, {data});
    expect(r.google_fonts.map((g) => [g.family, g.module])).toEqual([
      ['Inter', '@remotion/google-fonts/Inter'],
      ['JetBrains Mono', '@remotion/google-fonts/JetBrainsMono'],
      ['Not A Real Google Family', null],
    ]);
    expect(r.warnings.join('\n')).toMatch(/Not A Real Google Family comes from Google Fonts, but @remotion\/google-fonts has no/);
    const entry = fs.readFileSync(path.join(dir, '.slate/entry.tsx'), 'utf8');
    expect(entry).toContain("import {registerGoogleFont} from '@slate/kit';");
    expect(entry).toContain("import {loadFont as loadFont1} from '@remotion/google-fonts/JetBrainsMono';");
    expect(entry).toContain('registerGoogleFont("Inter", loadFont0);');
    expect(entry.indexOf('registerGoogleFont("JetBrains Mono", loadFont1);')).toBeLessThan(entry.indexOf('registerRoot(Root);'));
    expect(entry).not.toContain('Some Installed Face');
  });

  it.skipIf(!fs.existsSync(path.join(ENGINE, 'remotion', 'kit', 'index.ts')))(
    "registers every act exactly as the kit's actComposition would: one source for length, rate and size",
    async () => {
      const {build} = await import('esbuild');
      const {esbuildOptions} = await import('../../remotion/load_act.mjs');
      const {NEUTRAL_DATA_FILE, NEUTRAL_FILM_DIR} = await import('../../remotion/entry.mjs');
      const outfile = path.join(root, 'kit-acts.mjs');
      const entry = path.join(root, 'kit-acts-entry.mjs');
      fs.writeFileSync(entry, "export {actComposition} from '@slate/kit';\n");
      const opts = esbuildOptions({dir: NEUTRAL_FILM_DIR});
      await build({...opts, alias: {...opts.alias, '@film/data': NEUTRAL_DATA_FILE}, entryPoints: [entry], outfile});
      const {actComposition} = await import(outfile);
      const ctx = loadFilm(NEUTRAL_FILM_DIR);
      for (const a of ctx.acts) {
        for (const sub of [1, 4, 16]) {
          expect(actComposition(a.act, {sub})).toEqual({
            id: `Act${a.act}`,
            durationInFrames: a.frames * sub,
            fps: ctx.fps.value,
            width: ctx.width,
            height: ctx.height,
            defaultProps: {sub},
          });
        }
      }
    },
  );

  it('keeps the neutral @film/data module (engine type check, vitest) current', async () => {
    expect(await writeNeutralData()).toBe(false);
    const ctx = loadFilm(path.join(ENGINE, 'remotion', 'neutral'));
    expect(filmDataSource(ctx, path.join(ENGINE, 'remotion', 'data', 'neutral-film-data.ts'))).toBe(
      fs.readFileSync(path.join(ENGINE, 'remotion', 'data', 'neutral-film-data.ts'), 'utf8'),
    );
  });
});
