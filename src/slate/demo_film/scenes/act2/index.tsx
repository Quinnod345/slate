// Act 2, built with the kit: a card rises into place as "checked" is said, then the studio's name settles under it,
// with the film's captions burned in. Everything sits in the column every 9:16 platform leaves free of its own
// buttons (12-70% across, 16-60% down), in the brand's tokens, type and motion law. Every value is a pure function
// of film time, so the motion-blur samples land between frames.
import React from 'react';
import {ActRoot, Captions, Card, Ground, P, Text, useFilmFrame, useFrameSize, useTokens} from '@slate/kit';
import type {SceneProps} from '@film/data';
import {cardIn, cardRise, nameIn, nameRise} from './beats';

function Picture() {
  const frame = useFilmFrame();
  const tokens = useTokens();
  const {width, height} = useFrameSize();
  const col = {left: width * 0.12, width: width * 0.58};
  const card = {w: col.width, h: height * 0.15};
  const moving = frame < cardRise.spec.frame_out;
  return (
    <div style={{position: 'absolute', inset: 0}}>
      <Ground />
      <div style={{position: 'absolute', left: col.left + card.w / 2, top: height * 0.3}}>
        <Card w={card.w} h={card.h} pose={P(0, cardRise(frame), 0, 0, 0, 1, cardIn(frame))} atRest={!moving}>
          <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', padding: `0 ${width * 0.04}px`}}>
            <Text role="h2" color={tokens.color.ink}>
              Checked before it ships
            </Text>
          </div>
        </Card>
      </div>
      <div style={{position: 'absolute', left: col.left, width: col.width, top: height * 0.43, opacity: nameIn(frame), transform: `translateY(${nameRise(frame).toFixed(3)}px)`}}>
        <Text role="h1" color={tokens.color.ink}>
          Demo Studio
        </Text>
      </div>
      <Captions role="body" safe={{bottom: 0.41, side: 0.2}} />
    </div>
  );
}

export default function Act2({sub, act}: SceneProps) {
  return (
    <ActRoot act={act} sub={sub}>
      <Picture />
    </ActRoot>
  );
}
