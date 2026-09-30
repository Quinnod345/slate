// three.js in a web scene ('@slate/web/three'): a renderer the size of the film frame, drawn on demand once per seek,
// and the accumulation renderer.
//
// On demand: nothing renders on its own. renderOnSeek() draws the scene after every seek, once GSAP has moved things
// to time t, so the picture is a function of t alone (a three.js animation loop would follow the display instead).
//
// The accumulation renderer is generalized from the films' Accum.tsx: each drawn frame averages `samples` renders, the
// camera moving over a lens disk of radius `aperture` (world units) with the frustum sheared so the focus plane stays
// put: true depth of field, where everything on that plane is sharp and everything nearer or farther blurs by its real
// circle of confusion. A sub-pixel Halton (2, 3) jitter anti-aliases edges. It is deterministic (no randomness), so
// renders repeat frame for frame. Motion blur is the renderer's job, not this one's: web-render averages whole
// sub-frames in 16-bit, DOM and canvas alike. (postprocessing composers render blank under a manual frame loop,
// which is why the films wrote this instead.)
import * as THREE from 'three';
import {frameSize} from '../../remotion/kit/core/index';
import {onRender} from './scene';

export type RendererOptions = {
  canvas?: HTMLCanvasElement;
  /** where to put the canvas when none is given (default document.body) */
  parent?: HTMLElement;
  /** a transparent clear, so the page shows through (default true) */
  alpha?: boolean;
  antialias?: boolean;
  /** size in px (default: the film's frame, from film.json) */
  width?: number;
  height?: number;
};

/**
 * A WebGL renderer for the film frame: pixel ratio 1 (the page is the film's size), sRGB output, and a drawing
 * buffer that survives until the screenshot.
 */
export function createRenderer(opts: RendererOptions = {}): THREE.WebGLRenderer {
  const size = frameSize();
  const width = opts.width ?? size.width;
  const height = opts.height ?? size.height;
  const renderer = new THREE.WebGLRenderer({
    canvas: opts.canvas,
    alpha: opts.alpha ?? true,
    antialias: opts.antialias ?? true,
    preserveDrawingBuffer: true,
    powerPreference: 'high-performance',
  });
  renderer.setPixelRatio(1);
  renderer.setSize(width, height);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  if (!opts.canvas) (opts.parent ?? document.body).appendChild(renderer.domElement);
  return renderer;
}

/** Draw `scene` through `camera` after every seek (through an Accumulator when one is given). */
export function renderOnSeek(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera, accumulator?: Accumulator): () => void {
  return onRender(() => {
    if (accumulator && camera instanceof THREE.PerspectiveCamera) accumulator.render(scene, camera);
    else renderer.render(scene, camera);
  });
}

/** The i-th point of the Halton sequence in base b (i from 1). */
export function halton(i: number, b: number): number {
  let f = 1;
  let r = 0;
  let n = i;
  while (n > 0) {
    f /= b;
    r += f * (n % b);
    n = Math.floor(n / b);
  }
  return r;
}

export type AccumulatorOptions = {
  /** renders averaged into each drawn frame (default 24) */
  samples?: number;
  /** lens radius in world units; 0 keeps everything sharp and only anti-aliases (default 0.08) */
  aperture?: number;
  /** the point that stays in focus (default: 5 units in front of the camera) */
  focus?: THREE.Vector3 | (() => THREE.Vector3);
};

const QUAD_VERT = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }';

export class Accumulator {
  readonly renderer: THREE.WebGLRenderer;
  samples: number;
  aperture: number;
  focus: THREE.Vector3 | (() => THREE.Vector3) | null;
  private one: THREE.WebGLRenderTarget;
  private sum: THREE.WebGLRenderTarget;
  private readonly add: THREE.ShaderMaterial;
  private readonly out: THREE.ShaderMaterial;
  private readonly quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly addScene = new THREE.Scene();
  private readonly outScene = new THREE.Scene();
  private readonly geo = new THREE.PlaneGeometry(2, 2);

