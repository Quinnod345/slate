// A neutral test act: a light square crossing a dark frame at a steady speed, drawn as a pure function of film time
// so the motion-blur samples land between frames. React and remotion only (no kit), so the engine's tests don't
// wait on the kit.
import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {film, type SceneProps} from '@film/data';
import {shots} from '@film/data';

/** How far the square moves per film frame, in pixels. */
export const SPEED = 8;
export const SIZE = 40;

/** Film frames (fractional under motion blur): the 180° shutter centred on the frame, phase −90°. */
export function filmFrame(frame: number, sub: number, firstFrame: number): number {
  const s = Math.max(1, Math.round(sub));
  const f = Math.floor(frame / s);
  const k = frame % s;
  const local = s === 1 ? f : f - 0.25 + (0.5 * (k + 0.5)) / s;
  return firstFrame + local;
}

const secondsOf = (frame: number) => (frame * film.fps_den) / film.fps_num;
/** A shot's first film frame: every beat is keyed to its shot, never to a literal film frame. */
const shotStart = (id: string) => shots.shots.find((s) => s.id === id)?.frame_in ?? 0;

export const events = [
  {shot: 'S01', id: 'square_enters', t: secondsOf(shotStart('S01') + 3), kind: 'whoosh', size: 's', note: 'The square slides in from the left.'},
  {shot: 'S02', id: 'square_crosses_centre', t: secondsOf(shotStart('S02') + 5), kind: 'land', size: 'm', note: 'The square reaches the middle of the frame.'},
];

/** A saturated patch that never moves: greys encode the same under BT.601 and BT.709, this colour doesn't. */
export const PATCH = {x: 280, y: 10, size: 30, rgb: [0, 160, 200]};

export default function Act1({sub, firstFrame}: SceneProps) {
  const x = 20 + SPEED * filmFrame(useCurrentFrame(), sub, firstFrame);
  return (
    <AbsoluteFill style={{background: '#101010'}}>
      <div style={{position: 'absolute', left: 0, top: (film.height - SIZE) / 2, width: SIZE, height: SIZE, background: '#f0f0f0', transform: `translateX(${x}px)`}} />
      <div style={{position: 'absolute', left: PATCH.x, top: PATCH.y, width: PATCH.size, height: PATCH.size, background: `rgb(${PATCH.rgb.join(', ')})`}} />
      <div style={{position: 'absolute', left: 12, top: 10, color: '#808080', fontFamily: 'Menlo, monospace', fontSize: 14}}>Steady</div>
    </AbsoluteFill>
  );
}
