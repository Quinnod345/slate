// Entry generation: one composition per act plus the whole film, '@film/data' in the kit's shape, a tsconfig that
// type-checks from the film folder, placeholders for acts not built yet, and no deep relative imports anywhere.
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
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
    for (const f of ['.slate/entry.tsx', '.slate/Root.tsx', '.slate/act1.tsx', '.slate/film.tsx', '.slate/film-data.ts', '.slate/placeholder.tsx', '.slate/broken.tsx', '.slate/label.tsx', 'tsconfig.json', '.slate/tsconfig.act1.json']) {
      expect(fs.existsSync(path.join(dir, f)), f).toBe(true);
    }
    const rootTsx = fs.readFileSync(path.join(dir, '.slate/Root.tsx'), 'utf8');
    expect(rootTsx).toMatch(/<Composition id="Act1" lazyComponent=\{\(\) => import\('\.\/act1'\)\}/);
    expect(rootTsx).toMatch(/<Composition id="Film" lazyComponent=\{\(\) => import\('\.\/film'\)\}/);
    expect(rootTsx).toMatch(/Do not edit by hand/);
    // sizes and rates are read from film.json through @film/data, never written into the Root
    expect(rootTsx).not.toMatch(/width=\{\d/);
    expect(rootTsx).toMatch(/fps=\{FPS\}/);
    // each act's module holds its scene; the Root imports no scene at all
    expect(rootTsx).not.toMatch(/scenes\//);
    expect(fs.readFileSync(path.join(dir, '.slate/act1.tsx'), 'utf8')).toContain("import Scene from '../scenes/act1';");
  });

  it('never imports up out of the film: scenes reach the kit and data by alias', () => {
    const dir = film(root, 'aliases');
    ok('entry', {film: dir}, {data});
    for (const f of ['.slate/Root.tsx', '.slate/entry.tsx', '.slate/act1.tsx', '.slate/film.tsx', '.slate/film-data.ts', '.slate/placeholder.tsx', '.slate/broken.tsx', '.slate/label.tsx']) {
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

  it("lets anyone run the engine's own tsc from inside the film folder with the generated tsconfig", () => {
    const dir = film(root, 'tsc');
    ok('entry', {film: dir}, {data});
    const tsc = path.join(ENGINE, 'node_modules', 'typescript', 'bin', 'tsc');
    const run = () => spawnSync(process.execPath, [tsc, '-p', 'tsconfig.json'], {cwd: dir, encoding: 'utf8'});
    const clean = run();
    expect(clean.status, clean.stdout + clean.stderr).toBe(0);
    const f = path.join(dir, 'scenes/act1/index.tsx');
    fs.writeFileSync(f, fs.readFileSync(f, 'utf8') + "\nexport const oops: number = 'not a number';\n");
    const broken = run();
    expect(broken.status).not.toBe(0);
    expect(broken.stdout).toMatch(/scenes\/act1\/index\.tsx\(\d+,\d+\): error TS2322/);
  });

  it('registers an act that is not built yet as a placeholder, so the film renders from day one', () => {
    const dir = film(root, 'placeholder', twoActs);
    const r = ok('entry', {film: dir}, {data});
    expect(r.compositions).toEqual(['Act1', 'Act2', 'Film']);
    expect(r.acts[1]).toMatchObject({act: 2, scene: null, frame_in: 15, frames: 15});
    const act2 = fs.readFileSync(path.join(dir, '.slate/act2.tsx'), 'utf8');
    expect(act2).toContain('<Scene sub={S(sub)} act={2} firstFrame={15} />');
    // the kit's placeholder (in the brand's look, inside its ActRoot) when the kit is there; the engine's own otherwise
    if (fs.existsSync(path.join(ENGINE, 'remotion', 'kit', 'index.ts'))) {
      expect(act2).toContain("import {KitPlaceholder as Scene} from './kit-placeholder';");
      expect(fs.readFileSync(path.join(dir, '.slate/kit-placeholder.tsx'), 'utf8')).toContain("import {ActRoot, PlaceholderAct} from '@slate/kit';");
    } else {
      expect(act2).toContain("import {Placeholder as Scene} from './placeholder';");
    }
    expect(typecheck(loadFilm(dir), 1, 'x').status).toBe('PASS');
  });

  it("leaves an act that doesn't build out of the bundle behind a stand-in, and takes it back once it builds", () => {
    const dir = film(root, 'broken', (d) => {
      twoActs(d);
      fs.mkdirSync(path.join(d, 'scenes/act2'), {recursive: true});
      fs.writeFileSync(path.join(d, 'scenes/act2/index.tsx'), "import React from 'react';\nexport default function Act2() {\n  return (<div>half-written\n}\n");
    });
    const r = ok('entry', {film: dir}, {data});
    expect(r.broken).toHaveLength(1);
    expect(r.broken[0]).toMatchObject({act: 2});
    expect(r.broken[0].error).toMatch(/^scenes\/act2\/index\.tsx:\d+:\d+: /);
    expect(r.acts[1].broken).toBe(r.broken[0].error);
    expect(r.warnings.join('\n')).toMatch(/act 2 doesn't build \(scenes\/act2\/index\.tsx:\d+:\d+: .*\): it's left out of the bundle until it does/);
    const act2 = fs.readFileSync(path.join(dir, '.slate/act2.tsx'), 'utf8');
    expect(act2).toContain("import {Broken} from './broken';");
    expect(act2).not.toContain('../scenes/act2');
    expect(fs.readFileSync(path.join(dir, '.slate/film.tsx'), 'utf8')).not.toContain('../scenes/act2');
    // act 1 still imports its scene, and the Root still lists every act
    expect(fs.readFileSync(path.join(dir, '.slate/act1.tsx'), 'utf8')).toContain("import Scene from '../scenes/act1';");
    expect(r.compositions).toEqual(['Act1', 'Act2', 'Film']);
    // fixed: the act is back in
    fs.writeFileSync(path.join(dir, 'scenes/act2/index.tsx'), "import React from 'react';\nexport default function Act2() {\n  return <div>whole</div>;\n}\n");
    const fixed = ok('entry', {film: dir}, {data});
    expect(fixed.broken).toEqual([]);
    expect(fs.readFileSync(path.join(dir, '.slate/act2.tsx'), 'utf8')).toContain("import Scene from '../scenes/act2';");
  });

  it('drops rest-state stills while scenes/contracts.tsx does not build, and says why', () => {
    const dir = film(root, 'contracts-broken', (d) => {
      fs.writeFileSync(path.join(d, 'scenes/contracts.tsx'), "import React from 'react';\nexport function Rest() {\n  return (<div>\n}\n");
    });
    const r = ok('entry', {film: dir}, {data});
    expect(r.contracts).toEqual([]);
    expect(r.contracts_error).toMatch(/^scenes\/contracts\.tsx:\d+:\d+: /);
    expect(r.compositions).toEqual(['Act1', 'Film']);
    expect(fs.existsSync(path.join(dir, '.slate/rest-states.tsx'))).toBe(false);
    expect(r.warnings.join('\n')).toMatch(/scenes\/contracts\.tsx doesn't build .*no rest-state stills until it does/);
  });

  it("removes an act's generated modules when the act leaves timing/shots.json", () => {
    const dir = film(root, 'fewer', twoActs);
    ok('entry', {film: dir}, {data});
    expect(fs.existsSync(path.join(dir, '.slate/act2.tsx'))).toBe(true);
    const f = path.join(dir, 'timing/shots.json');
    const d = readJson(f);
    d.acts = [{index: 1, name: 'One', shots: ['S01', 'S02']}];
    d.shots[1].act = 1;
    writeJson(f, d);
    const r = ok('entry', {film: dir}, {data});
    expect(r.changed).toEqual(expect.arrayContaining(['.slate/act2.tsx', '.slate/tsconfig.act2.json']));
    expect(fs.existsSync(path.join(dir, '.slate/act2.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(dir, '.slate/tsconfig.act2.json'))).toBe(false);
  });

  it("takes an act's component from its default export, or a named Act<N> (the kit README's pattern)", () => {
    const dir = film(root, 'named', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8').replace('export default function Act1', 'export function Act1'));
    });
    const r = ok('entry', {film: dir}, {data});
    expect(r.acts[0]).toMatchObject({act: 1, scene: 'scenes/act1/index.tsx', export: 'Act1'});
    expect(fs.readFileSync(path.join(dir, '.slate/act1.tsx'), 'utf8')).toContain("import {Act1 as Scene} from '../scenes/act1';");
    expect(fs.readFileSync(path.join(dir, '.slate/film.tsx'), 'utf8')).toContain("import {Act1 as Act1Scene} from '../scenes/act1';");
    expect(typecheck(loadFilm(dir), 1, 'x').status).toBe('PASS');
  });

  it('refuses an act module that exports neither a default nor Act<N>', () => {
    const dir = film(root, 'nodefault', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8').replace('export default function Act1', 'export function Scene'));
    });
    const r = cli('entry', {film: dir}, {data});
    expect(r.code).toBe(2);
    expect(r.json.error).toMatch(/exports no component: neither a default export nor Act1/);
  });

  it('writes the entry for an act with a syntax error, so the type check can name the line', () => {
    const dir = film(root, 'syntax', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8').replace('return (', 'return ((('));
    });
    const r = ok('entry', {film: dir}, {data});
    expect(r.acts[0].scene).toBe('scenes/act1/index.tsx');
    const g = typecheck(loadFilm(dir), 1, 'x');
    expect(g.status).toBe('FAIL');
    expect(g.evidence[0]).toMatch(/^scenes\/act1\/index\.tsx:\d+:\d+ TS\d+ /);
  });

  it('says so when an act folder has sources but no index.tsx, instead of quietly using a placeholder', () => {
    const dir = film(root, 'noindex', (d) => {
      twoActs(d);
      fs.mkdirSync(path.join(d, 'scenes/act2'), {recursive: true});
      fs.writeFileSync(path.join(d, 'scenes/act2/Act2.tsx'), 'export default function Act2() { return null; }\n');
    });
    const r = ok('entry', {film: dir}, {data});
    expect(r.acts[1]).toMatchObject({act: 2, scene: null});
    expect(r.warnings.join('\n')).toMatch(/scenes\/act2 has Act2\.tsx but no index\.tsx, the act's entry point: act 2 renders as a placeholder/);
    const one = cli('emit-events', {film: dir, act: 2}, {data});
    expect(one.code).toBe(2);
    expect(one.json.error).toMatch(/Act 2 isn't built yet \(scenes\/act2 has Act2\.tsx but no index\.tsx/);
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
