// A kit-built act, end to end through the engine, the way `slate` drives it: the generated entry (with '@film/data'
// carrying the film's reads, their tokens, the language's match rules and the brand), one bundle for the film, a
// 10-frame render_mb render at 4 samples into ProRes 422 HQ with the exact frame count, stills, emit-events writing
// both the visual events and the kit's motion trace, and the hand-in check. The act (fixtures/kit-film/scenes/act1)
// imports '@slate/kit' for real: ActRoot, the font gate, tokens, word lookups, defineMotion and defineEvent.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {HERE, cli, kitCore, ok, probe, readJson, scratch} from './helpers.mjs';

const KIT_FILM = path.join(HERE, 'fixtures', 'kit-film');
const KIT_BRAND = path.join(HERE, 'fixtures', 'kit-brand');
// the language's match rules exactly as the Python bridge writes them (tests/engine_kit/test_word_time_parity.py)
const LANG_RULES = path.join(HERE, '..', 'kit', 'fixtures', 'word-times.json');

let root;
let data;
let dir;
let bundle = null;

/** A call the way slate.engine.bridge makes it: the film and its brand pack, every time. */
const call = (cmd, args = {}) => ok(cmd, {film: dir, brand_path: KIT_BRAND, ...args}, {data});

/** Every file under a folder, as text, for looking inside the bundle. */
function textOf(folder) {
  let out = '';
  for (const entry of fs.readdirSync(folder, {withFileTypes: true, recursive: true})) {
    if (!entry.isFile() || !/\.js$/.test(entry.name)) continue;
    out += fs.readFileSync(path.join(entry.parentPath ?? entry.path, entry.name), 'utf8');
  }
  return out;
}

beforeAll(() => {
  root = scratch('kit-e2e');
  data = path.join(root, 'data');
  dir = path.join(root, 'kit-e2e');
  fs.cpSync(KIT_FILM, dir, {recursive: true});
  // what the Python bridge writes before every call: the language's word-matching rules, from slate.timing's table
  fs.mkdirSync(path.join(dir, '.slate'), {recursive: true});
  fs.writeFileSync(path.join(dir, '.slate', 'lang.json'), JSON.stringify(readJson(LANG_RULES).lang) + '\n');
  ok('browser-ensure', {}, {data});
});

afterAll(() => {
  if (bundle) cli('bundle', {film: dir, brand_path: KIT_BRAND, serve_url: bundle.serve_url, release: true}, {data});
  fs.rmSync(root, {recursive: true, force: true});
});

