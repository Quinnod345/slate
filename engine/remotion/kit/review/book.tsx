// A short neutral proof: the cover opens, two leaves turn, and a ruled spread holds still for registration review.
import React from 'react';
import {HingedBook} from '../book';
import {Camera} from '../camera';
import {easeNamed, roleEase} from '../core/motion';
import {useFilmTime, useFrameSize, useTokens} from '../context';
import {Ground, RasterModeProvider, TiltSpan, type RasterMode} from '../paper';

export const BOOK_REVIEW_SECONDS = 3.4;

/** Time is in film seconds, including fractional frames during a motion-blur render. */
export function bookReviewState(t: number) {
  const turn = (start: number, duration: number) => -180 * easeNamed(roleEase('move'))((t - start) / duration);
  return {cover: turn(0.15, 0.65), leaves: [turn(0.95, 0.85), turn(2, 0.85)]};
}

function Page({w, h, label, index, cover = false}: {w: number; h: number; label: string; index: string; cover?: boolean}) {
  const tokens = useTokens(), c = tokens.color;
  const ink = cover ? c.onInverse : c.ink;
  return <div style={{position: 'absolute', inset: 0, color: ink, background: cover ? c.inverse : c.surface,
    fontFamily: tokens.fonts.sans.css, padding: w * 0.09, boxSizing: 'border-box'}}>
    <div style={{fontSize: w * 0.057, letterSpacing: '0.06em'}}><TiltSpan>{index}</TiltSpan></div>
    <div style={{marginTop: h * 0.11, fontSize: w * 0.12, lineHeight: 1.06, fontWeight: 700}}><TiltSpan>{label}</TiltSpan></div>
    <div style={{position: 'absolute', left: w * 0.09, right: w * 0.09, bottom: h * 0.12, height: h * 0.29,
      borderTop: `${w * 0.003}px solid ${ink}`, borderBottom: `${w * 0.003}px solid ${ink}`}}>
      {[0.25, 0.5, 0.75].map(f => <div key={f} style={{position: 'absolute', left: 0, right: 0, top: `${f * 100}%`,
        height: w * 0.0015, background: cover ? c.onInverse : c.hairline}} />)}
      <div style={{position: 'absolute', left: '72%', top: 0, bottom: 0, width: w * 0.002, background: ink}} />
    </div>
  </div>;
}

export function BookReview({raster = 'tilt'}: {raster?: RasterMode}) {
  const t = useFilmTime(), {width, height} = useFrameSize();
  const w = Math.min(width * 0.32, height * 0.46), h = w * 1.36;
  const state = bookReviewState(t), face = (label: string, index: string, cover = false) => <Page w={w} h={h} label={label} index={index} cover={cover} />;
  return <RasterModeProvider value={raster}>
    <Ground />
    <Camera rx={7} ry={-5} atRest>
      <HingedBook w={w} h={h} state={state} coverOuter={face('Field notes', 'VOLUME 01', true)} coverInner={face('Start here', 'INSIDE')}
        leaves={[{recto: face('Observe', '01'), verso: face('Connect', '02')}, {recto: face('Shape', '03'), verso: face('Make it clear', '04')}]}
        last={face('Carry it forward', '05')} />
    </Camera>
  </RasterModeProvider>;
}
