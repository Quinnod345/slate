// The logo build and the logo sting, drawn from a plan (core/logo.ts): construction lines survey the mark along its
// own edges, vertex marks arrive where its outline turns, the outlines draw, the parts fill in order on the brand's
// eases, and the keystone hangs over its place, drops and lands with an echo. Colours are the artwork's own (the
// brand's SVG) with the kit's tokens for the survey; every moment comes from the brand's duration scale.
import React, {useId} from 'react';
import {AbsoluteFill} from 'remotion';
import {brandPack} from './core/brand';
import {alpha} from './core/color';
import {planLogoBuild, type LogoBuildSpec, type LogoPlan} from './core/logo';
import {dur, durRank, easeNamed, exitDur, roleEase} from './core/motion';
import {clamp01, lerp} from './core/spline';
import type {LogoInput} from './core/types';
import {useFilmTime, useFrameSize, useTokens} from './context';
import {Ground} from './paper';
import {PerWord} from './type';

/** A brand logo by name (the first one when no name is given), with its build spec. */
export function brandLogo(name?: string): {name: string; svg: string; build: LogoBuildSpec} | null {
  const logos = brandPack().logos;
  if (!logos) return null;
  const entries: [string, LogoInput][] = Array.isArray(logos) ? logos.map((l, i) => [String((l as {name?: string}).name ?? i), l]) : Object.entries(logos);
  const hit = name ? entries.find(([n]) => n === name) : entries[0];
  if (!hit || typeof hit[1].svg !== 'string') return null;
  return {name: hit[0], svg: hit[1].svg, build: (hit[1].build ?? {}) as LogoBuildSpec};
}

const plans = new Map<string, LogoPlan>();

/** The build plan for a brand logo (memoised per artwork and spec). */
export function logoPlan(name?: string, spec?: LogoBuildSpec): LogoPlan | null {
  const logo = brandLogo(name);
  if (!logo) return null;
  const s = spec ?? logo.build;
  const key = `${logo.svg}\u0000${JSON.stringify(s)}\u0000${JSON.stringify(Object.values(brandPack().motion?.durations ?? {}))}`;
  let p = plans.get(key);
  if (!p) {
    p = planLogoBuild(logo.svg, s, dur);
    plans.set(key, p);
  }
  return p;
}

function rrect(cx: number, cy: number, w: number, h: number, r: number): string {
  const x0 = cx - w / 2;
  const y0 = cy - h / 2;
  const rr = Math.min(r, w / 2, h / 2);
  return `M${x0 + rr},${y0}H${x0 + w - rr}Q${x0 + w},${y0} ${x0 + w},${y0 + rr}V${y0 + h - rr}Q${x0 + w},${y0 + h} ${x0 + w - rr},${y0 + h}H${x0 + rr}Q${x0},${y0 + h} ${x0},${y0 + h - rr}V${y0 + rr}Q${x0},${y0} ${x0 + rr},${y0}Z`;
}

/**
 * Echo rings from a shape's box: each grows out on the entrance ease and fades on the exit ease; the second is fainter.
 * For a keystone's landing, a mark's pulse, a beat that deserves one echo (never decoration).
 */
export function Ripple({cx, cy, w, h, radius = 0, t0, t: tOverride, count = 2, spacing = 0.2, reach = 1.4, duration, color, weight}: {cx: number; cy: number; w: number; h: number; radius?: number; t0: number; t?: number; count?: number; spacing?: number; reach?: number; duration?: number; color?: string; weight?: number}) {
  const clock = useFilmTime();
  const tk = useTokens();
  const {short} = useFrameSize();
  const t = tOverride ?? clock;
  const d = duration ?? durRank(4);
  const inE = easeNamed(roleEase('entrance'));
  const outE = easeNamed(roleEase('exit'));
  const rings: React.ReactNode[] = [];
  for (let k = 0; k < count; k++) {
    const u = (t - (t0 + k * spacing)) / d;
    if (u <= 0 || u >= 1) continue;
    const g = 1 + inE(u) * reach;
    rings.push(<path key={k} d={rrect(cx, cy, w * g, h * g, radius * g)} fill="none" stroke={color ?? tk.color.accent} strokeWidth={weight ?? Math.max(1, 0.0015 * short)} opacity={(k === 0 ? 0.7 : 0.4) * (1 - outE(u))} />);
  }
  return <>{rings}</>;
}

export type LogoBuildProps = {
  /** which of the brand's logos (default: its first) */
  logo?: string;
  /** a build spec instead of the brand's own */
  spec?: LogoBuildSpec;
  /** film seconds the build starts */
  t0: number;
  /** the resting logo's top-left and width, in the layer's px */
  x: number;
  y: number;
  w: number;
  t?: number;
  /** when set, everything the build leaves (survey, echoes) has faded out by this time: the plain resting logo */
  settleBy?: number;
  /** draw on the inverse ground */
  dark?: boolean;
};

