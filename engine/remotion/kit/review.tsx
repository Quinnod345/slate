// The kit review sheet: every component in one brand's tokens, on one frame, so a look can be judged at a glance and a
// brand's onboarding ends with a sheet to approve. Each section also stands alone (the golden stills render them one by
// one). Sizes are fractions of each cell, colours are the brand's, and time is the sheet's own (seconds from frame 0).
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {brandPack} from './core/brand';
import {contrast} from './core/color';
import type {Cue} from './core/captions';
import {BUILTIN, evalEase, type Ease} from './core/eases';
import {fps, frameSize} from './core/film';
import {fontPlan} from './core/fontplan';
import {GESTURE_KINDS, motifBeats, motifSpec, type GestureKind, type MotifLayout, type MotifTrack} from './core/motif';
import {law, t90Of} from './core/motion';
import {KIT_VERSION} from './core/version';
import {KitProvider, useAppPx, useFilmFrame, useFilmTime, useFrameSize, useLook, useTokens, useType} from './context';
import {Captions} from './captions';
import {BarChart, CurveMarker, DistributionCurve, LineChart, RadarChart, RingChart} from './charts';
import {KineticWords} from './kinetic';
import {brandLogo, fitLogoBuild, LogoBuild, logoPlan} from './logo';
import {MarkCharacter, MEANINGS, MotifMark} from './motif';
import {Card, Ground, P} from './paper';
import {PerChar, CountUp} from './type';
import {Button, Checkbox, Chip, Field, Pill, ProgressBlocks, StatusDisc} from './ui';

type Cell = {w: number; h: number};

function Label({children}: {children: React.ReactNode}) {
  const s = useType('eyebrow');
  return <div style={{...s, marginBottom: '0.8em'}}>{children}</div>;
}

