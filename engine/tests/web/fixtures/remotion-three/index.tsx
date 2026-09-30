// A Remotion act drawn with three.js through @remotion/three (ThreeCanvas): the other half of the 3D stack, inside a
// Remotion scene rather than the web renderer. The web suite renders one still of it through engine-core.
import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {useCurrentFrame, useVideoConfig} from 'remotion';

export const silent = 'a fixture act with no beats';

export default function Act() {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  return (
    <div style={{width, height, background: 'black'}}>
      <ThreeCanvas width={width} height={height} camera={{position: [0, 0, 5], fov: 40}}>
        <ambientLight intensity={0.6} />
        <directionalLight position={[2, 3, 4]} intensity={2} />
        <mesh rotation={[0.4, frame * 0.1, 0]}>
          <boxGeometry args={[1.6, 1.6, 1.6]} />
          <meshStandardMaterial color="#d0d0d0" />
        </mesh>
      </ThreeCanvas>
    </div>
  );
}
