// Act 1, built with the kit: a card rises into place and a dot crosses the frame, drawn in the brand's tokens on the
// film's clock. Every value is a pure function of film time, so the motion-blur samples land between frames.
import React from 'react';
import {ActRoot, Text, useFilmFrame, useFrameSize, useTokens} from '@slate/kit';
import type {SceneProps} from '@film/data';
import {cardRise, dotCross} from './beats';

function Picture() {
  const frame = useFilmFrame();
  const tokens = useTokens();
  const {width, height, short} = useFrameSize();
  const card = {w: width * 0.5, h: height * 0.34};
  const dot = short * 0.08;
  return (
    <div style={{position: 'absolute', inset: 0, background: tokens.color.page}}>
      <div
        style={{
          position: 'absolute',
          left: (width - card.w) / 2,
          top: height * 0.18 + cardRise(frame),
          width: card.w,
          height: card.h,
          borderRadius: short * 0.03,
          background: tokens.color.surface,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Text role="h2" color={tokens.color.ink}>
          One clock
        </Text>
      </div>
      <div style={{position: 'absolute', left: dotCross(frame), top: height * 0.72, width: dot, height: dot, borderRadius: dot / 2, background: tokens.color.accent}} />
    </div>
  );
}

export default function Act1({sub, act}: SceneProps) {
  return (
    <ActRoot act={act} sub={sub}>
      <Picture />
    </ActRoot>
  );
}
