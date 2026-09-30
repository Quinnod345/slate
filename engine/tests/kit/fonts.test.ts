// The font plan and the substitution gate, with a fake measure (the real one runs in the page).
import {beforeEach, describe, expect, it} from 'vitest';
import {GATE_MARKER, checkFace, checkPlan, fontPlan, gateReport, parseReportLine, parseSource, reportLine, setBrand, setFilmData, toGateResult, type Measure} from '@slate/kit/core';
import FILM from './fixtures/film-data';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

/**
 * A measure that behaves like a browser: a family it "has" gives its own widths, one it doesn't falls back to the
 * generic's width. Faces are keyed "family|weight".
 */
function fakeMeasure(installed: Record<string, number>): Measure {
  const generic: Record<string, number> = {monospace: 600, serif: 520, 'sans-serif': 500};
  return (font: string) => {
    const m = font.match(/^(normal|italic) (\d+) \d+px (.*)$/);
    if (!m) throw new Error(`bad font string ${font}`);
    const [, , weight, families] = m;
    for (const fam of families.split(',').map((s) => s.trim().replace(/^"|"$/g, ''))) {
      if (generic[fam] !== undefined) return generic[fam];
      const w = installed[`${fam}|${weight}`] ?? installed[`${fam}|*`];
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
