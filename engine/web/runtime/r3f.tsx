// React Three Fiber in a web scene ('@slate/web/r3f'): a root that never renders on its own (frameloop 'never'),
// advanced exactly once per seek, after GSAP has moved everything to time t.
//
//   mountR3F(<Stage />);                 // in scene.tsx, before defineScene()
//   function Stage() {
//     const box = useRef<THREE.Mesh>(null);
//     useFrame((state) => { box.current!.rotation.y = state.clock.elapsedTime * 0.5; });   // elapsedTime is act seconds
//     return <mesh ref={box}>…</mesh>;
//   }
//
// Components move things inside useFrame (the recommended R3F pattern: mutate, don't re-render), and R3F's clock reads
// the frame's act time, so every frame is a function of t. <Accum /> swaps R3F's own render for the accumulation
// renderer (depth of field and anti-aliasing; see three.ts).
import React, {Suspense, useEffect, useMemo} from 'react';
import * as THREE from 'three';
import {advance, createRoot, extend, useFrame, useThree, type ReconcilerRoot, type RenderProps} from '@react-three/fiber';
import {frameSize} from '../../remotion/kit/core/index';
import {onReady, onRender} from './scene';
import {Accumulator, type AccumulatorOptions} from './three';

export type MountOptions = {
  /** the canvas to draw into (default: a new one, the film's size, added to `parent`) */
  canvas?: HTMLCanvasElement;
  parent?: HTMLElement;
  /** R3F's camera props (default: a perspective camera, R3F's defaults) */
  camera?: RenderProps<HTMLCanvasElement>['camera'];
  /** extra WebGL renderer settings (the drawing buffer is always preserved for the screenshot) */
  gl?: Record<string, unknown>;
  /** size in px (default: the film's frame, from film.json) */
  width?: number;
  height?: number;
};

function Mounted({onMounted}: {onMounted: () => void}) {
  useEffect(() => {
    onMounted();
  }, [onMounted]);
  return null;
}

/**
 * Mount an R3F tree for the scene. The renderer waits (ready) until the tree has committed and everything it suspends
 * on (models, textures) has loaded; after that every seek advances R3F once, at the frame's act time.
 */
export function mountR3F(element: React.ReactNode, opts: MountOptions = {}): {canvas: HTMLCanvasElement; root: () => ReconcilerRoot<HTMLCanvasElement> | null} {
  const size = frameSize();
  const width = opts.width ?? size.width;
  const height = opts.height ?? size.height;
  const canvas = opts.canvas ?? document.createElement('canvas');
  if (!opts.canvas) {
    canvas.width = width;
    canvas.height = height;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    (opts.parent ?? document.body).appendChild(canvas);
  }
  let root: ReconcilerRoot<HTMLCanvasElement> | null = null;
  onReady(async () => {
    // <Canvas> registers all of three.js with R3F itself; a bare root has to ask (R3F 9), or <mesh> and friends are unknown
    extend(THREE as unknown as Record<string, new (...args: never[]) => unknown>);
    root = createRoot(canvas);
    await root.configure({
      frameloop: 'never',
      dpr: 1,
      size: {width, height, top: 0, left: 0},
      gl: {antialias: true, alpha: true, ...(opts.gl ?? {}), preserveDrawingBuffer: true},
      camera: opts.camera,
      events: undefined,
    });
    await new Promise<void>((resolve) => {
      root!.render(
        <Suspense fallback={null}>
          {element}
          <Mounted onMounted={resolve} />
        </Suspense>,
      );
    });
  });
  // R3F's clock (state.clock.elapsedTime) is set to the frame's act time before its useFrame callbacks run.
  onRender((t: number) => {
    if (root) advance(t);
  });
  return {canvas, root: () => root};
}

/** The accumulation renderer as an R3F component: it takes over drawing (depth of field and anti-aliasing). */
export function Accum({samples = 24, aperture = 0.08, focus}: {samples?: number; aperture?: number; focus?: THREE.Vector3}) {
  const gl = useThree((s) => s.gl);
  const acc = useMemo(() => new Accumulator(gl, {samples, aperture, focus} satisfies AccumulatorOptions), [gl, samples, aperture, focus]);
  useEffect(() => () => acc.dispose(), [acc]);
  useFrame(({scene, camera}) => {
    if (camera instanceof THREE.PerspectiveCamera) acc.render(scene, camera);
    else gl.render(scene, camera);
  }, 1);
  return null;
}