describe.skipIf(!kitCore)('a kit-built act, end to end through the engine', () => {
  it("generates the entry: act 1 from its scene, act 2 a placeholder, '@film/data' with tokens, rules and brand", () => {
    const e = call('entry');
    expect(e.acts.map((a) => [a.act, a.scene, a.frames])).toEqual([[1, 'scenes/act1/index.tsx', 10], [2, null, 10]]);
    expect(e.broken).toEqual([]);
    const source = fs.readFileSync(path.join(dir, '.slate', 'film-data.ts'), 'utf8');
    expect(source).toMatch(/import voWordsJson from '\.\.\/timing\/vo_words\.json';/);
    expect(source).toMatch(/import langJson from '\.\/lang\.json';/);
    expect(source).toMatch(/export const brand: JsonObject \| null = \{"name":"kit-e2e-brand"/);
    expect(source).toMatch(/const data = \{film, shots, vo_words: voWords, vo_cues: voCues, lang, brand, fps, acts, brandDir\};/);
  });

  it('makes one bundle holding the act and the real kit (its font gate and clock), not a stand-in', () => {
    bundle = call('bundle');
    expect(bundle.compositions).toEqual(expect.arrayContaining(['Act1', 'Act2', 'Film']));
    expect(bundle.broken).toEqual([]);
    const js = textOf(bundle.serve_url);
    expect(js).toContain('e2e.card-rise'); // the act's own beats module
    expect(js).toContain('SLATE_FONT_GATE'); // the kit's font gate (fonts.tsx)
    expect(js).toContain('in the script line but not heard in the take'); // the kit's clock (core/film.ts)
  });

  it('renders 10 frames at 4 samples into ProRes 422 HQ 10-bit BT.709, exactly 10 frames, fonts gated', () => {
    const out = path.join(root, 'renders', 'act1.mov');
    const r = call('render', {act: 1, sub: 4, out, serve_url: bundle.serve_url, gl: 'swangle', concurrency: 2, trace: true});
    expect(r).toMatchObject({comp: 'Act1', act: 1, frames: [0, 9], film_frames: [0, 10], frame_count: 10, samples: 4, codec: 'prores', fps: '25/1', bundle_hash: bundle.bundle_hash});
    expect(probe(out)).toMatchObject({
      codec_name: 'prores',
      profile: 'HQ',
      pix_fmt: 'yuv422p10le',
      width: 320,
      height: 180,
      color_space: 'bt709',
      color_transfer: 'bt709',
      color_primaries: 'bt709',
      r_frame_rate: '25/1',
      nb_read_packets: '10',
    });
    // the kit's font gate ran in the page, on the brand's installed face
    const fonts = r.gates.find((g) => g.gate === 'fonts');
    expect(fonts?.status, JSON.stringify(r.gates)).toBe('PASS');
    // the trace read the act against the same '@film/data' the picture rendered with: the brand's own eases
    expect(r.trace).toEqual({written: true, file: 'timing/motion/act1.json', entries: 2});
    const rows = readJson(path.join(dir, 'timing', 'motion', 'act1.json'));
    expect(rows.map((m) => [m.id, m.ease])).toEqual([['e2e.card-rise', 'enter'], ['e2e.dot-cross', 'glide']]);
    expect(fs.readFileSync(path.join(dir, '.slate', 'film-data.ts'), 'utf8')).toMatch(/"name":"kit-e2e-brand"/);
  });

  it('draws the moves on the frames the beats say: the dot has crossed by the last frame', () => {
    const outDir = path.join(root, 'stills');
    const s = call('stills', {specs: ['Act1:5', 'Act1:9'], scale: 1, out_dir: outDir, serve_url: bundle.serve_url, gl: 'swangle', pixels: true});
    expect(s.stills.map((x) => path.basename(x.file))).toEqual(['Act1_f00005.png', 'Act1_f00009.png']);
    expect(s.stills[0].pixels).not.toBe(s.stills[1].pixels);
    expect(s.gates.find((g) => g.gate === 'fonts')?.status).toBe('PASS');
  });

  it('emits the visual events and the motion trace from the kit, on the words the voice says', () => {
    const r = call('emit-events', {act: 1});
    const [a] = r.acts;
    expect(a).toMatchObject({act: 1, file: 'timing/visual_events/act1.json', silent: false, events: 2, kinds: {land: 1, whoosh: 1}});
    expect(a.motion).toEqual({written: true, file: 'timing/motion/act1.json', entries: 2});
    const events = readJson(path.join(dir, 'timing', 'visual_events', 'act1.json'));
    // "clock" starts 0.08 s into E01's read (cue 0.0); "64", said "sixty four", opens E02's read at 0.2 s
    expect(events.map((e) => [e.id, e.shot, e.kind, Math.round(e.t * 1e6) / 1e6])).toEqual([
      ['card_lands', 'E01', 'land', 0.08],
      ['dot_sets_off', 'E02', 'whoosh', 0.2],
    ]);
    const rows = readJson(path.join(dir, 'timing', 'motion', 'act1.json'));
    for (const row of rows) {
      expect(Object.keys(row).sort()).toEqual(['ease', 'entrance', 'frame_in', 'frame_out', 'from', 'id', 'loop_period', 'prop', 'rig', 'samples', 'shot', 'to']);
    }
    expect(rows[1]).toMatchObject({id: 'e2e.dot-cross', shot: 'E02', prop: 'x', frame_in: 5, frame_out: 9, from: 40, to: 260, entrance: false});
    expect(rows[1].samples.map(([f]) => f)).toEqual([5, 6, 7, 8, 9]);
    expect(rows[1].samples.at(-1)).toEqual([9, 260]);
  });

  it('passes the hand-in check: types, module, word misses, kit problems, banned words, events, contracts', () => {
    const r = call('check', {act: 1, banned_words: ['guaranteed', 'risk-free']});
    const byName = Object.fromEntries(r.gates.map((g) => [g.gate.split('.').slice(2).join('.'), g]));
    expect(Object.keys(byName)).toEqual(['typecheck', 'module', 'word_misses', 'kit_problems', 'banned_words', 'literals', 'events', 'margins', 'contracts']);
    expect(r.passed, JSON.stringify(r.gates.filter((g) => g.status !== 'PASS'), null, 1)).toBe(true);
    expect(byName.module.summary).toMatch(/with the kit/);
    expect(byName.word_misses.summary).toBe('every word the act looks up was found in the timings');
    expect(byName.events.summary).toBe('2 beats, valid and current');
    expect(byName.banned_words.summary).toMatch(/none of the brand's 2 banned words/);
  });

  it('refuses the hand-in once a looked-up word is no longer in the read', () => {
    const beats = path.join(dir, 'scenes', 'act1', 'beats.ts');
    const before = fs.readFileSync(beats, 'utf8');
    try {
      fs.writeFileSync(beats, before.replace("word('E01', 'clock')", "word('E01', 'watch')"));
      const r = cli('check', {film: dir, brand_path: KIT_BRAND, act: 1, banned_words: [], contracts: false}, {data});
      expect(r.code).toBe(0);
      const misses = r.json.gates.find((g) => g.gate === 'handin.act1.word_misses');
      expect(misses.status).toBe('FAIL');
      expect(misses.summary).toMatch(/E01:watch/);
      expect(r.json.passed).toBe(false);
    } finally {
      fs.writeFileSync(beats, before);
    }
  });

  it("hands in a web act through the open web renderer: its page opens once, nothing is written, every gate passes", () => {
    const made = ok('web-render', {film: dir, act: 2, scaffold: true}, {data});
    expect(made).toMatchObject({scaffolded: true, act: 2});
    // a dry run reads the page's beats and writes nothing; Chrome's profile lives under Slate's data folder
    const dry = call('emit-events', {act: 2, dry_run: true, gl: 'swangle'});
    const [d] = dry.acts;
    expect(d).toMatchObject({act: 2, engine: 'web', dry_run: true, written: false, events: 1, problems: [], misses: []});
    expect(d.beats.map((b) => b.id)).toEqual(['template.line-lands']);
    expect(fs.existsSync(path.join(dir, 'timing', 'visual_events', 'act2.json'))).toBe(false);
    // the page's Chrome kept its profile under Slate's data folder (never $TMPDIR), and it's gone with the browser
    expect(d.browser.profile.startsWith(path.join(data, 'cache', 'chrome') + path.sep)).toBe(true);
    expect(fs.existsSync(d.browser.profile)).toBe(false);
    const emitted = call('emit-events', {act: 2, gl: 'swangle'});
    expect(emitted.acts[0]).toMatchObject({act: 2, engine: 'web', events: 1});
    const r = call('check', {act: 2, banned_words: ['guaranteed', 'risk-free'], gl: 'swangle'});
    const byName = Object.fromEntries(r.gates.map((g) => [g.gate.split('.').slice(2).join('.'), g]));
    expect(Object.keys(byName).sort()).toEqual(['banned_words', 'contracts', 'events', 'fonts', 'kit_problems', 'literals', 'network', 'page', 'typecheck', 'word_misses']);
    expect(r.passed, JSON.stringify(r.gates.filter((g) => g.status !== 'PASS'), null, 1)).toBe(true);
    expect(byName.events.summary).toBe('1 beats, valid and current with the page');
    expect(byName.banned_words.summary).toMatch(/none of the brand's 2 banned words in \d+ on-screen strings/);
    expect(byName.typecheck.summary).toMatch(/no type errors in \d+ scene file/);
  });

  it("fails a web act's hand-in on a banned word in its page, and when its beats file is out of date", () => {
    const page = path.join(dir, 'scenes', 'act2', 'web', 'index.html');
    const before = fs.readFileSync(page, 'utf8');
    const eventsFile = path.join(dir, 'timing', 'visual_events', 'act2.json');
    const beats = readJson(eventsFile);
    try {
      fs.writeFileSync(page, before.replace('</body>', '<p class="small">Results guaranteed.</p></body>'));
      fs.writeFileSync(eventsFile, JSON.stringify(beats.map((b) => ({...b, t: b.t + 0.04}))));
      const r = cli('check', {film: dir, brand_path: KIT_BRAND, act: 2, banned_words: ['guaranteed'], contracts: false, gl: 'swangle'}, {data});
      expect(r.code).toBe(0);
      const gate = (n) => r.json.gates.find((g) => g.gate === `handin.act2.${n}`);
      expect(gate('banned_words').status).toBe('FAIL');
      expect(gate('banned_words').summary).toMatch(/index\.html:\d+: "Results guaranteed\." \(guaranteed\)/);
      expect(gate('events').status).toBe('FAIL');
      expect(gate('events').summary).toMatch(/out of date with the page/);
      expect(r.json.passed).toBe(false);
    } finally {
      fs.writeFileSync(page, before);
      fs.writeFileSync(eventsFile, JSON.stringify(beats, null, 1) + '\n');
    }
  });
});
