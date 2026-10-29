// The font plan and the substitution gate, with a fake measure (the real one runs in the page).
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {GATE_MARKER, brandTokens, checkFace, checkPlan, fontPlan, gateReport, parseReportLine, parseSource, reportLine, setBrand, setFilmData, toGateResult, type Measure} from '@slate/kit/core';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

/**
 * A measure that behaves like a browser: a family it "has" gives its own widths, one it doesn't falls back to the
 * generic's width. Faces may be keyed "family|weight|style" for an exact face, or "family|weight" for both styles.
 */
function fakeMeasure(installed: Record<string, number>): Measure {
  const generic: Record<string, number> = {monospace: 600, serif: 520, 'sans-serif': 500};
  return (font: string) => {
    const m = font.match(/^(normal|italic) (\d+) \d+px (.*)$/);
    if (!m) throw new Error(`bad font string ${font}`);
    const [, style, weight, families] = m;
    for (const fam of families.split(',').map((s) => s.trim().replace(/^"|"$/g, ''))) {
      if (generic[fam] !== undefined) return generic[fam];
      const w = installed[`${fam}|${weight}|${style}`] ?? installed[`${fam}|${weight}`] ?? installed[`${fam}|*`];
      if (w !== undefined) return w;
    }
    return generic['sans-serif'];
  };
}

describe('the plan', () => {
  it("lists the brand's faces with their sources and licences", () => {
    const p = fontPlan();
    expect(p.faces.map((f) => [f.family, f.source.kind, f.weights])).toEqual([
      ['Helvetica Neue', 'installed', [400, 700]],
      ['Menlo', 'installed', [400]],
    ]);
    expect(p.problems).toEqual([]);
  });

  it('reads every kind of source', () => {
    expect(parseSource('installed', 'A')).toEqual({kind: 'installed', name: 'A'});
    expect(parseSource('google', 'Inter')).toEqual({kind: 'google', family: 'Inter'});
    expect(parseSource('https://example.test/kit.css', 'A')).toEqual({kind: 'url', url: 'https://example.test/kit.css'});
    expect(parseSource('https://example.test/a.woff2', 'A')).toEqual({kind: 'file', url: 'https://example.test/a.woff2'});
    expect(parseSource({kind: 'kit', url: 'https://example.test/k.css'}, 'A')).toEqual({kind: 'url', url: 'https://example.test/k.css'});
    expect(parseSource({kind: 'google-fonts', family: 'Inter Tight'}, 'x')).toEqual({kind: 'google', family: 'Inter Tight'});
    expect(parseSource('from a cloud', 'A')).toBeNull();
  });

  it('notes a face with no source or no licence', () => {
    const p = fontPlan({type: {families: [{role: 'sans', family: 'Nameless'}, {role: 'display', family: 'Unlicensed', source: 'installed'}]}});
    expect(p.faces.map((f) => f.family)).toEqual(['Unlicensed']);
    expect(p.problems.join(' ')).toMatch(/Nameless: no usable source/);
    expect(p.problems.join(' ')).toMatch(/Unlicensed: no licence noted/);
  });

  it('merges the weights required by roles sharing a family and source', () => {
    const p = fontPlan({type: {families: [
      {role: 'body', family: 'Fixture Sans', source: 'installed', weights: [400], licence: 'fixture'},
      {role: 'heading', family: 'fixture sans', source: 'local', weights: [590, 400], licence: 'fixture'},
    ]}});
    expect(p.faces).toHaveLength(1);
    expect(p.faces[0]).toMatchObject({family: 'Fixture Sans', weights: [400, 590], styles: ['normal']});
    expect(p.problems).toEqual([]);
    expect(p.errors ?? []).toEqual([]);
  });

  it('checks only the weight and style combinations the roles declare', () => {
    const p = fontPlan({type: {families: [
      {role: 'body', family: 'Fixture Sans', source: 'installed', weights: [400], styles: ['normal'], licence: 'fixture'},
      {role: 'emphasis', family: 'Fixture Sans', source: 'installed', weights: [590], styles: ['italic'], licence: 'fixture'},
    ]}});
    const checks = checkPlan(p, fakeMeasure({'Fixture Sans|400|normal': 540, 'Fixture Sans|590|italic': 560}));
    expect(checks.map((c) => [c.style, c.weight])).toEqual([['normal', 400], ['italic', 590]]);
    expect(gateReport(checks).status).toBe('PASS');
  });

  it('shares default and named weight normalization with the token adapter', () => {
    for (const weights of [undefined, ['regular', 'medium', 'semibold', 'bold', 'black', '510', 'bad', -1, 1001], []]) {
      const pack = {type: {families: [{role: 'sans', family: 'Fixture Sans', source: 'installed', weights, licence: 'fixture'}]}};
      expect(fontPlan(pack).faces[0].weights).toEqual(brandTokens(pack).fonts.sans.weights);
    }
    const p = fontPlan({type: {families: [{family: 'Fixture Sans', source: 'installed', licence: 'fixture'}]}});
    expect(p.faces[0].weights).toEqual([400, 700]);
  });

  it('accepts equivalent file sources with an optional format descriptor', () => {
    const url = 'https://example.test/fixture.woff2';
    const p = fontPlan({type: {families: [
      {role: 'body', family: 'Fixture Sans', source: url, weights: [400], licence: 'fixture'},
      {role: 'heading', family: 'Fixture Sans', source: {kind: 'file', url, format: 'woff2'}, weights: [700], licence: 'fixture'},
    ]}});
    expect(p.faces).toHaveLength(1);
    expect(p.faces[0]).toMatchObject({weights: [400, 700], source: {kind: 'file', url, format: 'woff2'}});
    expect(p.errors ?? []).toEqual([]);
  });

  it('reports incompatible sources and never selects the first source as the family proof', () => {
    const p = fontPlan({type: {families: [
      {role: 'body', family: 'Fixture Sans', source: 'installed', weights: [400], licence: 'fixture'},
      {role: 'heading', family: 'Fixture Sans', source: 'https://example.test/fixture.woff2', weights: [700], licence: 'fixture'},
    ]}});
    expect(p.faces).toEqual([]);
    expect(p.errors).toHaveLength(1);
    expect(p.errors![0]).toMatch(/Fixture Sans: conflicting font sources for roles "body" and "heading"/);
    const report = gateReport(checkPlan(p, fakeMeasure({'Fixture Sans|*': 540})), [], p.problems, p.errors);
    expect(report.status).toBe('FAIL');
    expect(report.summary).toMatch(/source conflict/);
    expect(report.failures).toEqual(p.errors);
  });

  it('keeps a later usable declaration when an earlier role has no source', () => {
    const p = fontPlan({type: {families: [
      {role: 'body', family: 'Fixture Sans', weights: [400], licence: 'fixture'},
      {role: 'heading', family: 'Fixture Sans', source: 'installed', weights: [590], licence: 'fixture'},
    ]}});
    expect(p.faces[0]).toMatchObject({role: 'heading', weights: [590]});
    expect(p.problems.join(' ')).toMatch(/no usable source/);
  });
});

