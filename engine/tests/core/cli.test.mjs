// The JSON contract every engine command keeps: exactly one JSON object on stdout, ok true or false, and Slate's exit
// codes (1 refused, 2 usage, 3 missing prerequisite). Plus the dependency pins the plan fixes (Appendix A).
import fs from 'node:fs';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import {ENGINE, cli, film, ok, readJson, scratch} from './helpers.mjs';

let root;
let data;
beforeAll(() => {
  root = scratch('cli');
  data = path.join(root, 'data');
});
afterAll(() => fs.rmSync(root, {recursive: true, force: true}));

describe('the JSON contract', () => {
  it('version prints one object with the engine version, protocol, commands and pins', () => {
    const v = ok('version', {}, {data});
    expect(v.ok).toBe(true);
    expect(v.engine_version).toBe(readJson(path.join(ENGINE, 'package.json')).version);
    expect(v.protocol).toBe(1);
    expect(Object.keys(v.commands).sort()).toEqual(
      ['browser-ensure', 'bundle', 'check', 'emit-events', 'entry', 'render', 'stills', 'version', 'web-render'].sort(),
    );
    expect(v.installed.remotion).toBe('4.0.521');
    expect(v.pins_ok).toBe(true);
    expect(v.browser.expected_major).toBe(149);
  });

  it('an unknown command is a usage error (exit 2) that lists the commands', () => {
    const r = cli('nope', {}, {data});
    expect(r.code).toBe(2);
    expect(r.json).toMatchObject({ok: false, kind: 'usage', protocol: 1, command: 'nope'});
    expect(r.json.error).toMatch(/render/);
  });

  it('args that are not a JSON object are a usage error', () => {
    for (const bad of ['{not json', '[1, 2]']) {
      const r = spawnSync(process.execPath, [path.join(ENGINE, 'cli.mjs'), 'entry', '--json', bad], {encoding: 'utf8', env: {...process.env, SLATE_DATA: data}});
      expect(r.status).toBe(2);
      const lines = r.stdout.trim().split('\n');
      expect(lines).toHaveLength(1);
      expect(JSON.parse(lines[0])).toMatchObject({ok: false, kind: 'usage'});
    }
  });

  it('a command without a film, or with a folder that is not one, is a usage error', () => {
    expect(cli('entry', {}, {data})).toMatchObject({code: 2, json: {ok: false, kind: 'usage'}});
    const r = cli('entry', {film: path.join(root, 'no-film-here')}, {data});
    expect(r.code).toBe(2);
    expect(r.json.error).toMatch(/film\.json/);
  });

  it('progress goes to stderr, never stdout', () => {
    const dir = film(root, 'progress');
    const r = cli('emit-events', {film: dir}, {data}); // cli() itself asserts one stdout line
    expect(r.code).toBe(0);
    expect(r.stderr).toMatch(/act 1: 2 beats/);
  });
});

describe('stopping cleanly', () => {
  it('stops itself at SLATE_ENGINE_DEADLINE_S with one JSON answer (exit 130), before a caller has to kill it', () => {
    const dir = film(root, 'deadline');
    const r = cli('bundle', {film: dir}, {data, env: {SLATE_ENGINE_DEADLINE_S: '0.2'}});
    expect(r.code).toBe(130);
    expect(r.json).toMatchObject({ok: false, kind: 'stopped', command: 'bundle'});
    expect(r.json.error).toBe('took longer than 0.2 s and was stopped');
    expect(r.json.hint).toMatch(/smaller ranges/);
    const bundles = path.join(data, 'cache', 'bundles');
    expect(fs.existsSync(bundles) ? fs.readdirSync(bundles) : []).toEqual([]);
  });

  it('a bundle stopped halfway (SIGTERM) leaves no half-made bundle behind', async () => {
    const own = path.join(root, 'data-stop');
    const dir = film(root, 'stop');
    const bundles = path.join(own, 'cache', 'bundles');
    const child = spawn(process.execPath, [path.join(ENGINE, 'cli.mjs'), 'bundle', '--json', JSON.stringify({film: dir})], {
      env: {...process.env, SLATE_DATA: own},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    child.stdout.on('data', (d) => (stdout += d));
    const exited = new Promise((resolve) => child.on('close', resolve));
    // wait until webpack is writing the half-made bundle, then stop it the way a timed-out caller would
    const started = Date.now();
    let sawTmp = false;
    while (Date.now() - started < 60_000) {
      if (fs.existsSync(bundles) && fs.readdirSync(bundles).some((n) => n.startsWith('.tmp-'))) {
        sawTmp = true;
        break;
      }
      await new Promise((r) => setTimeout(r, 25));
    }
    expect(sawTmp).toBe(true);
    child.kill('SIGTERM');
    expect(await exited).toBe(130);
    expect(JSON.parse(stdout.trim())).toMatchObject({ok: false, kind: 'stopped', error: 'stopped by SIGTERM'});
    expect(fs.readdirSync(bundles)).toEqual([]);
  });
});

describe('web-render dispatch', () => {
  it('is a missing prerequisite (exit 3) while engine/web/render.mjs is not there', () => {
    const r = cli('web-render', {}, {data, env: {SLATE_WEB_RENDER_MODULE: path.join(root, 'absent.mjs')}});
    expect(r.code).toBe(3);
    expect(r.json).toMatchObject({ok: false, kind: 'missing_prerequisite'});
  });

  it("hands its args to webRender(args) and returns the result; children see BROWSER=none", () => {
    const mod = path.join(root, 'fake-web.mjs');
    fs.writeFileSync(mod, 'export async function webRender(args) { return {frames: args.frames, browser: process.env.BROWSER}; }\n');
    const r = ok('web-render', {frames: 12}, {data, env: {SLATE_WEB_RENDER_MODULE: mod, BROWSER: 'open'}});
    expect(r.frames).toBe(12);
    expect(r.browser).toBe('none');
  });
});

describe('dependency pins (plan appendix A)', () => {
  const pkg = readJson(path.join(ENGINE, 'package.json'));
  const deps = {...pkg.dependencies, ...pkg.devDependencies};

  it('pins Remotion, React, TypeScript and esbuild exactly', () => {
    for (const name of ['remotion', '@remotion/bundler', '@remotion/renderer', '@remotion/cli', '@remotion/google-fonts']) {
      expect(deps[name], name).toBe('4.0.521');
    }
    expect(deps.react).toBe('19.2.8');
    expect(deps['react-dom']).toBe('19.2.8');
    expect(deps.typescript).toBe('5.9.3');
    expect(deps.esbuild).toBe('0.28.1');
  });

  it('drops postprocessing, @react-three/postprocessing and framer-motion, and keeps @remotion/motion-blur out', () => {
    for (const name of ['postprocessing', '@react-three/postprocessing', 'framer-motion', '@remotion/motion-blur']) {
      expect(deps[name], name).toBeUndefined();
    }
  });
});
