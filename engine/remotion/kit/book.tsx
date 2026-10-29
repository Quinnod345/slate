// A physical book in the kit's 2.5D world: caller-owned content on front/back faces, a curling strip chain, board
// thickness, and shadows that hand across the spine through zero. Shapes stay untilted; callers wrap type in TiltSpan.
import React from 'react';
import {alpha, mix} from './core/color';
import {bookGeometry, pageStripGeometry, pageTurnAngle, pageTurnShadow, type BookSize} from './core/book';
import {useTokens} from './context';
import {useRasterMode} from './paper';

export type LeafFaces = {recto: React.ReactNode; verso: React.ReactNode};
export type BookState = {/** 0 closed, -180 open. */ cover: number; leaves: number[]};
export type BookPalette = {page: string; pageEdge: string; pageEdgeDeep: string; cover: string; coverEdge: string; coverDeep: string; shade: string};

function useBookPalette(given?: Partial<BookPalette>): BookPalette {
  const c = useTokens().color;
  return {page: c.surface, pageEdge: mix(c.surface, c.ink, 0.12), pageEdgeDeep: mix(c.surface, c.ink, 0.2),
    cover: c.inverse, coverEdge: mix(c.inverse, c.onInverse, 0.2), coverDeep: c.inverseDeep, shade: c.ink, ...given};
}

function Face({w, h, children, back, shade, radius, color, paper}: {
  w: number; h: number; children: React.ReactNode; back?: boolean; shade: number[]; radius?: string; color: string; paper: string;
}) {
  const [a, b] = shade;
  return <div data-slate-book-face={back ? 'verso' : 'recto'} style={{position: 'absolute', left: 0, top: 0, width: w, height: h,
    overflow: 'hidden', backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden',
    transform: back ? 'rotateY(180deg)' : undefined, borderRadius: radius, background: paper}}>
    {children}
    {(a > 0.004 || b > 0.004) && <div style={{position: 'absolute', inset: 0, pointerEvents: 'none',
      background: `linear-gradient(90deg, ${alpha(color, back ? b : a)}, ${alpha(color, back ? a : b)})`}} />}
  </div>;
}

type StripData = ReturnType<typeof pageStripGeometry>;
function Strip({i, geometry, w, h, radius, recto, verso, palette}: LeafFaces & {
  i: number; geometry: StripData; w: number; h: number; radius: number; palette: BookPalette;
}) {
  const tilt = useRasterMode() === 'tilt';
  const g = geometry[i], last = i === geometry.length - 1;
  return <div data-slate-book-strip={i} style={{position: 'absolute', left: i ? g.w : 0, top: 0, width: g.w, height: h,
    transformStyle: 'preserve-3d', transformOrigin: '0 50%', transform: `rotateY(${g.relative.toFixed(6)}deg)`}}>
    <Face w={g.faceW} h={h} shade={g.frontShade} radius={last ? `0 ${radius}px ${radius}px 0` : undefined} color={palette.shade} paper={palette.page}>
      <div style={{position: 'absolute', left: g.rectoX, top: 0, width: w, height: h}}>{recto}</div>
    </Face>
    <Face w={g.faceW} h={h} back shade={g.backShade} radius={last ? `${radius}px 0 0 ${radius}px` : undefined} color={palette.shade} paper={palette.page}>
      <div style={{position: 'absolute', left: tilt ? 0 : g.versoX, top: 0, width: w, height: h,
        transform: tilt ? `translateX(${g.versoX}px)` : undefined}}>{verso}</div>
    </Face>
    {!last && <Strip i={i + 1} geometry={geometry} w={w} h={h} radius={radius} recto={recto} verso={verso} palette={palette} />}
  </div>;
}

export type PageLeafProps = LeafFaces & {w: number; h: number; theta: number; z?: number; strips?: number; curl?: number;
  radius?: number; palette?: Partial<BookPalette>};

/** A leaf hinged at x=0, centred in y. Ten chained strips curl in motion; at rest content draws once, flat and sharp. */
export function PageLeaf({w, h, theta, z = 0, recto, verso, strips = 10, curl = 1, radius, palette: given}: PageLeafProps) {
  const palette = useBookPalette(given);
  const g = bookGeometry({w, h, pageRadius: radius}, 0);
  const angle = pageTurnAngle(theta), geometry = pageStripGeometry(w, angle, strips, curl);
  if (!Number.isFinite(z)) throw new Error('A leaf depth must be finite');
  if (angle > -0.05 || angle < -179.95) {
    const turned = angle < -90;
    return <div data-slate-book-leaf={turned ? 'left' : 'right'} style={{position: 'absolute', left: turned ? -w : 0,
      top: -h / 2, width: w, height: h, transform: `translateZ(${z}px)`}}>
      <div style={{position: 'absolute', inset: 0, overflow: 'hidden', background: palette.page,
        borderRadius: turned ? `${g.pageRadius}px 0 0 ${g.pageRadius}px` : `0 ${g.pageRadius}px ${g.pageRadius}px 0`}}>{turned ? verso : recto}</div>
    </div>;
  }
  return <div data-slate-book-leaf="turning" style={{position: 'absolute', left: 0, top: -h / 2, width: 0, height: h,
    transformStyle: 'preserve-3d', transform: `translateZ(${z}px)`}}>
    <Strip i={0} geometry={geometry} w={w} h={h} radius={g.pageRadius} recto={recto} verso={verso} palette={palette} />
  </div>;
}

