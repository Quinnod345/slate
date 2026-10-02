// The kit's render-stability bench: not part of any film, and the scene `slate qa stability conformance --render`
// renders through the real pipeline to test the kit itself (the films' Lab-Jitter bench, generalized). One paper card
// built by the kit's own Card (thickness layers, a drop shadow, a face with a numeral, two lines of type, a vector
// curve and a shadowed chip) drifts slowly while the Camera pushes in slowly: the conditions where the films'
// 16-sample renders showed type jumping a whole pixel against its own card ("It's so jittery. Things are shaking up
// and down", 2026-09-30). slate.qa.stability measures whether the type, the curve and the shadow move smoothly and
// together.
//
// One scene per motion, drawn in either raster mode (the render's input props {scene, raster}; the spec in
// stability_bench.json names every variant and holds every number here):
//   drift   the card drifts and the camera pushes in slowly        rest    everything still, on a sub-pixel spot
//   zoom    a real push-in (zoom_per_frame px of depth a frame)    grow    the card scales itself (grow_per_frame)
//   land    the card swings up from lying back and lands, then rests on the sub-pixel spot
//   arrive  the card still, a line of type rising on its own transform (PerWord)
//   free    drift, plus a line of free type over the camera, in no card, under a slow push of its own (TypeTilt plane)
//   morph   three still cards whose size shrinks slowly: plain, with morphPose's first version (the pose moved by its
//           offset's rounding) and with morphPose
// In hint mode with no hints (no atRest, the default) the bench draws the pre-fix drawing: fresh rasters every frame
// and snapped type, the control the scan must still fail. In tilt mode it's the kit as converted acts use it.
//
// The film it renders in is a scratch one Slate makes from the spec (its size, rate, brand and length), whose act
// re-exports this component; its colours and type come from that film's brand, its look is the spec's.
import React from 'react';
import {AbsoluteFill, getInputProps} from 'remotion';
import {Camera} from '../camera';
import {ActRoot} from '../clock';
import {KitProvider, useFilmFrame, useFrameSize, useTokens} from '../context';
import {alpha} from '../core/color';
import {fps} from '../core/film';
import {easeNamed, roleEase} from '../core/motion';
import {clamp01} from '../core/spline';
import {Card, P, RasterModeProvider, TypeTilt, actRasterMode, cardShadow, morphPose, usePaper, type Pose, type RasterMode} from '../paper';
import {PerWord} from '../type';
import SPEC from './stability_bench.json';

export type BenchScene = 'drift' | 'zoom' | 'grow' | 'rest' | 'land' | 'arrive' | 'free' | 'morph';
export const BENCH_SCENES: readonly BenchScene[] = ['drift', 'zoom', 'grow', 'rest', 'land', 'arrive', 'free', 'morph'];

const S = SPEC.scene;

/** The bench's own geometry, from the spec (one source of truth with slate.qa.stability's morph and crop boxes). */
export const benchSpec = SPEC;

/** The card's face (card px): a numeral, two lines of type, a vector curve and a shadowed chip. */
function Face({t, arrive}: {t: number; arrive: boolean}) {
  const tk = useTokens();
  const pal = usePaper();
  const sans = tk.fonts.sans?.css ?? 'sans-serif';
  const line: React.CSSProperties = {fontFamily: sans, fontSize: 30, fontWeight: 700, color: tk.color.accent, lineHeight: 1.2};
  return (
    <div style={{position: 'absolute', inset: 0, fontFamily: sans}}>
      <div style={{position: 'absolute', left: 56, top: 40, fontSize: 150, fontWeight: 700, letterSpacing: -4, lineHeight: 1.2, color: tk.color.ink}}>{S.face.numeral}</div>
      <div style={{position: 'absolute', left: 60, top: 230}}>
        {arrive ? <PerWord text={S.face.line} t0={S.arrive.t0} t={t} duration={S.arrive.dur} style={line} /> : <span style={line}>{S.face.line}</span>}
      </div>
      <div style={{position: 'absolute', left: 60, top: 272, fontSize: 24, lineHeight: 1.2, color: tk.color.ink3}}>{S.face.sub}</div>
      <svg style={{position: 'absolute', left: 40, top: 330}} width={820} height={150} viewBox="0 0 820 150">
        <path d="M0 140 C 200 140, 300 20, 410 20 S 620 140, 820 140" fill={alpha(tk.color.accent, 0.1)} stroke={tk.color.accent} strokeWidth={3} />
      </svg>
      <div style={{position: 'absolute', left: 620, top: 60, padding: '10px 18px', borderRadius: 999, fontSize: 22, fontWeight: 700, lineHeight: 1.2, background: tk.color.surface, boxShadow: `0 6px 16px -6px rgba(${pal.shadowRgb},.25)`, color: tk.color.ink}}>{S.face.chip}</div>
    </div>
  );
}

/** The card, as the kit draws it, at its pose. */
function BenchCard({pose, t, arrive = false, w = S.card.w, h = S.card.h}: {pose: Pose; t: number; arrive?: boolean; w?: number; h?: number}) {
  const pal = usePaper();
  const {short} = useFrameSize();
  return (
    <Card w={w} h={h} pose={pose} radius={S.card.radius} layers={S.card.layers} persp={S.persp} shadow={cardShadow('layered', pal.shadowRgb, short)}>
      <Face t={t} arrive={arrive} />
    </Card>
  );
}

