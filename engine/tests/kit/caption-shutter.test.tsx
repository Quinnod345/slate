import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {beforeEach, describe, expect, it} from 'vitest';
import {Captions, KitProvider, filmFrameOf, secOf, setBrand, setFilmData, type Cue} from '@slate/kit';
import FILM from './fixtures/film-data';

const rates = [{num: 25, den: 1}, {num: 30000, den: 1001}, {num: 24000, den: 1001}];
const cases = rates.flatMap((rate) =>
  [4, 16, 64].flatMap((samples) =>
    [180, 360].map((shutter) => ({...rate, rate: rate.num + '/' + rate.den, samples, shutter})),
  ),
);

beforeEach(() => {
  setFilmData(FILM as never);
  setBrand(null);
});

function cuesAt(boundary: number): Cue[] {
  return [
    {start: 0, end: boundary, text: 'Leaving words', lines: ['Leaving words'], shot: null},
    {start: boundary, end: boundary + secOf(100), text: 'Arriving words', lines: ['Arriving words'], shot: null},
  ];
}

function markup(frame: number, sub: number, cues?: Cue[], t?: number): string {
  return renderToStaticMarkup(
    <KitProvider frame={frame} sub={sub}>
      <Captions cues={cues} t={t} />
    </KitProvider>,
  );
}

function label(html: string): string | null {
  if (html.includes('Leaving words')) return 'leaving';
  if (html.includes('Arriving words')) return 'arriving';
  return null;
}

describe('caption cue selection across the render shutter', () => {
  it.each(cases)('holds one cue at $rate fps, $samples samples, $shutter degrees', ({num, den, samples, shutter}) => {
    setFilmData({...FILM, film: {...FILM.film, fps_num: num, fps_den: den}} as never);
    const cues = cuesAt(secOf(102));
    setFilmData({...FILM, film: {...FILM.film, fps_num: num, fps_den: den}, captions: cues} as never);
    for (const [local, expected] of [[0, 'leaving'], [1, 'arriving'], [2, 'arriving']] as const) {
      const labels = Array.from({length: samples}, (_, k) => label(markup(filmFrameOf(local * samples + k, 101, samples, shutter), samples)));
      expect(labels).toEqual(Array(samples).fill(expected));
    }
  });

  it('keeps an empty master frame empty at a cue ending', () => {
    const cues = cuesAt(secOf(50));
    cues[1].start = secOf(60);
    const labels = Array.from({length: 16}, (_, k) => label(markup(filmFrameOf(50 * 16 + k, 0, 16), 16, cues)));
    expect(labels).toEqual(Array(16).fill(null));
  });

  it('chooses the cue at frame center when its boundary is between frames', () => {
    const cues = cuesAt(secOf(50.125));
    for (const [master, expected] of [[50, 'leaving'], [51, 'arriving']] as const) {
      const labels = Array.from({length: 16}, (_, k) => label(markup(filmFrameOf(master * 16 + k, 0, 16), 16, cues)));
      expect(labels).toEqual(Array(16).fill(expected));
    }
  });

  it('selects the first cue for every sample of master frame zero', () => {
    const cues = cuesAt(secOf(50));
    const labels = Array.from({length: 16}, (_, k) => label(markup(filmFrameOf(k, 0, 16), 16, cues)));
    expect(labels).toEqual(Array(16).fill('leaving'));
  });

  it('keeps fade opacity on the fractional sample clock', () => {
    const cues = cuesAt(secOf(50));
    const html = Array.from({length: 16}, (_, k) => markup(filmFrameOf(51 * 16 + k, 0, 16), 16, cues));
    expect(html.map(label)).toEqual(Array(16).fill('arriving'));
    const opacity = html.map((row) => Number(row.match(/opacity:([^;]+)/)?.[1]));
    expect(opacity.every((value) => Number.isFinite(value) && value > 0 && value < 1)).toBe(true);
    expect(new Set(opacity).size).toBeGreaterThan(1);
  });

  it('preserves an explicit cue clock supplied by the caller', () => {
    const cues = cuesAt(secOf(50.25));
    expect(label(markup(50, 16, cues, secOf(50.3)))).toBe('arriving');
  });

  it('preserves literal fractional time in a single-sample preview', () => {
    const cues = cuesAt(secOf(50.25));
    expect(label(markup(50.3, 1, cues))).toBe('arriving');
  });
});