  constructor(renderer: THREE.WebGLRenderer, opts: AccumulatorOptions = {}) {
    this.renderer = renderer;
    this.samples = Math.max(1, Math.round(opts.samples ?? 24));
    this.aperture = opts.aperture ?? 0.08;
    this.focus = opts.focus ?? null;
    const {one, sum} = this.makeTargets();
    this.one = one;
    this.sum = sum;
    this.add = new THREE.ShaderMaterial({
      uniforms: {tex: {value: null}, w: {value: 1}},
      vertexShader: QUAD_VERT,
      fragmentShader: 'uniform sampler2D tex; uniform float w; varying vec2 vUv; void main(){ gl_FragColor = texture2D(tex, vUv) * w; }',
      // pure accumulation: sum += sample × w (additive blending would scale by alpha a second time)
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneFactor,
      depthTest: false,
      depthWrite: false,
      transparent: true,
    });
    this.out = new THREE.ShaderMaterial({
      uniforms: {tex: {value: null}},
      vertexShader: QUAD_VERT,
      fragmentShader: 'uniform sampler2D tex;\nvarying vec2 vUv;\nvoid main() {\n  gl_FragColor = texture2D(tex, vUv);\n  #include <colorspace_fragment>\n}\n',
      depthTest: false,
      depthWrite: false,
    });
    this.addScene.add(new THREE.Mesh(this.geo, this.add));
    this.outScene.add(new THREE.Mesh(this.geo, this.out));
  }

  private makeTargets() {
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    const opts = {type: THREE.HalfFloatType, colorSpace: THREE.LinearSRGBColorSpace, depthBuffer: true, samples: 0};
    return {
      one: new THREE.WebGLRenderTarget(size.x, size.y, opts),
      sum: new THREE.WebGLRenderTarget(size.x, size.y, {...opts, depthBuffer: false}),
    };
  }

  private focusPoint(camera: THREE.PerspectiveCamera): THREE.Vector3 {
    if (typeof this.focus === 'function') return this.focus();
    if (this.focus) return this.focus;
    return camera.getWorldDirection(new THREE.Vector3()).multiplyScalar(5).add(camera.position);
  }

  /** Render `scene` through `camera`, averaged over the lens and the pixel, to the screen. */
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera): void {
    const gl = this.renderer;
    const size = gl.getDrawingBufferSize(new THREE.Vector2());
    if (size.x !== this.one.width || size.y !== this.one.height) {
      this.one.dispose();
      this.sum.dispose();
      const t = this.makeTargets();
      this.one = t.one;
      this.sum = t.sum;
    }
    const W = size.x;
    const H = size.y;
    const base = camera.position.clone();
    const q = camera.quaternion.clone();
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(q);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q);
    const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(q);
    const d = Math.max(0.01, this.focusPoint(camera).clone().sub(base).dot(fwd)); // focus distance along the view axis
    const Hd = 2 * d * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const Wd = Hd * camera.aspect;
    const prevAuto = gl.autoClear;
    const prevTarget = gl.getRenderTarget();
    const prevClear = gl.getClearColor(new THREE.Color());
    const prevAlpha = gl.getClearAlpha();
    gl.autoClear = true;
    gl.setRenderTarget(this.sum);
    gl.setClearColor(0x000000, 0);
    gl.clear(true, false, false);
    gl.setClearColor(prevClear, prevAlpha);
    const n = this.samples;
    for (let i = 0; i < n; i++) {
      // lens point on a golden-angle spiral over the disk; pixel jitter from Halton (2, 3)
      const r = this.aperture * Math.sqrt((i + 0.5) / n);
      const a = i * 2.399963229728653;
      const dx = r * Math.cos(a);
      const dy = r * Math.sin(a);
      const jx = n > 1 ? halton(i + 1, 2) - 0.5 : 0;
      const jy = n > 1 ? halton(i + 1, 3) - 0.5 : 0;
      camera.position.copy(base).addScaledVector(right, dx).addScaledVector(up, dy);
      camera.setViewOffset(W, H, (-dx / Wd) * W + jx, (dy / Hd) * H + jy, W, H);
      camera.updateMatrixWorld();
      gl.setRenderTarget(this.one);
      gl.render(scene, camera);
      this.add.uniforms.tex.value = this.one.texture;
      this.add.uniforms.w.value = 1 / n;
      gl.setRenderTarget(this.sum);
      gl.autoClear = false;
      gl.render(this.addScene, this.quadCam);
      gl.autoClear = true;
    }
    camera.clearViewOffset();
    camera.position.copy(base);
    camera.updateMatrixWorld();
    gl.setRenderTarget(prevTarget);
    this.out.uniforms.tex.value = this.sum.texture;
    gl.render(this.outScene, this.quadCam);
    gl.autoClear = prevAuto;
  }

  dispose(): void {
    this.one.dispose();
    this.sum.dispose();
    this.add.dispose();
    this.out.dispose();
    this.geo.dispose();
  }
}