/** One of the bench's scenes at master-frame time `f` (fractional: the motion blur's sub-samples). */
function Scene({scene, f}: {scene: BenchScene; f: number}) {
  const pal = usePaper();
  const tk = useTokens();
  const rate = fps();
  const [rx, ry] = S.rest;
  const still = scene === 'rest' || scene === 'arrive' || scene === 'land' || scene === 'morph';
  const cam = still ? {x: 0, y: 0, z: 0} : {x: S.drift.camera_per_frame[0] * f, y: S.drift.camera_per_frame[1] * f, z: (scene === 'zoom' ? S.zoom_per_frame : S.drift.camera_per_frame[2]) * f};
  let pose: Pose;
  if (scene === 'rest' || scene === 'arrive') pose = P(rx, ry);
  else if (scene === 'land') {
    // swings up from lying back and lands on the sub-pixel spot by its last frame, on the brand's move ease, then rests
    const [dx, dy, dz, drx, ds] = S.land.from;
    const u = easeNamed(roleEase('move'))(clamp01((f / rate - S.land.start_s) / (S.land.frames / rate - S.land.start_s)));
    const from = P(rx + dx, ry + dy, dz, drx, 0, ds);
    pose = P(from.x + (rx - from.x) * u, from.y + (ry - from.y) * u, from.z * (1 - u), from.rx * (1 - u), 0, from.s + (1 - from.s) * u);
  } else pose = P(S.drift.card[0] + S.drift.card_per_frame[0] * f, S.drift.card[1] + S.drift.card_per_frame[1] * f, 0, 0, 0, 1 + (scene === 'grow' ? S.grow_per_frame * f : 0));
  const t = f / rate;
  return (
    <AbsoluteFill style={{background: pal.page}}>
      {scene === 'morph' ? (
        <MorphCards f={f} />
      ) : (
        <Camera x={cam.x} y={cam.y} z={cam.z} persp={S.persp}>
          <BenchCard pose={pose} t={t} arrive={scene === 'arrive'} />
        </Camera>
      )}
      {scene === 'free' && (
        // free type over the camera, under a slow push of its own about a point off its centre
        <AbsoluteFill style={{transform: `scale(${(1 + S.free.push_per_frame * f).toFixed(6)})`, transformOrigin: `${S.free.origin[0]}px ${S.free.origin[1]}px`}}>
          <TypeTilt plane style={{position: 'absolute', left: S.free.left, top: S.free.top, fontFamily: tk.fonts.sans?.css ?? 'sans-serif', fontSize: S.free.size, lineHeight: 1.08, letterSpacing: '-0.015em', color: tk.color.ink, whiteSpace: 'nowrap'}}>
            {S.free.text[0]}
            <b>{S.free.text[1]}</b>
          </TypeTilt>
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
}

/** `morph`: three still cards on a sub-pixel spot, shrinking slowly (shrink px over the bench's frames). */
function MorphCards({f}: {f: number}) {
  const tk = useTokens();
  const u = f / SPEC.film.frames;
  const w = S.morph.size[0] - S.morph.shrink[0] * u;
  const h = S.morph.size[1] - S.morph.shrink[1] * u;
  // morphPose's first version, for comparison: the pose moved back by the rounding of the offset (−w/2, −h/2)
  const shift = (p: Pose): Pose => ({...p, x: p.x + (-w / 2 - Math.round(-w / 2)), y: p.y + (-h / 2 - Math.round(-h / 2))});
  const at = (x: number) => P(x, S.morph.y);
  const poses = [at(S.morph.x[0]), shift(at(S.morph.x[1])), morphPose(at(S.morph.x[2]))];
  const sans = tk.fonts.sans?.css ?? 'sans-serif';
  const pal = usePaper();
  const {short} = useFrameSize();
  return (
    <Camera persp={S.persp}>
      {poses.map((p, i) => (
        <Card key={i} w={w} h={h} pose={p} radius={S.card.radius} layers={S.card.layers} persp={S.persp} shadow={cardShadow('layered', pal.shadowRgb, short)}>
          <div style={{position: 'absolute', left: 40, top: 30, fontFamily: sans, fontSize: 120, fontWeight: 700, letterSpacing: -3, lineHeight: 1.2, color: tk.color.ink}}>{S.face.numeral}</div>
          <div style={{position: 'absolute', left: 44, top: 190, fontFamily: sans, fontSize: 28, fontWeight: 700, lineHeight: 1.2, color: tk.color.accent}}>{S.face.line}</div>
          <div style={{position: 'absolute', left: 44, top: 232, fontFamily: sans, fontSize: 22, lineHeight: 1.2, color: tk.color.ink3}}>{S.face.sub}</div>
        </Card>
      ))}
    </Camera>
  );
}

function sceneOf(v: unknown): BenchScene {
  return (BENCH_SCENES as readonly unknown[]).includes(v) ? (v as BenchScene) : 'drift';
}

function Bench({scene}: {scene: BenchScene}) {
  const f = useFilmFrame();
  return <Scene scene={scene} f={f} />;
}

/**
 * The bench as an act: the scratch film's act re-exports it. Its scene and raster mode come from the render's input
 * props ({scene, raster}); `props` stands in for them where there are none (tests, Node).
 */
export function StabilityBench({sub = 1, act = 1, props}: {sub?: number; act?: number; firstFrame?: number; props?: {scene?: string; raster?: string}}) {
  let given: {scene?: string; raster?: string} = props ?? {};
  if (!props) {
    try {
      given = (getInputProps() as {scene?: string; raster?: string}) ?? {};
    } catch {
      given = {};
    }
  }
  const raster: RasterMode = actRasterMode('hint', given);
  return (
    <ActRoot act={act} sub={sub}>
      <KitProvider look={SPEC.film.look}>
        <RasterModeProvider value={raster}>
          <Bench scene={sceneOf(given.scene)} />
        </RasterModeProvider>
      </KitProvider>
    </ActRoot>
  );
}

export default StabilityBench;