describe('the gate', () => {
  it('passes a face that renders as itself', () => {
    const measure = fakeMeasure({'Helvetica Neue|400': 540, 'Helvetica Neue|700': 570, 'Menlo|400': 610});
    const checks = checkPlan(fontPlan(), measure);
    expect(checks.every((c) => c.ok)).toBe(true);
    expect(checks.some((c) => c.synthetic)).toBe(false);
    const r = gateReport(checks);
    expect(r.status).toBe('PASS');
    expect(r.summary).toMatch(/3 faces of Helvetica Neue, Menlo rendered as declared/);
  });

  it('fails a face the browser substituted', () => {
    const measure = fakeMeasure({'Menlo|400': 610}); // Helvetica Neue isn't there
    const r = gateReport(checkPlan(fontPlan(), measure));
    expect(r.status).toBe('FAIL');
    expect(r.failures.length).toBe(2);
    expect(r.failures[0]).toMatch(/Helvetica Neue 400 did not render/);
  });

  it('passes a face whose widths happen to equal one generic (it still differs from the others)', () => {
    const measure = fakeMeasure({'Twin|400': 500}); // same width as sans-serif, but not as serif or monospace
    expect(checkFace({role: 'sans', family: 'Twin', fallback: 'sans-serif'}, 400, 'normal', measure).ok).toBe(true);
  });

  it('warns when a weight measures exactly like the regular (the browser may be faking it)', () => {
    const measure = fakeMeasure({'Thin|*': 530});
    const c = checkFace({role: 'sans', family: 'Thin', fallback: 'sans-serif'}, 700, 'normal', measure);
    expect(c.ok).toBe(true);
    expect(c.synthetic).toBe(true);
    expect(gateReport([c]).warnings[0]).toMatch(/faking this weight/);
  });

  it('fails on a load error even when the widths look fine', () => {
    const r = gateReport([], ['Inter from Google Fonts failed to load: network']);
    expect(r.status).toBe('FAIL');
  });

  it('fails a missing weight required only by a second role', () => {
    const p = fontPlan({type: {families: [
      {role: 'body', family: 'Fixture Sans', source: 'installed', weights: [400], licence: 'fixture'},
      {role: 'heading', family: 'Fixture Sans', source: 'installed', weights: [590], licence: 'fixture'},
    ]}});
    const report = gateReport(checkPlan(p, fakeMeasure({'Fixture Sans|400': 540})));
    expect(report.status).toBe('FAIL');
    expect(report.failures).toHaveLength(1);
    expect(report.failures[0]).toMatch(/Fixture Sans 590 did not render/);
  });

  it('keeps ordinary onboarding problems as warnings', () => {
    const p = fontPlan({type: {families: [{role: 'sans', family: 'Fixture Sans', source: 'installed', weights: [400]}]}});
    const report = gateReport(checkPlan(p, fakeMeasure({'Fixture Sans|400': 540})), [], p.problems, p.errors);
    expect(report.status).toBe('PASS');
    expect(report.warnings.join(' ')).toMatch(/no licence noted/);
  });

  it('round-trips through the log line the engine reads, and becomes a gate result', () => {
    const r = gateReport(checkPlan(fontPlan(), fakeMeasure({'Menlo|400': 610})));
    const line = `console: ${reportLine(r)} (and then some)`;
    expect(line).toContain(GATE_MARKER);
    const back = parseReportLine(line);
    expect(back).toEqual(r);
    const g = toGateResult(back!, '2026-09-30T12:00:00Z');
    expect(g).toMatchObject({gate: 'fonts', status: 'FAIL', checked_at: '2026-09-30T12:00:00Z'});
    expect(g.evidence.some((e) => e.includes('SUBSTITUTED'))).toBe(true);
    expect(Object.keys(g).sort()).toEqual(['checked_at', 'details', 'engine', 'evidence', 'gate', 'inputs', 'status', 'summary']);
  });
});