// ---------------------------------------------------------------------------------------------------------- palette
export function PaletteSection({w, h}: Cell) {
  const tk = useTokens();
  const mono = useType('mono');
  const c = tk.color;
  const roles: [string, string][] = [
    ['page', c.page],
    ['surface', c.surface],
    ['ink', c.ink],
    ['ink2', c.ink2],
    ['ink3', c.ink3],
    ['hairline', c.hairline],
    ['accent', c.accent],
    ['accent ink', c.accentInk],
    ['soft accent', c.softAccent],
    ['inverse', c.inverse],
    ['ok', c.tones.ok.dot],
    ['warn', c.tones.warn.dot],
    ['risk', c.tones.risk.dot],
    ['neutral', c.tones.neutral.dot],
  ];
  // the fewest columns that fit every swatch (its chip and three lines of text) in the cell
  const labelH = h * 0.16;
  const gapX = 0.02 * w;
  const fit = (cols: number) => {
    const sw = (w - (cols - 1) * gapX) / cols;
    const font = Math.max(6, Math.min(10, sw * 0.105));
    const rows = Math.ceil(roles.length / cols);
    return {cols, sw, font, ok: rows * (sw * 0.67 + 3 * font * 1.25) + (rows - 1) * gapX <= h - labelH};
  };
  const {sw, font} = [5, 6, 7, 8, 9, 10, 12, 14].map(fit).find((x) => x.ok) ?? fit(14);
  return (
    <div style={{width: w, height: h}}>
      <Label>Palette · roles and contrast on the page</Label>
      <div style={{display: 'flex', flexWrap: 'wrap', gap: gapX}}>
        {roles.map(([name, col]) => (
          <div key={name} style={{width: sw}}>
            <div style={{height: sw * 0.62, borderRadius: sw * 0.08, background: col, boxShadow: `inset 0 0 0 1px ${c.hairline}`}} />
            <div style={{...mono, fontSize: font, marginTop: sw * 0.05, letterSpacing: 0, textTransform: 'none', color: c.ink2, lineHeight: 1.25, whiteSpace: 'nowrap', overflow: 'hidden'}}>
              {name}
              <br />
              {col.toUpperCase()}
              <br />
              {contrast(col, c.page).toFixed(1)}:1
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------- type
export function TypeSection({w, h}: Cell) {
  const tk = useTokens();
  const roles = ['hero', 'h1', 'h2', 'body', 'eyebrow', 'mono'] as const;
  const faces = fontPlan().faces;
  const mono = useType('mono');
  // large roles shrink to fit the cell; small ones keep their real size (that's what they look like on screen)
  const maxRow = h / 6;
  return (
    <div style={{width: w, height: h, overflow: 'hidden'}}>
      <Label>Type · {faces.length ? faces.map((f) => `${f.family} ${f.weights.join('/')} (${f.source.kind})`).join(' · ') : 'no families declared'}</Label>
      <div style={{display: 'flex', gap: w * 0.04}}>
        <div style={{flex: '1 1 55%', minWidth: 0}}>
          {roles.slice(0, 3).map((role) => (
            <TypeRow key={role} role={role} maxSize={maxRow} />
          ))}
        </div>
        <div style={{flex: '1 1 45%', minWidth: 0}}>
          {roles.slice(3).map((role) => (
            <TypeRow key={role} role={role} maxSize={maxRow} />
          ))}
          <div style={{...mono, color: tk.color.ink3, marginTop: '0.4em'}}>
            <CountUp t0={0} from={0} to={1234} duration={0.001} role="mono" /> · tabular
          </div>
        </div>
      </div>
    </div>
  );
}

function TypeRow({role, maxSize}: {role: string; maxSize: number}) {
  const s = useType(role);
  const tk = useTokens();
  const st = tk.type[role];
  return (
    <div style={{...s, fontSize: Math.min(s.fontSize, maxSize), whiteSpace: 'nowrap', overflow: 'hidden', marginBottom: '0.15em'}}>
      {role} · {st.weight}
    </div>
  );
}

// ----------------------------------------------------------------------------------------------------------- motion
function EasePlot({e, name, w, h}: {e: Ease; name: string; w: number; h: number}) {
  const tk = useTokens();
  const mono = useType('mono');
  const pts = Array.from({length: 65}, (_, i) => {
    const u = i / 64;
    return `${(u * w).toFixed(2)},${(h - evalEase(e.spec, u) * h).toFixed(2)}`;
  });
  const t9 = t90Of(name);
  return (
    <div style={{width: w}}>
      <svg width={w} height={h} style={{overflow: 'visible', display: 'block'}}>
        <rect x={0} y={0} width={w} height={h} fill="none" stroke={tk.color.hairline} strokeWidth={1} />
        <line x1={0} x2={w} y1={h * 0.1} y2={h * 0.1} stroke={tk.color.hairline} strokeWidth={1} strokeDasharray="3 3" />
        <line x1={t9 * w} x2={t9 * w} y1={0} y2={h} stroke={tk.color.accent} strokeOpacity={0.45} strokeWidth={1} />
        <polyline points={pts.join(' ')} fill="none" stroke={tk.color.accent} strokeWidth={Math.max(1.2, w * 0.012)} strokeLinejoin="round" />
      </svg>
      <div style={{...mono, fontSize: Math.min(mono.fontSize, w * 0.11), letterSpacing: 0, textTransform: 'none', marginTop: 4, whiteSpace: 'nowrap'}}>
        {name} {t9.toFixed(3)}
      </div>
    </div>
  );
}

export function MotionSection({w, h}: Cell) {
  const L = law();
  const tk = useTokens();
  const mono = useType('mono');
  const names = L.names.length ? L.names : ['sine'];
  const n = Math.min(6, names.length);
  const gap = 0.03 * w;
  const pw = Math.min((w - (n - 1) * gap) / n, h * 0.34);
  const durs = L.durationNames.map((d) => [d, L.durations[d]] as const);
  const maxD = Math.max(...durs.map((d) => d[1]), 1e-9);
  const colW = w / 2;
  const small = {...mono, fontSize: Math.min(mono.fontSize, h * 0.055), letterSpacing: 0, textTransform: 'none' as const};
  return (
    <div style={{width: w, height: h}}>
      <Label>Motion · {L.neutral ? 'the kit’s neutral law (no brand law yet)' : 'the brand’s named eases'} · T90 marked</Label>
      <div style={{display: 'flex', gap}}>
        {names.slice(0, n).map((name) => (
          <EasePlot key={name} e={L.eases[name] ?? BUILTIN.sine} name={name} w={pw} h={pw * 0.7} />
        ))}
      </div>
      <div style={{marginTop: h * 0.04, display: 'flex', flexWrap: 'wrap'}}>
        {durs.map(([name, sec]) => (
          <div key={name} style={{width: colW, display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2}}>
            <div style={{...small, width: colW * 0.36, overflow: 'hidden', whiteSpace: 'nowrap'}}>{name}</div>
            <div style={{height: Math.max(3, h * 0.02), width: (sec / maxD) * colW * 0.34, background: tk.color.accent, borderRadius: 2}} />
            <div style={small}>{sec.toFixed(2)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------ cards
/** The card's content at its natural app size; the cell scales the whole card to fit. */
const CARD_DESIGN = {w: 440, h: 238};

export function CardsSection({w, h}: Cell) {
  const look = useLook();
  const a = useAppPx();
  const labelH = h * 0.14;
  const dw = a(CARD_DESIGN.w);
  const dh = a(CARD_DESIGN.h);
  const k = Math.min(1, (w * 0.94) / dw, (h - labelH) * 0.9 / dh);
  return (
    <div style={{width: w, height: h, position: 'relative'}}>
      <Label>Cards and app UI · look: {look.name}</Label>
      <div style={{position: 'absolute', left: (w - dw * k) / 2, top: labelH + (h - labelH - dh * k) / 2, width: dw, height: dh, transform: `scale(${k.toFixed(5)})`, transformOrigin: '0 0'}}>
        <div style={{position: 'absolute', left: dw / 2, top: dh / 2, width: 0, height: 0}}>
          <Card w={dw} h={dh} pose={P()} pages={look.card.pages ? [{dx: dw * 0.03, dy: dh * 0.05, dz: -20, rz: 2.5, op: 0.9}] : []}>
            <div style={{padding: a(18), display: 'flex', flexDirection: 'column', gap: a(14)}}>
              <div style={{display: 'flex', gap: a(10), alignItems: 'center'}}>
                <Button label="Continue" />
                <Button label="Back" variant="secondary" />
                <Checkbox on={1} />
                <StatusDisc state="done" />
                <StatusDisc state="next" />
              </div>
              <div style={{display: 'flex', gap: a(8), flexWrap: 'wrap'}}>
                <Chip label="Ok" tone="ok" />
                <Chip label="Warn" tone="warn" />
                <Chip label="Risk" tone="risk" />
                <Pill label="Pill" />
              </div>
              <Field label="Field" value="A value, settled" w={260} />
              <ProgressBlocks value={3.4} w={220} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------------------------------------- charts
export function ChartsSection({w, h}: Cell) {
  const a = useAppPx();
  const dist = {mean: 0, sd: 1, min: -3, max: 3};
  const top = h * 0.14;
  const gw = w / 2;
  const gh = (h - top) / 2;
  const pad = Math.min(gw, gh) * 0.08;
  const radar = Math.max(10, Math.min(gw, gh) * 0.72 - a(4));
  return (
    <div style={{width: w, height: h, position: 'relative'}}>
      <Label>Charts</Label>
      <div style={{position: 'absolute', left: pad, top: top + gh * 0.34, width: gw - 2 * pad, height: gh * 0.6}}>
        <DistributionCurve dist={dist} w={gw - 2 * pad} h={gh * 0.6} />
        <CurveMarker dist={dist} w={gw - 2 * pad} h={gh * 0.6} value={0.6} label="0.6σ" />
      </div>
      <div style={{position: 'absolute', left: gw + pad, top: top + pad}}>
        <BarChart values={[3, 5, 4, 7, 6]} w={gw - 2 * pad} h={gh - 2 * pad} highlight={3} />
      </div>
      <div style={{position: 'absolute', left: (gw - radar) / 2, top: top + gh + (gh - radar) / 2}}>
        <RadarChart size={radar} spokes={['A', 'B', 'C', 'D', 'E']} series={[[0.8, 0.6, 0.9, 0.5, 0.7]]} />
      </div>
      <div style={{position: 'absolute', left: gw + pad, top: top + gh + pad, display: 'flex', gap: pad, alignItems: 'center'}}>
        <RingChart size={Math.min(gh - 2 * pad, gw * 0.35)} value={0.68} />
        <LineChart values={[2, 3, 2.5, 4, 3.6, 5]} w={gw * 0.5} h={(gh - 2 * pad) * 0.6} dots />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------- logo
export function LogoSection({w, h, t0 = 0.2}: Cell & {t0?: number}) {
  const tk = useTokens();
  const body = useType('small');
  const eyebrow = useType('eyebrow');
  const logo = brandLogo();
  // the label's own height (one line and its margin), so the build's room starts below it
  const labelH = eyebrow.fontSize * (eyebrow.lineHeight + 0.8);
  return (
    <div style={{width: w, height: h, position: 'relative'}}>
      <Label>Logo build {logo ? `· ${logo.name}` : ''}</Label>
      {logo ? <LogoCell w={w} h={h - labelH} top={labelH} t0={t0} /> : <div style={{...body, color: tk.color.ink3}}>This brand has no logo in its pack yet.</div>}
    </div>
  );
}

/**
 * The build in the room below the label, sized and placed so everything it draws fits (fitLogoBuild): the survey past
 * the mark's edges, the keystone hanging before it drops, its echo. Sized by the cell's width alone, a square mark
 * overflowed the cell and its hanging keystone was clipped at the cell's top (the demo pack at frame 45).
 */
function LogoCell({w, h, top, t0}: Cell & {top: number; t0: number}) {
  const plan = logoPlan();
  if (!plan) return null;
  const fit = fitLogoBuild(plan, w, h);
  // the build draws in screen px: place it by the room's own origin through a nested absolute frame
  return (
    <div style={{position: 'absolute', left: 0, top, width: w, height: h}}>
      <LogoBuild t0={t0} x={fit.x} y={fit.y} w={fit.w} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------ motif
export function MotifSection({w, h}: Cell) {
  const t = useFilmTime();
  const {spec, declared} = motifSpec();
  const body = useType('small');
  const size = Math.min(w * 0.3, h * 0.55);
  return (
    <div style={{width: w, height: h, position: 'relative'}}>
      <Label>Motif · {spec.parts} parts{declared ? '' : ' (no motif in the pack: a plain ring)'}</Label>
      <div style={{position: 'absolute', left: 0, top: 0}}>
        <MarkCharacter x={w * 0.2} y={h * 0.58} size={size} track={[]} t={t} />
        <MotifMark x={w * 0.55} y={h * 0.58} size={size * 0.62} track={[{kind: 'wave', frame: 6}]} t={t} />
        <MarkCharacter x={w * 0.84} y={h * 0.58} size={size * 0.62} track={[{kind: 'pulse', frame: 10}, {kind: 'ripple', frame: 10}]} t={t} />
      </div>
      <div style={{...body, position: 'absolute', left: 0, bottom: 0}}>at rest · a wave · a pulse with its echo</div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------------- type in motion
export function KineticSection({w, h, t0 = 0.1}: Cell & {t0?: number}) {
  const frameW = useFrameSize().width;
  const words = 'Every move lands on its word';
  const n = words.split(' ').length;
  const times = Array.from({length: n}, (_, i) => t0 + 0.25 + i * 0.16);
  return (
    <div style={{width: w, height: h, position: 'relative'}}>
      <Label>Type in motion</Label>
      <PerChar text="Per character" t0={t0} role="h1" />
      <div style={{marginTop: h * 0.08}}>
        <KineticWords shot="" text={words} times={times} role="h2" highlight={['lands']} width={w / Math.max(1, frameW)} align="left" />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------------------- captions
const SAMPLE_CUE: Cue[] = [{start: -1e6, end: 1e6, text: 'Captions burn in, in the brand’s own font.', lines: ['Captions burn in,', 'in the brand’s own font.'], shot: null}];

export function CaptionsSection({w, h}: Cell) {
  return (
    <div style={{width: w, height: h, position: 'relative'}}>
      <Label>Captions</Label>
      <div style={{position: 'absolute', inset: 0}}>
        <Captions cues={SAMPLE_CUE} safe={{bottom: 0.05, side: 0.02}} />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------------------- counting
/**
 * Counts mid-count, as the kit draws them (CountUp): a centred count from 0 in its own nines, a count whose suffix holds
 * still at its box's right end, and a count followed by a word that never moves. Guides mark each box's centre and the
 * word's left edge, so a re-centring count shows against them.
 */
export function CountSection({w, h, t0 = 0.1}: Cell & {t0?: number}) {
  const tk = useTokens();
  const mono = useType('mono');
  const h2 = useType('h2');
  const top = h * 0.14;
  const row = (h - top) / 3;
  const comma = (v: number) => Math.round(v).toLocaleString('en-US');
  const caption = (text: string) => <div style={{...mono, position: 'absolute', left: 0, top: 0, letterSpacing: 0, textTransform: 'none'}}>{text}</div>;
  const at = (i: number): React.CSSProperties => ({position: 'absolute', left: 0, top: top + i * row, width: w, height: row});
  const numberTop = mono.fontSize * 2;
  return (
    <div style={{width: w, height: h, position: 'relative'}}>
      <Label>Counting · the box holds still while the figures change</Label>
      <div style={at(0)}>
        {caption('centred, from 0: its own nines until it has the figures of 1200')}
        <div style={{position: 'absolute', left: w / 2, top: numberTop, width: 1, height: row - numberTop, background: tk.color.hairline}} />
        <div style={{position: 'absolute', left: 0, right: 0, top: numberTop, textAlign: 'center'}}>
          <CountUp t0={t0} from={0} to={1200} duration={2.4} role="h1" />
        </div>
      </div>
      <div style={at(1)}>
        {caption('a suffix holds still at the right end of its box')}
        <div style={{position: 'absolute', left: w * 0.1, top: numberTop}}>
          <CountUp t0={t0} from={0} to={2500} duration={2.4} role="h1" format={(v) => `${comma(v)}+`} />
        </div>
      </div>
      <div style={at(2)}>
        {caption('followed: the word after it never moves')}
        <div style={{position: 'absolute', left: w * 0.1, top: numberTop, display: 'flex', alignItems: 'baseline', gap: h2.fontSize * 0.3}}>
          <CountUp t0={t0} from={0} to={64} duration={2.4} role="h1" followed />
          <span style={{...h2, color: tk.color.ink2}}>% of every part</span>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------- card turn
/**
 * Two-sided cards turned past 90°: each shows its back face (Card `back`) where a one-sided card showed its edge slices
 * and nothing else. Drawn in the papery look, so the thickness is there to hide.
 */
export function CardTurnSection({w, h}: Cell) {
  const tk = useTokens();
  const a = useAppPx();
  const mono = useType('mono');
  const cw = Math.min(w * 0.26, h * 0.5);
  const ch = cw * 0.62;
  const turns = [35, 120, 160];
  return (
    <KitProvider look="paper-2.5d">
      <div style={{width: w, height: h, position: 'relative'}}>
        <Label>Cards turn past 90° · a back face, never a missing edge</Label>
        {turns.map((ry, i) => (
          <div key={ry} style={{position: 'absolute', left: (w * (i + 0.5)) / turns.length, top: h * 0.55, width: 0, height: 0, perspective: cw * 4}}>
            <Card w={cw} h={ch} pose={P(0, 0, 0, 8, ry)} back={{face: tk.color.softAccent, children: <div style={{...mono, padding: a(12), color: tk.color.accentInk}}>back</div>}}>
              <div style={{...mono, padding: a(12)}}>front</div>
            </Card>
            <div style={{...mono, position: 'absolute', left: -cw / 2, width: cw, top: ch * 0.75, textAlign: 'center'}}>{ry}°</div>
          </div>
        ))}
      </div>
    </KitProvider>
  );
}

// -------------------------------------------------------------------------------------------------------- gestures
/**
 * The sizes the gesture sheet draws the character at, as shares of the frame's short side: the smallest and the largest
 * a film used it (48 and 820 px on 1080 lines, so 2/45 and 41/54). On the film's own short side, each runs in the
 * regime the film's would (full choreography, unison, or moving as a whole).
 */
export const GESTURE_SIZES = {small: 2 / 45, large: 41 / 54} as const;
/** The gesture sheet's contact: every gesture lands at t = 1 s. */
export const GESTURE_CONTACT_S = 1;
/** Seconds the gesture sheet runs: the contact, the longest gesture, and a still second after. */
export const GESTURE_SHEET_S = 4;

/** Meanings in the brand's vocabulary that use a gesture (brand.motif.gestures, or the rig's defaults). */
export function meaningsOf(kind: GestureKind): string[] {
  const own = (brandPack().motif as {gestures?: Record<string, string[]>} | null | undefined)?.gestures;
  const vocab = (own && Object.keys(own).length ? own : MEANINGS) as Record<string, string[]>;
  return Object.entries(vocab)
    .filter(([, kinds]) => kinds.includes(kind))
    .map(([m]) => m);
}

/** One gesture's track for its cell, keyed so its contact lands on `C` (a whole film frame). */
export function gestureCellTrack(kind: GestureKind, C: number, toward: {x: number; y: number}): MotifTrack {
  switch (kind) {
    case 'lean':
      return [{kind: 'lean', frame: C, toward}];
    case 'hold':
      return [{kind: 'hold', frame: C, untilFrame: C + Math.round(0.8 * fps())}];
    case 'breathe':
      return [{kind: 'breathe', frame: C, cycles: 1}];
    default:
      return [{kind, frame: C} as MotifTrack[number]];
  }
}

/** The geometry of a gesture sheet for the film's frame: disc sizes, cells and the sheet's own size, in px. */
export function gestureSheetLayout() {
  const film = frameSize();
  const large = GESTURE_SIZES.large * film.short;
  const small = GESTURE_SIZES.small * film.short;
  const cols = 4;
  const rows = Math.ceil(GESTURE_KINDS.length / cols);
  const cellW = Math.round(large * 1.5);
  const strip = Math.round(large * 0.2);
  const cellH = Math.round(large * 1.5) + strip;
  const head = Math.round(large * 0.16);
  return {film, large, small, cols, rows, cellW, cellH, strip, head, width: cols * cellW, height: head + rows * cellH};
}

function GestureCell({kind, x, y, L}: {kind: GestureKind; x: number; y: number; L: ReturnType<typeof gestureSheetLayout>}) {
  const tk = useTokens();
  const mono = useType('mono');
  const frame = useFilmFrame();
  const C = Math.round(GESTURE_CONTACT_S * fps());
  const lineH = mono.fontSize * mono.lineHeight;
  const cx = x + L.cellW / 2;
  const cy = y + lineH * 1.5 + (L.cellH - L.strip - lineH * 1.5) / 2;
  const toward = {x: cx + L.cellW, y: cy};
  const track = gestureCellTrack(kind, C, toward);
  const big: MotifLayout = {x: cx, y: cy, size: L.large};
  const tiny: MotifLayout = {x: x + L.cellW * 0.06 + L.small / 2, y: y + lineH * 2 + L.small / 2, size: L.small};
  const spec = motifSpec().spec;
  // the rig's sounds for this gesture, as the picture runs it at the large size
  const beats = motifBeats(track, spec, {layout: () => ({...big, size: big.size * spec.markRatio}), short: L.film.short});
  const meanings = meaningsOf(kind);
  const total = GESTURE_SHEET_S * fps();
  const sx = (f: number) => x + L.cellW * 0.06 + (L.cellW * 0.88 * f) / total;
  const stripY = y + L.cellH - L.strip * 0.7;
  const sounds = beats.length === 0 ? 'silent' : beats.length === 1 ? `1 sound, f${beats[0].frame}` : `${beats.length} sounds, f${beats[0].frame}–f${beats[beats.length - 1].frame}`;
  return (
    <>
      <div style={{...mono, position: 'absolute', left: x + L.cellW * 0.06, top: y + lineH * 0.5, color: tk.color.ink2, whiteSpace: 'nowrap'}}>
        {kind} · {meanings.length ? meanings.join(', ') : 'in no meaning of this brand'}
      </div>
      <MarkCharacter {...big} track={track} />
      <MarkCharacter {...tiny} track={track} />
      <div style={{position: 'absolute', left: sx(0), top: stripY, width: L.cellW * 0.88, height: 1, background: tk.color.hairline}} />
      {beats.map((b) => (
        <div key={`${b.frame}-${b.gesture}`} style={{position: 'absolute', left: sx(b.frame) - 1, top: stripY - L.strip * 0.18, width: 2, height: L.strip * 0.36, background: frame >= b.frame ? tk.color.accent : tk.color.ink3}} />
      ))}
      <div style={{position: 'absolute', left: sx(Math.min(frame, total)), top: stripY - L.strip * 0.3, width: 1, height: L.strip * 0.6, background: tk.color.ink}} />
      <div style={{...mono, position: 'absolute', left: x + L.cellW * 0.06, top: stripY + L.strip * 0.36, color: tk.color.ink3, whiteSpace: 'nowrap'}}>
        {sounds}
      </div>
    </>
  );
}

/**
 * The gesture sheet (L-kit-079, L-kit-D082): every gesture the rig draws in its own cell, keyed so its contact lands at
 * t = 1 s, at the smallest and the largest size a film uses the character (48 and 820 px on 1080 lines, scaled to the
 * film), with the rig's sounds (motifBeats) on a strip under each. Reviewed by eye before builders use the rig; it
 * renders at full speed (Kit-Gestures) and at quarter speed (Kit-Gestures-Quarter). The sheet is bigger than the film's
 * frame (the largest character needs the room), so everything on it is sized and run as on the film's own frame: the
 * type, and the regime each size runs in.
 */
export function GestureSheet({speed = 1}: {speed?: number}) {
  const L = gestureSheetLayout();
  return (
    <AbsoluteFill>
      <Ground />
      <KitProvider width={L.film.width} height={L.film.height}>
        <GestureHeader L={L} speed={speed} />
        {GESTURE_KINDS.map((kind, i) => (
          <GestureCell key={kind} kind={kind} x={(i % L.cols) * L.cellW} y={L.head + Math.floor(i / L.cols) * L.cellH} L={L} />
        ))}
      </KitProvider>
    </AbsoluteFill>
  );
}

function GestureHeader({L, speed}: {L: ReturnType<typeof gestureSheetLayout>; speed: number}) {
  const tk = useTokens();
  const mono = useType('mono');
  const t = useFilmTime();
  return (
    <div style={{...mono, position: 'absolute', left: L.cellW * 0.06, top: (L.head - mono.fontSize) / 2, color: tk.color.ink3, whiteSpace: 'nowrap'}}>
      Gestures · contact at {GESTURE_CONTACT_S} s · {Math.round(L.small)} and {Math.round(L.large)} px on {L.film.short} lines · {speed === 1 ? 'full speed' : `${speed}× speed`} · t {t.toFixed(2)} s
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------- the sheet
const SECTIONS: {id: string; C: React.FC<Cell>}[] = [
  {id: 'palette', C: PaletteSection},
  {id: 'type', C: TypeSection},
  {id: 'motion', C: MotionSection},
  {id: 'cards', C: CardsSection},
  {id: 'charts', C: ChartsSection},
  {id: 'logo', C: LogoSection},
  {id: 'motif', C: MotifSection},
  {id: 'kinetic', C: KineticSection},
  {id: 'captions', C: CaptionsSection},
];

/** Sections drawn on their own (golden stills and reviews), never on the one-frame sheet. */
const EXTRA_SECTIONS: {id: string; C: React.FC<Cell>}[] = [
  {id: 'count', C: CountSection},
  {id: 'card-turn', C: CardTurnSection},
];

/** One section filling the frame (for a still of one component). */
export function ReviewSection({id}: {id: string}) {
  const {width, height, short} = useFrameSize();
  const s = [...SECTIONS, ...EXTRA_SECTIONS].find((x) => x.id === id) ?? SECTIONS[0];
  const pad = 0.06 * short;
  return (
    <AbsoluteFill>
      <Ground />
      <div style={{position: 'absolute', left: pad, top: pad}}>
        <s.C w={width - 2 * pad} h={height - 2 * pad} />
      </div>
    </AbsoluteFill>
  );
}

/** Every component in the brand's tokens, on one frame. */
export function KitReviewSheet({title}: {title?: string}) {
  const {width, height, short} = useFrameSize();
  const tk = useTokens();
  const look = useLook();
  const mono = useType('mono');
  const portrait = height > width;
  const cols = portrait ? 2 : 3;
  const rows = Math.ceil(SECTIONS.length / cols);
  const pad = 0.035 * short;
  const head = 0.05 * short;
  const gap = 0.025 * short;
  const cw = (width - 2 * pad - (cols - 1) * gap) / cols;
  const chh = (height - 2 * pad - head - (rows - 1) * gap) / rows;
  return (
    <AbsoluteFill>
      <Ground />
      <div style={{...mono, position: 'absolute', left: pad, top: pad * 0.8, color: tk.color.ink3}}>
        {title ?? 'Kit review'} · look {look.id} · kit {KIT_VERSION}
      </div>
      {SECTIONS.map((s, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        return (
          <div key={s.id} style={{position: 'absolute', left: pad + col * (cw + gap), top: pad + head + row * (chh + gap), width: cw, height: chh, overflow: 'hidden', borderTop: `1px solid ${tk.color.hairline}`, paddingTop: gap * 0.4, boxSizing: 'border-box'}}>
            <s.C w={cw} h={chh - gap * 0.4} />
          </div>
        );
      })}
    </AbsoluteFill>
  );
}

export const REVIEW_SECTIONS = SECTIONS.map((s) => s.id);
/** Sections with a composition of their own but no place on the sheet. */
export const EXTRA_REVIEW_SECTIONS = EXTRA_SECTIONS.map((s) => s.id);
