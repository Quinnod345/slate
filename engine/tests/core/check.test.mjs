// check <act>: the hand-in check. Every part is a gate result and only PASS counts. The failures the check exists
// for: a type error, a banned word on screen, a missing or stale events file, and act boundaries that don't meet.
import fs from 'node:fs';
import path from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {cli, film, kitCore, ok, readJson, scratch, twoActs, writeJson} from './helpers.mjs';
import {onScreenStrings} from '../../remotion/check.mjs';

let root;
let data;
beforeAll(() => {
  root = scratch('check');
  data = path.join(root, 'data');
});
afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

const byGate = (r) => Object.fromEntries(r.gates.map((g) => [g.gate.split('.').at(-1), g]));

function check(dir, act, extra = {}) {
  return ok('check', {film: dir, act, banned_words: ['gratis', 'no risk'], ...extra}, {data});
}

describe('check: a clean act', () => {
  it('passes every gate, each a gate result with its engine and input hashes', () => {
    const dir = film(root, 'clean');
    ok('emit-events', {film: dir}, {data});
    const r = check(dir, 1);
    expect(r.passed, JSON.stringify(r.gates, null, 1)).toBe(true);
    expect(r.status).toBe('PASS');
    const g = byGate(r);
    expect(Object.keys(g)).toEqual(['typecheck', 'module', 'word_misses', 'banned_words', 'literals', 'events', 'margins', 'contracts']);
    for (const gate of r.gates) {
      expect(Object.keys(gate).sort()).toEqual(['checked_at', 'details', 'engine', 'evidence', 'gate', 'inputs', 'status', 'summary']);
      expect(gate.gate).toMatch(/^handin\.act1\./);
      // filed under the scenes stage by `slate status` ("banned" alone would read as a script gate)
      expect(gate.details.stage).toBe('scenes');
    }
    expect(g.typecheck.engine).toEqual({name: 'typescript', version: '5.9.3'});
    expect(g.typecheck.inputs['scenes/act1/index.tsx']).toMatch(/^[0-9a-f]{64}$/);
    expect(g.events.summary).toBe('2 beats, valid and current');
  });
});

describe('check: failures', () => {
  it('fails the type check on a type error, with file and line', () => {
    const dir = film(root, 'types', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8') + "\nexport const oops: number = 'not a number';\n");
    });
    const g = byGate(check(dir, 1)).typecheck;
    expect(g.status).toBe('FAIL');
    expect(g.evidence[0]).toMatch(/^scenes\/act1\/index\.tsx:\d+:\d+ TS2322 /);
  });

  it("fails on a banned word in the act's on-screen strings or its shots' on-screen text, case-insensitively", () => {
    const dir = film(root, 'banned');
    ok('emit-events', {film: dir}, {data});
    const g = byGate(check(dir, 1, {banned_words: ['steady', 'Crosses the', 'slides']})).banned_words;
    expect(g.status).toBe('FAIL');
    expect(g.evidence).toEqual([
      'scenes/act1/index.tsx:40: "Steady" (steady)',
      'timing/shots.json S02 on_screen: "crosses the frame" (Crosses the)',
    ]);
    // "slides" is only in an event's note, which the sound engine reads and nobody sees
  });

  it('is UNCHECKED, never a pass, when no banned-word list could be read', () => {
    const dir = film(root, 'nobrand');
    const r = ok('check', {film: dir, act: 1}, {data});
    expect(byGate(r).banned_words.status).toBe('UNCHECKED');
    expect(r.passed).toBe(false);
  });

  it('fails when the events file is missing, and when it is out of date with the act', () => {
    const dir = film(root, 'events');
    const missing = byGate(check(dir, 1)).events;
    expect(missing.status).toBe('FAIL');
    expect(missing.summary).toMatch(/no timing\/visual_events\/act1\.json: run `slate emit-events --act 1`/);
    ok('emit-events', {film: dir}, {data});
    const f = path.join(dir, 'timing/visual_events/act1.json');
    const beats = readJson(f);
    beats[0].t = 0.2;
    writeJson(f, beats);
    const stale = byGate(check(dir, 1)).events;
    expect(stale.status).toBe('FAIL');
    expect(stale.summary).toMatch(/out of date with the act/);
  });

  it('fails an events file that breaks the schema', () => {
    const dir = film(root, 'events-bad');
    writeJson(path.join(dir, 'timing/visual_events/act1.json'), {silent: true});
    const g = byGate(check(dir, 1)).events;
    expect(g.status).toBe('FAIL');
    expect(g.summary).toMatch(/a silent act needs a reason/);
  });
});

