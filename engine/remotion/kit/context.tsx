// The kit's React context: the film time of the frame being drawn, the frame's size, and the brand's tokens and look.
// ActRoot fills it from Remotion's clock; tests and review sheets can fill it directly with <KitProvider>. Components
// only ever read it through these hooks, so none of them knows a frame size or a rate of its own.
import React, {createContext, useContext} from 'react';
import {brandPack} from './core/brand';
import {fps, frameSize, secOf} from './core/film';
import {look as lookOfBrand, lookOf, type Look} from './core/looks';
import {cssVars, tokens as tokensOf, typeStyle, type ResolvedTypeStyle, type Tokens} from './core/tokens';

export type KitState = {
  /** film seconds of the frame being drawn (sub-frame accurate) */
  t: number;
  /** the same as a (fractional) film frame */
  frame: number;
  /** motion-blur sub-samples per master frame */
  sub: number;
  width: number;
  height: number;
  short: number;
  tokens: Tokens;
  look: Look;
};

const Ctx = createContext<KitState | null>(null);

function defaults(): KitState {
  const size = frameSize();
  const pack = brandPack();
  return {t: 0, frame: 0, sub: 1, width: size.width, height: size.height, short: size.short, tokens: tokensOf(pack), look: lookOfBrand(pack)};
}

export type KitProviderProps = {
  /** film seconds, or a film frame (one of the two) */
  t?: number;
  frame?: number;
  sub?: number;
  width?: number;
  height?: number;
  /** a look id to use instead of the brand's (review sheets show every look) */
  look?: string;
  children?: React.ReactNode;
};

/** Put the kit's clock, size, tokens and look in context. Anything not given comes from the parent or the film. */
export function KitProvider({t, frame, sub, width, height, look, children}: KitProviderProps) {
  const parent = useContext(Ctx);
  const pack = brandPack();
  let w = width ?? parent?.width;
  let h = height ?? parent?.height;
  if (w === undefined || h === undefined) {
    const s = frameSize();
    w = w ?? s.width;
    h = h ?? s.height;
  }
  const fr = frame ?? (t !== undefined ? t * safeFps() : parent?.frame ?? 0);
  const time = t ?? (frame !== undefined ? secOf(frame) : parent?.t ?? 0);
  const value: KitState = {
    t: time,
    frame: fr,
    sub: sub ?? parent?.sub ?? 1,
    width: w,
    height: h,
    short: Math.min(w, h),
    tokens: tokensOf(pack),
    look: look ? lookOf({...pack, look}).look : parent?.look ?? lookOfBrand(pack),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function safeFps(): number {
  try {
    return fps();
  } catch {
    return 1;
  }
}

/** Everything in context (outside a provider: the film's size and brand at t = 0). */
export function useKit(): KitState {
  return useContext(Ctx) ?? defaults();
}

/** Film seconds of the frame being drawn, sub-frame accurate. */
export const useFilmTime = (): number => useKit().t;
/** The film frame being drawn (fractional for motion-blur sub-samples). */
export const useFilmFrame = (): number => useKit().frame;
/** The frame's size, with its short and long sides. */
export function useFrameSize(): {width: number; height: number; short: number; long: number; cx: number; cy: number} {
  const k = useKit();
  return {width: k.width, height: k.height, short: k.short, long: Math.max(k.width, k.height), cx: k.width / 2, cy: k.height / 2};
}
/** The brand's tokens. */
export const useTokens = (): Tokens => useKit().tokens;
/** The look in force. */
export const useLook = (): Look => useKit().look;

/**
 * Px for a length given as a fraction of the frame's short side, so every size follows the film's frame: `u(0.028)` is
 * body text on any frame.
 */
export function useUnit(): (fraction: number) => number {
  const s = useKit().short;
  return (fraction: number) => fraction * s;
}

/**
 * One app pixel at film scale: app screens are shown at twice their size on a frame whose short side is 1080 lines,
 * so text authored at the app's sizes clears the read-time minimum. Scales with the frame.
 */
export function useAppPx(): (appPx: number) => number {
  const s = useKit().short;
  return (appPx: number) => (appPx * s) / 540;
}

/** A type role as CSS, sized for this frame. */
export function useType(role: string, color?: string): ResolvedTypeStyle {
  const k = useKit();
  return typeStyle(k.tokens, role, k.short, color);
}

/** The brand's tokens as CSS custom properties on a full-frame wrapper (--slate-color-accent, --slate-font-sans …). */
export function TokenScope({children, style}: {children?: React.ReactNode; style?: React.CSSProperties}) {
  const k = useKit();
  const vars = cssVars(k.tokens, k.short) as React.CSSProperties;
  return <div style={{position: 'absolute', inset: 0, ...vars, ...style}}>{children}</div>;
}
