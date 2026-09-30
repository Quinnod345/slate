// The kit's review compositions: the whole review sheet, each section alone, a placeholder act, a logo sting, kinetic
// type on the film's own words with its captions, and one composition that must fail the font gate (it asks for a
// face no machine has). The golden stills render these; brand onboarding renders the sheet for approval.
//
// A review for a brand with no film yet passes the pack as input props ({brand}); the rest comes from `@film/data`,
// whatever film the entry is bundled with (the kit's own tests use a neutral fixture). Sizes and the rate come from
// the film, and a vertical sheet proves the layout at 9:16.
import React from 'react';
import {AbsoluteFill, Composition, getInputProps, useCurrentFrame, useVideoConfig} from 'remotion';
import {setBrand} from '../core/brand';
import {acts, actSpan, fps, frameSize, shot} from '../core/film';
import {fontPlan} from '../core/fontplan';
import type {BrandPack} from '../core/types';
import {actComposition, ActRoot} from '../clock';
import {KitProvider, TokenScope, useFrameSize} from '../context';
import {Captions} from '../captions';
import {FontGate} from '../fonts';
import {KineticWords} from '../kinetic';
import {LogoSting, stingLength} from '../logo';
import {Ground} from '../paper';
import {PlaceholderAct} from '../placeholder';
import {KitReviewSheet, REVIEW_SECTIONS, ReviewSection} from '../review';

const input = (getInputProps() ?? {}) as {brand?: BrandPack};
if (input.brand) setBrand(input.brand);

const firstAct = (): number | string | undefined => acts()[0];
const firstShot = (): string => {
  const a = firstAct();
  return a !== undefined ? actSpan(a)?.first ?? '' : '';
};

/** Film time from the composition's frame (review compositions start at film frame 0). */
function Sheet({children, fonts = true}: {children?: React.ReactNode; fonts?: boolean}) {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  return (
    <FontGate enabled={fonts}>
      <KitProvider frame={frame} width={width} height={height}>
        <TokenScope>{children}</TokenScope>
      </KitProvider>
    </FontGate>
  );
}

const Review: React.FC = () => (
  <Sheet>
    <KitReviewSheet />
  </Sheet>
);

const section = (id: string) => {
  const C: React.FC = () => (
    <Sheet>
      <ReviewSection id={id} />
    </Sheet>
  );
  return C;
};

const Sting: React.FC = () => (
  <Sheet>
    <LogoSting t0={0.1} tagline="Every part lands on time" />
  </Sheet>
);

function KineticFilmBody() {
  const {height} = useFrameSize();
  const id = firstShot();
  const s = shot(id);
  return (
    <AbsoluteFill>
      <Ground />
      <div style={{position: 'absolute', left: 0, right: 0, top: height * 0.34, display: 'flex', justifyContent: 'center'}}>
        <KineticWords shot={id} text={s.onScreen || s.vo} highlight={['lands']} />
      </div>
      <Captions />
    </AbsoluteFill>
  );
}

/** Kinetic type on the film's first shot's own words, with the film's captions burned in. */
const KineticFilm: React.FC = () => (
  <ActRoot act={firstAct() ?? 1}>
    <KineticFilmBody />
  </ActRoot>
);

const Placeholder: React.FC<{sub: number}> = ({sub}) => (
  <ActRoot act={firstAct() ?? 1} sub={sub}>
    <PlaceholderAct act={firstAct() ?? 1} />
  </ActRoot>
);

/** Must fail: a face that exists nowhere, so the gate has to catch the browser's silent substitution. */
const MISSING = fontPlan({type: {families: [{role: 'sans', family: 'Slate Missing Face 7Q', weights: [400], source: 'installed', licence: 'test face that exists nowhere'}]}});
const FontMissing: React.FC = () => (
  <FontGate plan={MISSING}>
    <Sheet fonts={false}>
      <KitReviewSheet title="This frame must never render" />
    </Sheet>
  </FontGate>
);

export const KitReviewRoot: React.FC = () => {
  const size = frameSize();
  const rate = fps();
  const long = Math.max(1, Math.round(rate * 4)); // four seconds of sheet, whole frames at the film's rate
  const first = firstAct();
  const act = first !== undefined ? actComposition(first, {id: 'Kit-Placeholder'}) : null;
  return (
    <>
      <Composition id="Kit-Review" component={Review} durationInFrames={long} fps={rate} width={size.width} height={size.height} />
      <Composition id="Kit-Review-Vertical" component={Review} durationInFrames={long} fps={rate} width={size.height} height={size.width} />
      {REVIEW_SECTIONS.map((id) => (
        <Composition key={id} id={`Kit-Section-${id}`} component={section(id)} durationInFrames={long} fps={rate} width={size.width} height={size.height} />
      ))}
      <Composition id="Kit-Sting" component={Sting} durationInFrames={Math.max(1, Math.round((stingLength(undefined, true) + 0.2) * rate))} fps={rate} width={size.width} height={size.height} />
      {act && <Composition id="Kit-Placeholder" component={Placeholder} durationInFrames={act.durationInFrames} fps={act.fps} width={act.width} height={act.height} defaultProps={act.defaultProps} />}
      {act && <Composition id="Kit-Kinetic" component={KineticFilm} durationInFrames={act.durationInFrames} fps={act.fps} width={act.width} height={act.height} />}
      <Composition id="Kit-FontMissing" component={FontMissing} durationInFrames={1} fps={rate} width={size.width} height={size.height} />
    </>
  );
};