describe('the page verdict cache', () => {
  beforeEach(async () => {
    (await import('@slate/kit')).resetFontGate();
    vi.stubGlobal('window', {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('does not reuse a passed plan when identical faces now have a source conflict', async () => {
    const {runFontGate} = await import('@slate/kit');
    const clear = fontPlan({});
    const conflicting = fontPlan({type: {families: [
      {role: 'body', family: 'Fixture Sans', source: 'installed', weights: [400], licence: 'fixture'},
      {role: 'heading', family: 'Fixture Sans', source: 'https://example.test/fixture.woff2', weights: [700], licence: 'fixture'},
    ]}});
    expect(conflicting.faces).toEqual(clear.faces);
    expect((await runFontGate(clear)).status).toBe('PASS');
    const failed = await runFontGate(conflicting);
    expect(failed.status).toBe('FAIL');
    expect(failed.failures.join(' ')).toMatch(/conflicting font sources/);
  });

  it('refreshes ordinary warnings even when the checked faces are unchanged', async () => {
    const {runFontGate} = await import('@slate/kit');
    const clear = fontPlan({});
    expect((await runFontGate(clear)).warnings).toEqual([]);
    const warned = await runFontGate({...clear, problems: ['Fixture licence needs an onboarding note.']});
    expect(warned.status).toBe('PASS');
    expect(warned.warnings).toEqual(['Fixture licence needs an onboarding note.']);
  });

  it('refuses a conflicted render before loading any other declared font', async () => {
    const {holdFirstFrame} = await import('@slate/kit');
    const fontFace = vi.fn(function () { throw new Error('A conflicted plan must not load fonts.'); });
    vi.stubGlobal('FontFace', fontFace);
    const p = fontPlan({type: {families: [
      {role: 'other', family: 'Fixture Other', source: 'https://example.test/other.woff2', weights: [400], licence: 'fixture'},
      {role: 'body', family: 'Fixture Sans', source: 'installed', weights: [400], licence: 'fixture'},
      {role: 'heading', family: 'Fixture Sans', source: 'https://example.test/fixture.woff2', weights: [700], licence: 'fixture'},
    ]}});
    const released = vi.fn();
    const cancelled = vi.fn();
    const hold = holdFirstFrame(p, {delayRender: () => 101, continueRender: released, cancelRender: cancelled});
    expect(await hold.done).toBe(false);
    expect(fontFace).not.toHaveBeenCalled();
    expect(released).not.toHaveBeenCalled();
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(String(cancelled.mock.calls[0][0])).toMatch(/Font gate failed:.*source conflict/);
  });
});

describe('the hold on the first frame', () => {
  // the kit's real module: holdFirstFrame with the Remotion calls and the gate run injected
  const PLAN = {faces: [{family: 'Hold Sans', weights: [400], styles: ['normal']}], problems: []} as never;
  const OTHER = {faces: [{family: 'Other Serif', weights: [700], styles: ['normal']}], problems: []} as never;
  const PASS = {status: 'PASS', summary: '1 face rendered as declared', faces: [], failures: [], warnings: []} as never;
  const FAIL = {status: 'FAIL', summary: 'Hold Sans was substituted', faces: [], failures: ['Hold Sans'], warnings: []} as never;

  function deps(run: () => Promise<unknown>) {
    const calls = {delay: 0, released: [] as number[], failed: [] as unknown[]};
    return {
      calls,
      deps: {
        delayRender: () => 100 + ++calls.delay,
        continueRender: (h: number) => void calls.released.push(h),
        cancelRender: (e: unknown) => void calls.failed.push(e),
        run: run as never,
      },
    };
  }

  beforeEach(async () => (await import('@slate/kit')).resetFontGate());

  it('is taken once per page for a plan, however often a gate renders, and released by the gate itself', async () => {
    const {holdFirstFrame} = await import('@slate/kit');
    let pass!: (r: unknown) => void;
    const {calls, deps: d} = deps(() => new Promise((r) => (pass = r)));
    const first = holdFirstFrame(PLAN, d);
    // a second render, a remount, or a render React threw away: the same hold, never a second one to strand
    expect(holdFirstFrame(PLAN, d)).toBe(first);
    expect(holdFirstFrame(PLAN, d)).toBe(first);
    expect(calls.delay).toBe(1);
    expect(first.settled).toBe(false);
    pass(PASS); // no component effect involved: the gate's own result releases the hold
    expect(await first.done).toBe(true);
    expect(calls.released).toEqual([first.handle]);
    expect(calls.failed).toEqual([]);
    expect(first.settled && first.ok).toBe(true);
    // a gate that mounts after the faces passed starts ready, and takes nothing
    expect(holdFirstFrame(PLAN, d).ok).toBe(true);
    expect(calls.delay).toBe(1);
  });

  it('fails the render once, loudly, when the faces do not pass or the gate throws', async () => {
    const {holdFirstFrame, resetFontGate} = await import('@slate/kit');
    const bad = deps(() => Promise.resolve(FAIL));
    const failed = holdFirstFrame(PLAN, bad.deps);
    expect(await failed.done).toBe(false);
    expect(bad.calls.released).toEqual([]);
    expect(bad.calls.failed).toHaveLength(1);
    expect(String(bad.calls.failed[0])).toMatch(/Font gate failed: Hold Sans was substituted/);
    resetFontGate();
    const broken = deps(() => Promise.reject(new Error('the stylesheet never loaded')));
    const threw = holdFirstFrame(PLAN, broken.deps);
    expect(await threw.done).toBe(false);
    expect(broken.calls.released).toEqual([]);
    expect(String(broken.calls.failed[0])).toMatch(/never loaded/);
  });

  it('holds once per plan, and forgets its holds on reset (a review sheet switching brands)', async () => {
    const {holdFirstFrame, resetFontGate} = await import('@slate/kit');
    const {calls, deps: d} = deps(() => Promise.resolve(PASS));
    const a = holdFirstFrame(PLAN, d);
    const b = holdFirstFrame(OTHER, d);
    expect(b).not.toBe(a);
    expect(calls.delay).toBe(2);
    await Promise.all([a.done, b.done]);
    expect(calls.released.sort()).toEqual([a.handle, b.handle].sort());
    resetFontGate();
    holdFirstFrame(PLAN, d);
    expect(calls.delay).toBe(3);
  });

  it('takes a new failing hold when the same faces acquire a plan error', async () => {
    const {holdFirstFrame} = await import('@slate/kit');
    const good = deps(() => Promise.resolve(PASS));
    const first = holdFirstFrame(PLAN, good.deps);
    expect(await first.done).toBe(true);
    const bad = deps(() => Promise.resolve(FAIL));
    const failed = holdFirstFrame(Object.assign({}, PLAN, {errors: ['Fixture Sans has conflicting font sources.']}) as never, bad.deps);
    expect(failed).not.toBe(first);
    expect(await failed.done).toBe(false);
    expect(bad.calls.delay).toBe(1);
    expect(bad.calls.released).toEqual([]);
    expect(bad.calls.failed).toHaveLength(1);
  });
});