/** A two-act film whose acts meet on the "Rest" state (scenes/contracts.tsx), or don't. */
function contractFilm(name, {act2Rest = true, act2Declares = 'Rest', act1Declares = 'Rest', withAct2 = true} = {}) {
  return film(root, name, (d) => {
    twoActs(d);
    fs.writeFileSync(
      path.join(d, 'scenes/contracts.tsx'),
      [
        '// The rest state both acts meet on: the square parked in the middle.',
        "import React from 'react';",
        "import {AbsoluteFill} from 'remotion';",
        "import {film} from '@film/data';",
        'export function Rest() {',
        "  return (<AbsoluteFill style={{background: '#101010'}}><div style={{position: 'absolute', left: (film.width - 40) / 2, top: (film.height - 40) / 2, width: 40, height: 40, background: '#f0f0f0'}} /></AbsoluteFill>);",
        '}',
        '',
      ].join('\n'),
    );
    const act = (n, body) => {
      fs.mkdirSync(path.join(d, `scenes/act${n}`), {recursive: true});
      fs.writeFileSync(path.join(d, `scenes/act${n}/index.tsx`), body.join('\n'));
    };
    act(1, [
      "import React from 'react';",
      "import {AbsoluteFill, useCurrentFrame} from 'remotion';",
      "import {film, type SceneProps} from '@film/data';",
      "import {Rest} from '../contracts';",
      act1Declares ? `export const contracts = {out: '${act1Declares}'};` : '',
      "export const silent = 'the square travels quietly';",
      'export default function Act1({sub}: SceneProps) {',
      '  const f = Math.floor(useCurrentFrame() / Math.max(1, Math.round(sub)));',
      '  if (f >= 9) return <Rest />;',
      "  return (<AbsoluteFill style={{background: '#101010'}}><div style={{position: 'absolute', left: ((film.width - 40) / 2) * (f / 9), top: (film.height - 40) / 2, width: 40, height: 40, background: '#f0f0f0'}} /></AbsoluteFill>);",
      '}',
      '',
    ]);
    if (withAct2) {
      act(2, [
        "import React from 'react';",
        "import {AbsoluteFill, useCurrentFrame} from 'remotion';",
        "import {film, type SceneProps} from '@film/data';",
        "import {Rest} from '../contracts';",
        act2Declares ? `export const contracts = {in: '${act2Declares}'};` : '',
        "export const silent = 'the square leaves quietly';",
        'export default function Act2({sub}: SceneProps) {',
        '  const f = Math.floor(useCurrentFrame() / Math.max(1, Math.round(sub)));',
        act2Rest ? '  if (f < 6) return <Rest />;' : '',
        "  return (<AbsoluteFill style={{background: '#101010'}}><div style={{position: 'absolute', left: (film.width - 40) / 2 + (f + 1) * 12, top: (film.height - 40) / 2, width: 40, height: 40, background: '#f0f0f0'}} /></AbsoluteFill>);",
        '}',
        '',
      ]);
    }
  });
}

