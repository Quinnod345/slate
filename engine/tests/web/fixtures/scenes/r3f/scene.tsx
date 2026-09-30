// The R3F fixture: a rounded box (drei) turned by useFrame from R3F's clock (act seconds), drawn through the
// accumulation renderer. It proves the React Three Fiber path: frameloop 'never', one advance per seek, drei's helpers
// bundled from the engine's own packages, <Accum /> taking over the drawing.
import {useRef} from 'react';
import * as THREE from 'three';
import {useFrame} from '@react-three/fiber';
import {RoundedBox} from '@react-three/drei';
import {defineScene} from '@slate/web';
import {Accum, mountR3F} from '@slate/web/r3f';

function Stage() {
  const box = useRef<THREE.Mesh>(null);
  useFrame((state) => {
    if (box.current) box.current.rotation.set(0.4, state.clock.elapsedTime * 3, 0);
  });
  return (
    <>
      <ambientLight intensity={0.6} />
      <directionalLight position={[2, 3, 4]} intensity={2} />
      <RoundedBox ref={box} args={[1.6, 1.6, 1.6]} radius={0.18} smoothness={4}>
        <meshStandardMaterial color="#d0d0d0" />
      </RoundedBox>
      <Accum samples={4} aperture={0.02} focus={new THREE.Vector3(0, 0, 0)} />
    </>
  );
}

mountR3F(<Stage />, {camera: {position: [0, 0, 5], fov: 40}});
defineScene();