/** The logo building itself, as a full-frame SVG in screen px. */
export function LogoBuild({logo, spec, t0, x, y, w, t: tOverride, settleBy, dark = false}: LogoBuildProps) {
  const clock = useFilmTime();
  const tk = useTokens();
  const {width, height, short} = useFrameSize();
  const uid = `logo${useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  const plan = logoPlan(logo, spec);
  if (!plan) return null;
  const t = tOverride ?? clock;
  const rel = t - t0;
  const [vx, vy, vw] = plan.viewBox;
  const k = w / (vw || 1);
  const X = (u: number) => x + (u - vx) * k;
  const Y = (v: number) => y + (v - vy) * k;
  const T = plan.times;
  const enter = easeNamed(roleEase('entrance'));
  const move = easeNamed(roleEase('move'));
  const state = easeNamed(roleEase('state'));
  const exitE = easeNamed(roleEase('exit'));
  const surveyInk = dark ? alpha(tk.color.onInverse, 0.55) : tk.color.ink3;
  const paper = dark ? tk.color.inverse : tk.color.page;
  const settle = settleBy === undefined ? 1 : 1 - exitE((t - (settleBy - exitDur(durRank(3)))) / exitDur(durRank(3)));
  const surveyOp = (1 - exitE((rel - T.outStart) / T.outDur)) * settle;
  const box = plan.box;
  const pad = 0.12 * (box.x1 - box.x0 + box.y1 - box.y0) * 0.5;
  const dash = `${(0.004 * short).toFixed(2)} ${(0.007 * short).toFixed(2)}`;
  const hair = Math.max(1, 0.0012 * short);
  const shapeColor = (fill: string | null) => fill ?? (dark ? tk.color.onInverse : tk.color.ink);

  // the survey: guides draw out along the mark's edges, vertex marks arrive
  const guideIn = (i: number) => enter((rel - i * 0.05) / T.constructDur);
  const guides: React.ReactNode[] = [];
  plan.guides.h.forEach((v, i) => {
    const g = guideIn(i);
    if (g <= 0) return;
    guides.push(<line key={`h${i}`} x1={X(box.x0 - pad)} x2={X(box.x0 - pad + (box.x1 - box.x0 + 2 * pad) * g)} y1={Y(v)} y2={Y(v)} stroke={surveyInk} strokeWidth={hair} strokeDasharray={dash} />);
  });
  plan.guides.v.forEach((u, i) => {
    const g = guideIn(i + plan.guides.h.length);
    if (g <= 0) return;
    guides.push(<line key={`v${i}`} x1={X(u)} x2={X(u)} y1={Y(box.y0 - pad)} y2={Y(box.y0 - pad + (box.y1 - box.y0 + 2 * pad) * g)} stroke={surveyInk} strokeWidth={hair} strokeDasharray={dash} />);
  });
  const markStep = plan.marks.length > 1 ? (0.6 * T.constructDur) / (plan.marks.length - 1) : 0;
  const markSize = 0.0075 * short;
  const marks = plan.marks.map((p, i) => {
    const a = enter((rel - i * markStep) / (0.5 * T.constructDur));
    if (a <= 0) return null;
    const s = markSize * (0.85 + 0.15 * a);
    return <rect key={`m${i}`} x={X(p.x) - s / 2} y={Y(p.y) - s / 2} width={s} height={s} fill={paper} stroke={surveyInk} strokeWidth={hair} opacity={a} />;
  });

  const tf = `translate(${(x - vx * k).toFixed(3)} ${(y - vy * k).toFixed(3)}) scale(${k.toFixed(6)})`;
  const parts = plan.shapes.filter((s) => !s.keystone);
  const outlineOp = 1 - exitE((rel - T.fillEnd) / durRank(0));
  const outlineU = plan.outline ? enter((rel - T.outlineStart) / (T.outlineDur || 1)) : 0;

  // the keystone
  const key = plan.keystone !== null ? plan.shapes[plan.keystone] : null;
  let keystone: React.ReactNode = null;
  let echo: React.ReactNode = null;
  if (key && T.hangStart !== null && T.dropStart !== null && T.dropDur !== null && T.land !== null) {
    const kb = key.bbox;
    const kcx = X((kb.x0 + kb.x1) / 2);
    const kcy = Y((kb.y0 + kb.y1) / 2);
    const kw = (kb.x1 - kb.x0) * k;
    const kh = (kb.y1 - kb.y0) * k;
    const hang = (box.y1 - box.y0) * k * 0.9;
    const dir = plan.keystoneFrom;
    const off = (d: number) => (dir === 'above' ? {dx: 0, dy: -d} : dir === 'below' ? {dx: 0, dy: d} : dir === 'left' ? {dx: -d, dy: 0} : {dx: d, dy: 0});
    const appear = enter((rel - T.hangStart) / durRank(2));
    const drop = state((rel - T.dropStart) / T.dropDur);
    const o = off(hang * (1 - drop));
    const landed = rel >= T.land;
    if (appear > 0) {
      keystone = (
        <g>
          {!landed && (
            <>
              <path d={rrect(kcx, kcy, kw, kh, Math.min(kw, kh) * 0.25)} fill="none" stroke={shapeColor(key.fill)} strokeWidth={hair} strokeDasharray={dash} opacity={0.75 * appear * surveyOp} />
              <line x1={kcx + o.dx} y1={kcy + o.dy} x2={kcx} y2={kcy} stroke={shapeColor(key.fill)} strokeWidth={hair} strokeDasharray={dash} opacity={0.6 * appear * (1 - drop)} />
            </>
          )}
          <path d={key.d} transform={`translate(${o.dx.toFixed(3)} ${(o.dy + (1 - appear) * 0.01 * short).toFixed(3)}) ${tf}`} fill={shapeColor(key.fill)} opacity={appear} />
        </g>
      );
    }
    if (plan.impact && landed) echo = <Ripple cx={kcx} cy={kcy} w={kw} h={kh} radius={Math.min(kw, kh) * 0.25} t0={t0 + T.land} t={t} duration={T.echoDur} color={shapeColor(key.fill)} />;
    if (settle < 1 && echo) echo = <g opacity={settle}>{echo}</g>;
  }

  return (
    <svg width={width} height={height} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
      <defs>
        {parts.map((s) => {
          const u = move((rel - s.fillStart) / s.fillDur);
          const b = s.bbox;
          const bw = (b.x1 - b.x0 + 2) * u;
          const bh = (b.y1 - b.y0 + 2) * u;
          const wipe = plan.wipe;
          const rx = wipe === 'x-' ? b.x1 + 1 - bw : b.x0 - 1;
          const ry = wipe === 'y-' ? b.y1 + 1 - bh : b.y0 - 1;
          const horizontal = wipe === 'x+' || wipe === 'x-';
          return (
            <clipPath key={s.index} id={`${uid}-f${s.index}`}>
              <rect x={X(horizontal ? rx : b.x0 - 1)} y={Y(horizontal ? b.y0 - 1 : ry)} width={Math.max(0, (horizontal ? bw : b.x1 - b.x0 + 2) * k)} height={Math.max(0, (horizontal ? b.y1 - b.y0 + 2 : bh) * k)} />
            </clipPath>
          );
        })}
      </defs>
      <g opacity={surveyOp}>
        {guides}
        {marks}
      </g>
      {echo}
      {plan.outline &&
        outlineOp > 0 &&
        parts.map((s) => (
          <path key={`o${s.index}`} d={s.d} transform={tf} fill="none" stroke={shapeColor(s.fill)} strokeWidth={(hair / k).toFixed(4)} pathLength={1} strokeDasharray={`${clamp01(outlineU)} 2`} opacity={outlineOp} />
        ))}
      {/* the clip goes on an untransformed group: on the transformed path itself it would be read in the path's own
          (scaled) space and clip the fill away */}
      {parts.map((s) =>
        move((rel - s.fillStart) / s.fillDur) > 0 ? (
          <g key={`f${s.index}`} clipPath={`url(#${uid}-f${s.index})`}>
            <path d={s.d} transform={tf} fill={shapeColor(s.fill)} />
          </g>
        ) : null,
      )}
      {keystone}
    </svg>
  );
}

