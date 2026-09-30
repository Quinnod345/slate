// Which brand pack the kit is dressing the film in. By default it is the film's own (`@film/data`'s `brand`); a review
// sheet or a test can put another in place with setBrand(). Everything the kit derives from the brand (tokens, the
// motion law, fonts, the look) is memoised against the pack object and recomputed when it changes.
import {filmData, onFilmDataChange} from './film';
import type {BrandPack} from './types';

let override: BrandPack | null = null;
const listeners = new Set<() => void>();

onFilmDataChange(() => {
  for (const fn of listeners) fn();
});

/** The brand pack in force: an override from setBrand(), else the film's own, else an empty pack. */
export function brandPack(): BrandPack {
  return override ?? (filmData()?.brand as BrandPack | undefined) ?? {};
}

/** Use another brand pack (a review sheet for a brand with no film yet, or a test). null goes back to the film's. */
export function setBrand(pack: BrandPack | null): void {
  override = pack;
  for (const fn of listeners) fn();
}

/** Run `fn` whenever the brand in force changes. */
export function onBrandChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * A tiny memo keyed on the brand pack object, cleared whenever the brand or the film data changes (a law read in frames
 * depends on the film's rate as well as the pack).
 */
export function memoOnBrand<T>(compute: (pack: BrandPack) => T): (pack?: BrandPack) => T {
  const seen = new Map<object, T>();
  onBrandChange(() => seen.clear());
  return (pack?: BrandPack) => {
    const p = pack ?? brandPack();
    if (seen.has(p)) return seen.get(p) as T;
    const value = compute(p);
    seen.set(p, value);
    return value;
  };
}