describe('check: boundary contracts', () => {
  it('passes when both sides render the named rest state bit for bit and hold it 6 frames', () => {
    const dir = contractFilm('meet');
    ok('emit-events', {film: dir}, {data});
    const r = check(dir, 1);
    const g = byGate(r).contracts;
    expect(g.status, g.evidence.join('\n')).toBe('PASS');
    expect(g.summary).toBe('act 1 → act 2: "Rest" matches Act2:0 and Contract-Rest:0 bit for bit, held 6 frames');
    expect(r.passed).toBe(true);
    expect(fs.readdirSync(path.join(dir, 'qa/contracts/act1'))).toContain('Contract-Rest_f00000.png');
  });

  it('checks several acts in one run with one bundle between them, and leaves none behind', () => {
    const dir = contractFilm('both');
    ok('emit-events', {film: dir}, {data});
    const r = cli('check', {film: dir, acts: [1, 2], banned_words: ['gratis']}, {data});
    expect(r.code, r.stderr).toBe(0);
    expect(r.json.acts.map((a) => [a.act, a.status])).toEqual([[1, 'PASS'], [2, 'PASS']]);
    expect(r.json.passed).toBe(true);
    expect(r.json.gates.filter((g) => g.gate.endsWith('.contracts')).map((g) => g.summary)).toEqual([
      'act 1 → act 2: "Rest" matches Act2:0 and Contract-Rest:0 bit for bit, held 6 frames',
      'act 1 → act 2: "Rest" matches Act1:14 and Contract-Rest:0 bit for bit, held 6 frames',
    ]);
    expect(r.stderr.match(/bundling /g)).toHaveLength(1);
    expect(fs.readdirSync(path.join(data, 'cache', 'bundles'))).toEqual([]);
  });

  it("fails when the neighbour's boundary frame isn't the rest state (a mismatch in pixels)", () => {
    const dir = contractFilm('miss', {act2Rest: false});
    const g = byGate(check(dir, 2)).contracts;
    expect(g.status).toBe('FAIL');
    expect(g.evidence.join('\n')).toMatch(/Act2:0 and .* differ \(PSNR [\d.]+ dB\); "Rest" must be bit-identical|frame \d differs from its boundary frame/);
  });

  it('fails, without rendering, when the two sides declare different rest states', () => {
    const dir = contractFilm('names', {act2Declares: 'Elsewhere'});
    const g = byGate(check(dir, 1)).contracts;
    expect(g.status).toBe('FAIL');
    expect(g.summary).toBe('act 1 → act 2: act 1 ends on "Rest" but act 2 starts on "Elsewhere"');
    expect(fs.existsSync(path.join(dir, 'qa/contracts'))).toBe(false);
  });

  it('is UNCHECKED while the neighbour is not built and there is no rest component to compare with', () => {
    const dir = contractFilm('alone', {withAct2: false});
    fs.rmSync(path.join(dir, 'scenes/contracts.tsx'));
    fs.writeFileSync(path.join(dir, 'scenes/act1/index.tsx'), fs.readFileSync(path.join(dir, 'scenes/act1/index.tsx'), 'utf8').replace("import {Rest} from '../contracts';", "const Rest = () => null;"));
    const g = byGate(check(dir, 1)).contracts;
    expect(g.status).toBe('UNCHECKED');
    expect(g.summary).toMatch(/act 2 isn't built yet/);
  });
});

/** contractFilm with act 2 drawn by the open web renderer: its page parks the square (the "Rest" state) for 6 frames. */
function webContractFilm(name, {restLeftNudge = 0} = {}) {
  const dir = contractFilm(name, {withAct2: false});
  const web = path.join(dir, 'scenes/act2/web');
  fs.mkdirSync(web, {recursive: true});
  fs.writeFileSync(
    path.join(web, 'index.html'),
    [
      '<!doctype html>',
      '<html lang="en"><head><meta charset="utf-8"><title>act 2</title>',
      '<style>html, body { margin: 0; padding: 0; overflow: hidden; background: #101010; }',
      '#sq { position: absolute; width: 40px; height: 40px; background: #f0f0f0; }</style></head>',
      '<body><div id="sq"></div><script type="module" src="./scene.ts"></script></body></html>',
      '',
    ].join('\n'),
  );
  fs.writeFileSync(
    path.join(web, 'scene.ts'),
    [
      '// The square parked in the middle (scenes/contracts.tsx\'s Rest) for the first 6 frames, then leaving.',
      "import {frameSize} from '@slate/kit/core';",
      "import {defineScene} from '@slate/web';",
      'const {width, height} = frameSize();',
      'document.body.style.width = `${width}px`;',
      'document.body.style.height = `${height}px`;',
      "const sq = document.getElementById('sq') as HTMLElement;",
      `const rest = (width - 40) / 2 + ${restLeftNudge};`,
      'sq.style.top = `${(height - 40) / 2}px`;',
      'defineScene({',
      "  silent: 'the square leaves quietly',",
      '  fonts: false,',
      '  seek: (_t, info) => {',
      '    sq.style.left = `${info.frame < 6 ? rest : rest + (info.frame + 1) * 12}px`;',
      '  },',
      '});',
      '',
    ].join('\n'),
  );
  return dir;
}

describe("check: a web act's boundary contracts", () => {
  it("holds a web act's first frames to the rest state the act before it ends on, bit for bit", () => {
    const dir = webContractFilm('web-meet');
    const g = byGate(check(dir, 2, {contract_gl: 'swangle'})).contracts;
    expect(g.status, g.evidence.join('\n')).toBe('PASS');
    expect(g.summary).toBe('act 1 → act 2: the web act\'s first 6 frames draw "Rest" exactly as Act1:14 and Contract-Rest:0, held still');
    expect(g.details).toMatchObject({hold: 6, boundaries: 1, checked: 1});
    expect(fs.readdirSync(path.join(dir, 'qa/contracts/act2/web-in'))).toContain('f00000_s00.png');
  });

  it("fails a web act whose boundary frame isn't the neighbour's rest state", () => {
    const dir = webContractFilm('web-miss', {restLeftNudge: 1});
    const g = byGate(check(dir, 2, {contract_gl: 'swangle'})).contracts;
    expect(g.status).toBe('FAIL');
    expect(g.summary).toMatch(/Act2:0 \(the web act\) and Act1:14 differ \(PSNR [\d.]+ dB\); "Rest" must be bit-identical/);
  });
});

describe('check: word misses', () => {
  it.skipIf(!kitCore)("fails while a kit word lookup fell back to an estimate, and the events can't be current", () => {
    const dir = film(root, 'misses');
    ok('emit-events', {film: dir}, {data});
    // a lookup the voice never says, added after the events were written
    fs.writeFileSync(path.join(dir, 'scenes/act1/beats.ts'), "import {word} from '@slate/kit/core';\nexport const said = word('S02', 'square');\n");
    const gates = byGate(check(dir, 1));
    expect(gates.word_misses.status).toBe('FAIL');
    expect(gates.word_misses.evidence).toContain('S02:square');
    expect(gates.events.status).toBe('FAIL');
    expect(gates.events.summary).toMatch(/can't be current: .*fell back to an estimate \(.*S02:square\)/);
    // the brand has no motion law yet: the kit's neutral law stands in, which is noted, not held against the act
    expect(gates.kit_problems.status).toBe('PASS');
    expect(gates.kit_problems.summary).toMatch(/^nothing refused; noted: .*neutral law/);
  });
});

describe('check: what can reach the screen', () => {
  it('fails on a banned word in JSON copy the act imports, and never reads its notes', () => {
    const dir = film(root, 'copy', (d) => {
      writeJson(path.join(d, 'scenes/act1/copy.json'), {id: 'gratis_offer', title: 'Totally gratis'});
      fs.writeFileSync(path.join(d, 'scenes/act1/NOTES.md'), 'Never say gratis on screen.\n');
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(
        f,
        fs.readFileSync(f, 'utf8')
          .replace("import {film, type SceneProps} from '@film/data';", "import {film, type SceneProps} from '@film/data';\nimport copy from './copy.json';")
          .replace('>Steady<', '>{copy.title}<'),
      );
    });
    ok('emit-events', {film: dir}, {data});
    const r = check(dir, 1);
    const g = byGate(r).banned_words;
    expect(g.status).toBe('FAIL');
    expect(g.evidence).toEqual(['scenes/act1/copy.json:3: "Totally gratis" (gratis)']);
    expect(Object.keys(g.inputs)).toEqual(expect.arrayContaining(['scenes/act1/copy.json', 'scenes/act1/index.tsx']));
    expect(byGate(r).typecheck.status).toBe('PASS');
  });

  it('hands in a web scene through the open web renderer: a page that never registers fails, nothing is written', () => {
    const dir = film(root, 'web', (d) => {
      twoActs(d);
      fs.mkdirSync(path.join(d, 'scenes/act2/web'), {recursive: true});
      fs.writeFileSync(path.join(d, 'scenes/act2/web/index.html'), '<!doctype html>\n<title>act 2</title>\n<p>A gratis page</p>\n');
    });
    const r = cli('check', {film: dir, act: 2, banned_words: ['gratis'], load_timeout_ms: 1500, gl: 'swangle'}, {data});
    expect(r.code).toBe(0);
    const g = Object.fromEntries(r.json.gates.map((x) => [x.gate.replace('handin.act2.', ''), x]));
    expect(g.page.status).toBe('FAIL');
    expect(g.page.summary).toMatch(/never registered window\.slateScene/);
    expect(g.word_misses.status).toBe('UNCHECKED');
    expect(g.banned_words.status).toBe('FAIL');
    expect(g.banned_words.evidence).toEqual(['scenes/act2/web/index.html:3: "A gratis page" (gratis)']);
    expect(g.events.status).toBe('FAIL');
    expect(g.events.summary).toMatch(/no timing\/visual_events\/act2\.json/);
    expect(r.json.passed).toBe(false);
    expect(fs.existsSync(path.join(dir, 'timing/visual_events/act2.json'))).toBe(false);
  });

  it('turns a syntax error into failed gates with the file and line, never a crash', () => {
    const dir = film(root, 'syntax', (d) => {
      const f = path.join(d, 'scenes/act1/index.tsx');
      fs.writeFileSync(f, fs.readFileSync(f, 'utf8') + '\nexport const broken = ;\n');
    });
    const r = check(dir, 1);
    const g = byGate(r);
    expect(g.typecheck.status).toBe('FAIL');
    expect(g.typecheck.evidence[0]).toMatch(/^scenes\/act1\/index\.tsx:\d+:\d+ TS1109 /);
    expect(g.module.status).toBe('FAIL');
    expect(g.module.summary).toMatch(/don't build/);
    expect(r.passed).toBe(false);
  });
});

describe('on-screen strings', () => {
  it('reads string literals and JSX text, and skips imports, types, styles, ids, notes and declarations', () => {
    const src = [
      "import x from 'free-package';",
      "type K = 'free' | 'paid';",
      "export const silent = 'free of beats';",
      "export const contracts = {out: 'FreeRest'};",
      "const ev = {id: 'free_id', note: 'free note', kind: 'tap'};",
      "console.log('free log');",
      "const A = () => <div className=\"free\" style={{fontFamily: 'Free Sans'}}>Try it free</div>;",
      "const label = `Free ${x} trial`;",
      "const shown = 'Always free';",
    ].join('\n');
    const texts = onScreenStrings('a.tsx', src).map((s) => s.text.trim());
    expect(texts).toEqual(['Try it free', 'Free   trial', 'Always free']);
  });
});
