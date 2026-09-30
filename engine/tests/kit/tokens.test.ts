// The brand-token adapter: roles from the pack, derived by contrast when missing, and CSS variables.
import {beforeEach, describe, expect, it} from 'vitest';
import {brandTokens, contrast, contrastPairs, cssVars, flattenPalette, luminance, mix, parseColor, setBrand, setFilmData, tokens, typeStyle} from '@slate/kit/core';
import FILM from './fixtures/film-data';
import BRAND from './fixtures/neutral-brand.json';

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

describe('colour maths', () => {
  it('parses the usual spellings', () => {
    expect(parseColor('#abc')).toEqual({r: 170, g: 187, b: 204, a: 1});
    expect(parseColor('#3456A6')).toEqual({r: 52, g: 86, b: 166, a: 1});
    expect(parseColor('rgba(10, 20, 30, 0.5)')).toEqual({r: 10, g: 20, b: 30, a: 0.5});
    expect(parseColor('teal')).toBeNull();
  });
  it('mixes and measures contrast like WCAG', () => {
    expect(mix('#000000', '#FFFFFF', 0.5)).toBe('#808080');
    expect(contrast('#000000', '#FFFFFF')).toBeCloseTo(21, 6);
    expect(luminance('#FFFFFF')).toBeCloseTo(1, 12);
  });
});

describe('palette roles', () => {
  it("takes named roles straight from the pack", () => {
    const t = tokens();
    expect(t.color.page).toBe('#F2F0EB');
    expect(t.color.surface).toBe('#FFFFFF');
    expect(t.color.ink).toBe('#1F1F1D');
    expect(t.color.accent).toBe('#3456A6');
    expect(t.color.inverse).toBe('#1C2233');
    expect(t.color.gray[0]).toBe('#F7F6F3');
    expect(t.color.tones.ok.dot).toBe('#2F7D52');
    expect(t.problems).toEqual([]);
  });

  it('derives what a pack leaves out, with readable contrast', () => {
    const t = brandTokens({palette: {background: '#FAFAF7', text: '#161616', primary: '#6A3FB5'}});
    expect(t.color.page).toBe('#FAFAF7');
    expect(t.color.ink).toBe('#161616');
    expect(t.color.accent).toBe('#6A3FB5');
    expect(contrast(t.color.ink2, t.color.page)).toBeGreaterThanOrEqual(7);
    expect(contrast(t.color.ink3, t.color.page)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t.color.accentInk, t.color.page)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t.color.onAccent, t.color.accent)).toBeGreaterThanOrEqual(3);
    expect(contrast(t.color.onInverse, t.color.inverse)).toBeGreaterThanOrEqual(4.5);
    // no tone colours in the pack: said, not invented
    expect(t.problems.join(' ')).toMatch(/no ok\/warn\/risk tone colours/);
    expect(t.color.tones.ok).toEqual(t.color.tones.neutral);
  });

  it('picks sensible roles from an unnamed palette, and says it did', () => {
    const t = brandTokens({palette: {a: '#101010', b: '#F5F5F0', c: '#D4452B'}});
    expect(t.color.page).toBe('#F5F5F0');
    expect(t.color.ink).toBe('#101010');
    expect(t.color.accent).toBe('#D4452B');
    expect(t.problems.join(' ')).toMatch(/lightest token/);
  });

  it('draws in greys and says so when there is no palette at all', () => {
    const t = brandTokens({});
    expect(t.problems.join(' ')).toMatch(/no palette/);
    expect(contrast(t.color.ink, t.color.page)).toBeGreaterThan(10);
  });

  it('flattens nested palettes', () => {
    expect(flattenPalette({gray: {50: '#fafafa', 900: '#111'}, roles: {page: 'gray.50'}} as never)).toEqual({'gray.50': '#FAFAFA', 'gray.900': '#111111'});
  });
});

describe('type', () => {
  it("builds families from the pack, with generic fallbacks and no font names of the kit's own", () => {
    const t = tokens();
    expect(t.fonts.sans.css).toBe('"Helvetica Neue", sans-serif');
    expect(t.fonts.mono.css).toBe('"Menlo", monospace');
    expect(t.fonts.display.family).toBe('Helvetica Neue');
    const none = brandTokens({});
    expect(none.fonts.sans.css).toBe('sans-serif');
    expect(none.problems.join(' ')).toMatch(/no type families/);
  });

  it('sizes type from the frame, never from a fixed resolution', () => {
    const t = tokens();
    const at540 = typeStyle(t, 'body', 540);
    const at1080 = typeStyle(t, 'body', 1080);
    expect(at1080.fontSize).toBeCloseTo(2 * at540.fontSize, 6);
    expect(at1080.fontSize).toBeGreaterThanOrEqual(28); // body reads at 28 px or more on 1080 lines
    // a heading asks for 700, which the pack declares
    expect(typeStyle(t, 'h1', 540).fontWeight).toBe(700);
    // the mono family only declares 400, so a bold mono role snaps to it (no faux bold)
    expect(t.type.mono.weight).toBe(400);
  });

  it('reads a pack scale given in px against its reference, or as fractions', () => {
    const t = brandTokens({type: {families: [{role: 'sans', family: 'X', source: 'installed'}], scale: {body: {size: 32}}, reference: 1000}});
    expect(t.type.body.size).toBeCloseTo(0.032, 12);
  });
});

describe('CSS variables', () => {
  it('exposes every token', () => {
    const v = cssVars(tokens(), 540);
    expect(v['--slate-color-page']).toBe('#F2F0EB');
    expect(v['--slate-color-accent-ink']).toMatch(/^#/);
    expect(v['--slate-font-sans']).toBe('"Helvetica Neue", sans-serif');
    expect(v['--slate-tone-ok-dot']).toBe('#2F7D52');
    expect(v['--slate-type-body-size']).toMatch(/px$/);
  });
  it('lists contrast pairs in the shape the accessibility gate reads', () => {
    const pairs = contrastPairs(tokens());
    expect(pairs[0]).toMatchObject({fg: '#1F1F1D', bg: '#F2F0EB', role: 'body'});
    expect(pairs.every((p) => p.ratio >= 1)).toBe(true);
  });
  it('follows setBrand() for review sheets', () => {
    setBrand({palette: {page: '#000000', ink: '#FFFFFF', accent: '#FF0000'}});
    expect(tokens().color.page).toBe('#000000');
    setBrand(null);
    expect(tokens().color.page).toBe(BRAND.palette.paper);
  });
});
