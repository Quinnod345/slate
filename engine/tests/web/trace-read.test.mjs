// Read one real local web page without encoding media. The raw registry and its consumed source bytes must reach
// the caller on the film clock; a dry read must not replace an existing act's events or motion.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it, vi} from 'vitest';
import {sha256Buffer} from '../../remotion/lib/common.mjs';
import {loadFilm} from '../../remotion/lib/film.mjs';
import {filmDataFor, webRender} from '../../web/render.mjs';
import {BRAND, ENGINE, SCENES, alive, film, readJson, scratch} from './helpers.mjs';

let root;

beforeAll(() => {
  root = scratch('web-trace-read');
  for (const [key, value] of Object.entries({
    SLATE_DATA: path.join(root, 'data'),
    SLATE_FILMS: path.join(root, 'films'),
    SLATE_BRANDS: path.join(root, 'brands'),
    SLATE_FAKE_VENDORS: '1',
    BROWSER: 'none',
  })) vi.stubEnv(key, value);
});

afterAll(() => {
  vi.unstubAllEnvs();
  fs.rmSync(root, {recursive: true, force: true});
});

function fileHashes(dir) {
  const hashes = {};
  const visit = (current) => {
    for (const entry of fs.readdirSync(current, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(current, entry.name);
      if (entry.isDirectory()) visit(file);
      else hashes[path.relative(dir, file)] = sha256Buffer(fs.readFileSync(file));
    }
  };
  visit(dir);
  return hashes;
}

describe('the explicit raw web-shot trace read', () => {
  it('returns real film-frame rows and consumed sources, writes nothing, and closes its browser', async () => {
    const f = film(root, {name: 'trace-read'});
    const timingFile = path.join(f, 'timing', 'shots.json');
    const timing = readJson(timingFile);
    const offset = timing.total_frames;
    const fps = readJson(path.join(f, 'film.json'));
    const lead = timing.shots.map((s, i) => ({...s, id: `A${String(i + 1).padStart(2, '0')}`}));
    const detail = timing.shots.map((s) => ({
      ...s, act: 2,
      frame_in: s.frame_in + offset, frame_out: s.frame_out + offset,
      sec_in: ((s.frame_in + offset) * fps.fps_den) / fps.fps_num,
      sec_out: ((s.frame_out + offset) * fps.fps_den) / fps.fps_num,
      ...(s.id === 'W01' ? {engine: 'web'} : {}),
    }));
    fs.writeFileSync(timingFile, JSON.stringify({
      ...timing, total_frames: offset * 2,
      acts: [{index: 1, name: 'Before', shots: lead.map((s) => s.id)},
        {index: 2, name: 'Trace read', shots: detail.map((s) => s.id)}],
      shots: [...lead, ...detail],
    }));

    const scene = path.join(f, 'scenes', 'shots', 'W01', 'web');
    fs.cpSync(path.join(SCENES, 'tween'), scene, {recursive: true});
    fs.writeFileSync(path.join(scene, 'offsets.ts'),
      "import {actSpan} from '@slate/kit/core';\n" +
      'export const startFrame = actSpan(2)!.frameIn + 5;\n' +
      'export const endFrame = actSpan(2)!.frameIn + 15;\n');
    fs.writeFileSync(path.join(scene, 'style.css'), '#bar { outline: 1px solid white; }\n');
    const sourceFile = path.join(scene, 'scene.ts');
    const source = fs.readFileSync(sourceFile, 'utf8');
    expect(source).toContain('frame_in: 5, frame_out: 15');
    expect(source).toContain('t: secOf(15)');
    expect(source).toContain('defineScene();');
    fs.writeFileSync(sourceFile,
      "import './style.css';\nimport {startFrame, endFrame} from './offsets';\n" +
      source.replace('frame_in: 5, frame_out: 15', 'frame_in: startFrame, frame_out: endFrame')
        .replace('t: secOf(15)', 't: secOf(endFrame)')
        .replace('defineScene();', `const scene = defineScene();
const registeredTrace = scene.trace.bind(scene);
// A deliberately wrong-act row must remain visible in the raw read. The mixed composer must judge it later;
// the producer cannot silently filter it into apparent successful coverage.
scene.trace = () => {
  const rows = registeredTrace();
  return [...rows, {...rows[0], id: 'raw.other.act', shot: 'A01'}];
};`));

    const eventFile = path.join(f, 'timing', 'visual_events', 'act2.json');
    const motionFile = path.join(f, 'timing', 'motion', 'act2.json');
    for (const file of [eventFile, motionFile]) fs.mkdirSync(path.dirname(file), {recursive: true});
    const previousEvents = Buffer.from('[{"id":"previous.beat","shot":"W01","t":2.4,"kind":"land","size":"s"}]\n');
    const previousMotion = Buffer.from('[{"id":"previous.move","shot":"W01","prop":"x","frame_in":50,"frame_out":51,"samples":[[50,0],[51,1]]}]\n');
    fs.writeFileSync(eventFile, previousEvents);
    fs.writeFileSync(motionFile, previousMotion);

    const requiredSources = [
      path.join(f, 'film.json'), timingFile, path.join(BRAND, 'brand.json'),
      path.join(scene, 'index.html'), sourceFile, path.join(scene, 'offsets.ts'), path.join(scene, 'style.css'),
      path.join(ENGINE, 'web', 'runtime', 'motion.ts'),
    ];
    const expectedHashes = new Map(requiredSources.map((file) => [
      fs.realpathSync(file), sha256Buffer(fs.readFileSync(file)),
    ]));
    const before = fileHashes(f);
    const expectedFilmData = sha256Buffer(Buffer.from(filmDataFor(loadFilm(f), readJson(path.join(BRAND, 'brand.json')), BRAND).contents));
    const result = await webRender({
      film: f, act: 2, scene, emit: true, dry_run: true, trace: false, trace_rows: true,
      brand_path: BRAND, gl: 'swangle',
    }, {cwd: root});

    expect(result.skipped).toEqual([]);
    expect(result.dry_run).toBe(true);
    expect(result.acts).toHaveLength(1);
    const summary = result.acts[0];
    expect(summary).toMatchObject({act: 2, engine: 'web', dry_run: true, written: false, events: 1});
    expect(summary.problems).toEqual([]);
    expect(summary.scene_problems).toEqual([]);
    expect(summary.motion).toMatchObject({written: false, entries: 2, reason: 'dry run'});
    expect(summary.beats[0]).toMatchObject({id: 'bar.lands', shot: 'W01', t: ((offset + 15) * fps.fps_den) / fps.fps_num});
    const trace = summary.trace_read;
    expect(trace).toMatchObject({
      frame_base: 'film', act: 2, act_frames: [offset, offset * 2],
      fps_num: fps.fps_num, fps_den: fps.fps_den, reason: null, film_data_sha256: expectedFilmData,
    });
    expect(trace.rows).toHaveLength(2);
    const row = trace.rows.find((entry) => entry.id === 'bar.slide');
    expect(row).toMatchObject({shot: 'W01', prop: 'x', frame_in: offset + 5, frame_out: offset + 15, from: 0, to: 200});
    expect(row.samples.map(([frame]) => frame)).toEqual(Array.from({length: 11}, (_, n) => offset + 5 + n));
    expect(row.samples.every(([, value]) => typeof value === 'number' && Number.isFinite(value))).toBe(true);
    expect(row.samples[0]).toEqual([offset + 5, 0]);
    expect(row.samples.at(-1)).toEqual([offset + 15, 200]);
    expect(trace.rows.find((entry) => entry.id === 'raw.other.act')).toMatchObject({shot: 'A01', samples: row.samples});

    expect(summary.sources).toEqual(Object.keys(summary.source_hashes).sort());
    const observed = new Map();
    for (const [name, digest] of Object.entries(summary.source_hashes)) {
      expect(digest).toMatch(/^[0-9a-f]{64}$/);
      const file = fs.realpathSync(path.resolve(f, name));
      expect(sha256Buffer(fs.readFileSync(file))).toBe(digest);
      observed.set(file, digest);
    }
    for (const [file, digest] of expectedHashes) expect(observed.get(file), file).toBe(digest);
    expect(fileHashes(f)).toEqual(before);
    expect(fs.readFileSync(eventFile)).toEqual(previousEvents);
    expect(fs.readFileSync(motionFile)).toEqual(previousMotion);
    expect(fs.existsSync(path.join(f, 'timing', 'motion', 'shots'))).toBe(false);
    expect(fs.existsSync(path.join(f, 'renders'))).toBe(false);
    expect(Number.isInteger(result.browser.pid) && result.browser.pid > 0).toBe(true);
    expect(alive(result.browser.pid)).toBe(false);
    expect(typeof result.browser.profile).toBe('string');
    expect(fs.existsSync(result.browser.profile)).toBe(false);
  });

  it.each([
    ['string flag', {trace_rows: 'true'}, /"trace_rows" must be a boolean/],
    ['numeric flag', {trace_rows: 1}, /"trace_rows" must be a boolean/],
    ['null flag', {trace_rows: null}, /"trace_rows" must be a boolean/],
    ['no film', {film: undefined}, /"trace_rows" is a read-only film-shot operation/],
    ['render mode', {emit: false}, /"trace_rows" is a read-only film-shot operation/],
    ['write mode', {dry_run: false}, /"trace_rows" is a read-only film-shot operation/],
    ['implicit scene', {scene: undefined}, /"trace_rows" is a read-only film-shot operation/],
  ])('refuses %s before film resolution or browser work', async (_name, changed, reason) => {
    // Neither path exists. A different prerequisite error would prove the option guard ran too late.
    const before = fileHashes(root);
    await expect(webRender({
      film: path.join(root, 'missing-film'), scene: path.join(root, 'missing-scene'), act: 2,
      emit: true, dry_run: true, trace_rows: true, ...changed,
    }, {cwd: root})).rejects.toThrow(reason);
    expect(fileHashes(root)).toEqual(before);
  });
});