/** When a sting's picture is done (film seconds from its t0): the build, the tagline, and a hold. */
export function stingLength(logo?: string, tagline = true): number {
  const plan = logoPlan(logo);
  const build = plan ? Math.max(plan.times.end, (plan.times.land ?? plan.times.fillEnd) + durRank(3)) : durRank(5);
  return build + (tagline ? durRank(4) + durRank(5) : durRank(4));
}

/**
 * A logo sting: the build centred on the ground, then the tagline arriving under it once the mark has landed, then a
 * hold. For "kinetic type or logo sting" films and the end of any film.
 */
export function LogoSting({logo, t0, tagline, dark = false, width: wFrac = 0.42, t: tOverride}: {logo?: string; t0: number; tagline?: string; dark?: boolean; width?: number; t?: number}) {
  const {width, height, short} = useFrameSize();
  const tk = useTokens();
  const plan = logoPlan(logo);
  if (!plan) return <Ground dark={dark} />;
  const [, , vw, vh] = plan.viewBox;
  const w = Math.min(width * wFrac, (height * 0.4 * vw) / (vh || 1));
  const h = (w * vh) / (vw || 1);
  const x = (width - w) / 2;
  const y = (height - h) / 2 - (tagline ? 0.04 * short : 0);
  const landAt = t0 + (plan.times.land ?? plan.times.fillEnd);
  return (
    <AbsoluteFill>
      <Ground dark={dark} />
      <LogoBuild logo={logo} t0={t0} x={x} y={y} w={w} t={tOverride} dark={dark} settleBy={t0 + stingLength(logo, !!tagline)} />
      {tagline && (
        <div style={{position: 'absolute', left: 0, right: 0, top: y + h + 0.05 * short, textAlign: 'center'}}>
          <PerWord text={tagline} t0={landAt + lerp(0, durRank(2), 0.5)} t={tOverride} role="h2" color={dark ? tk.color.onInverse : tk.color.ink} />
        </div>
      )}
    </AbsoluteFill>
  );
}