function Cover({theta, z, outer, inner, g, palette}: {theta: number; z: number; outer: React.ReactNode; inner: React.ReactNode;
  g: ReturnType<typeof bookGeometry>; palette: BookPalette}) {
  const lit = Math.cos(theta * Math.PI / 180), front = 0.28 * (1 - Math.max(0, lit)), back = 0.28 * (1 - Math.max(0, -lit));
  const radius = `0 ${g.coverRadius}px ${g.coverRadius}px 0`;
  return <div data-slate-book-cover="front" style={{position: 'absolute', left: 0, top: -g.ch / 2, width: g.cw, height: g.ch,
    transformStyle: 'preserve-3d', transformOrigin: '0 50%', transform: `translateZ(${z}px) rotateY(${theta}deg)`}}>
    {[1, 2].map(i => <div key={i} style={{position: 'absolute', inset: 0, borderRadius: radius, background: palette.coverEdge,
      transform: `translateZ(${-g.coverThickness * i / 3}px)`}} />)}
    <Face w={g.cw} h={g.ch} shade={[front, front]} radius={radius} color={palette.shade} paper={palette.cover}>{outer}</Face>
    <div style={{position: 'absolute', inset: 0, transformStyle: 'preserve-3d', transform: `translateZ(${-g.coverThickness}px)`}}>
      <Face w={g.cw} h={g.ch} back shade={[back, back]} radius={`${g.coverRadius}px 0 0 ${g.coverRadius}px`}
        color={palette.shade} paper={palette.page}>{inner}</Face>
    </div>
  </div>;
}

function TurnShadow({theta, side, z, g, palette}: {theta: number; side: 1 | -1; z: number;
  g: ReturnType<typeof bookGeometry>; palette: BookPalette}) {
  const tilt = useRasterMode() === 'tilt';
  const s = pageTurnShadow(theta, side, g.w);
  if (s.strength <= 0) return null;
  return <div data-slate-book-shadow={side} style={{position: 'absolute', left: tilt ? 0 : s.x, top: -g.h / 2,
    width: tilt ? s.maxWidth : s.width, height: g.h, transformOrigin: tilt ? '0 0' : undefined,
    transform: tilt ? `translate3d(${s.x}px, 0px, ${z}px) scaleX(${s.width / s.maxWidth})` : `translateZ(${z}px)`,
    background: `linear-gradient(${side === 1 ? 90 : 270}deg, ${alpha(palette.shade, s.opacity)}, ${alpha(palette.shade, 0)})`,
    pointerEvents: 'none'}} />;
}

export type HingedBookProps = BookSize & {state: BookState; coverOuter: React.ReactNode; coverInner: React.ReactNode;
  leaves: LeafFaces[]; last: React.ReactNode; palette?: Partial<BookPalette>; strips?: number; curl?: number};

/** A book whose origin is the spine's centre. Place inside Camera/Place; animate state with the kit's film-time eases.
 * Each face is caller-owned content in page or cover coordinates. Do not fade/filter this preserve-3d parent. */
export function HingedBook({state, coverOuter, coverInner, leaves, last, palette: given, strips = 10, curl = 1, ...size}: HingedBookProps) {
  const palette = useBookPalette(given), g = bookGeometry(size, leaves.length);
  const cover = pageTurnAngle(state.cover), angles = leaves.map((_, i) => pageTurnAngle(state.leaves[i] ?? 0));
  return <div data-slate-book="hinged" style={{position: 'absolute', left: 0, top: 0, width: 0, height: 0, transformStyle: 'preserve-3d'}}>
    {[2, 1, 0].map(i => <div key={`back${i}`} style={{position: 'absolute', left: 0, top: -g.ch / 2, width: g.cw, height: g.ch,
      borderRadius: `0 ${g.coverRadius}px ${g.coverRadius}px 0`, background: i ? palette.coverDeep : palette.cover,
      transform: `translateZ(${g.back - i * g.coverThickness / 3}px)`,
      boxShadow: i === 2 ? `0 ${g.w * 0.065}px ${g.w * 0.13}px ${-g.w * 0.048}px ${alpha(palette.shade, 0.45)}` : undefined}} />)}
    {[0, 1, 2, 3].map(i => <div key={`block${i}`} style={{position: 'absolute', left: 2 * g.unit, top: -g.h / 2 + g.unit,
      width: g.w - g.unit, height: g.h - 2 * g.unit, borderRadius: `0 ${g.pageRadius}px ${g.pageRadius}px 0`,
      background: i % 2 ? palette.pageEdge : palette.pageEdgeDeep, transform: `translateZ(${g.back + (i + 1) * g.unit * 0.8}px)`}} />)}
    <div style={{position: 'absolute', left: 0, top: -g.h / 2, width: g.w, height: g.h,
      borderRadius: `0 ${g.pageRadius}px ${g.pageRadius}px 0`, overflow: 'hidden', background: palette.page, transform: `translateZ(${g.base}px)`}}>{last}</div>
    {angles.map((theta, i) => <React.Fragment key={`shadow${i}`}>
      <TurnShadow theta={theta} side={1} z={(i + 1 < leaves.length ? g.right(i + 1) : g.base) + g.unit / 3} g={g} palette={palette} />
      <TurnShadow theta={theta} side={-1} z={(i ? g.left(i - 1) : g.coverLeft + g.coverThickness) + g.unit / 3} g={g} palette={palette} />
    </React.Fragment>)}
    {leaves.map((faces, i) => <PageLeaf key={i} w={g.w} h={g.h} theta={angles[i]} z={g.leafZ(i, angles[i])} {...faces}
      strips={strips} curl={curl} radius={g.pageRadius} palette={palette} />)}
    <Cover theta={cover} z={g.coverZ(cover)} outer={coverOuter} inner={coverInner} g={g} palette={palette} />
  </div>;
}
