#!/usr/bin/env node
// Run Slate's ExtendScript in the mock After Effects (ae_mock.mjs): the offline stand-in for osascript + AE that the
// Python tests use, so a build, an audit or a smoke run goes end to end (Python spec -> ES3 script -> report) without
// After Effects.
//
//   node engine/ae/tests/mock_run.mjs --session <session.json>
//
// session.json: {state: {media, fonts, missingFonts, prefs, dirtyProject, omTemplates, rsTemplates},
//                runs: [{wrapper: "<the wrapper slate.ae.driver wrote>"}]}
// The runs share one mock AE session, in order (a build, then its audit). Prints one JSON object:
// {runs: [{status, run_id, settings, report_file, error}], saves, discarded, flushes, purged}.

import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {makeAE} from './ae_mock.mjs';
import {loadJsx} from './jsx_loader.mjs';

/** Pull the args, run info and target out of a wrapper written by slate.ae.driver.wrapper_source. */
export function parseWrapper(path) {
  const src = readFileSync(path, 'utf8');
  const grab = (re) => {
    const m = re.exec(src);
    if (!m) throw new Error(`not a Slate wrapper: ${path}`);
    return JSON.parse(m[1]);
  };
  return {
    args: grab(/\$\.global\.SLATE_ARGS = (.*);\n/),
    run: grab(/\$\.global\.SLATE_RUN = (.*);\n/),
    target: grab(/\$\.evalFile\(new File\((.*)\)\);/),
  };
}

export function runSession(session) {
  const ae = makeAE(session.state || {});
  const out = {runs: []};
  for (const r of session.runs || []) {
    const w = parseWrapper(r.wrapper);
    const ctx = {console};
    ctx.$ = {global: ctx, fileName: w.target, stack: '', writeln: () => {}, sleep: () => {}};
    Object.assign(ctx, ae.globals);
    ctx.SLATE_ARGS = w.args;
    ctx.SLATE_RUN = w.run;
    vm.createContext(ctx);
    let error = null;
    try {
      new vm.Script(loadJsx(w.target), {filename: w.target}).runInContext(ctx);
    } catch (e) {
      error = String(e && e.stack ? e.stack : e);
    }
    const section = ae.env.settings[w.run.section || 'Slate'] || {};
    const settings = {};
    for (const [k, v] of Object.entries(section)) if (k.startsWith(`${w.run.channel}.`)) settings[k] = v;
    out.runs.push({run_id: w.run.run_id, status: settings[`${w.run.channel}.status`] || null, settings,
      report_file: w.run.report_file, error});
  }
  out.saves = ae.env.saves;
  out.discarded = ae.env.discarded;
  out.flushes = ae.env.flushes;
  out.purged = ae.env.purged;
  return out;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const i = process.argv.indexOf('--session');
  if (i < 0) {
    process.stderr.write('usage: mock_run.mjs --session <session.json>\n');
    process.exit(2);
  }
  const session = JSON.parse(readFileSync(process.argv[i + 1], 'utf8'));
  process.stdout.write(JSON.stringify(runSession(session)) + '\n');
}
