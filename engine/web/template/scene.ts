// The template web scene: a three.js object and one line of text, both moved with the brand's motion law, drawn in the
// brand's tokens. `slate new` copies it into scenes/act<N>/web/ when a brand's look picks the web engine; the tests
// render it standalone. Replace the object, the copy and the moves; keep the shape:
//
//   1. build the picture once, at module level (DOM, three.js, R3F);
//   2. make every move with tween() (it lands on whole film frames and writes the act's motion trace), or on a
//      timeline() for anything the trace doesn't need to see;
//   3. put each beat that wants a sound next to the move that makes it (defineEvent);
//   4. finish with defineScene(): it registers window.slateScene, which the renderer seeks frame by frame.
//
// Nothing here names a brand: colours, type, eases and durations all come from the brand in force, by role and rank,
// so the template works for any pack (and in plain greys for a brand that hasn't been onboarded yet).
import * as THREE from 'three';
import {cssVars, defineEvent, durRank, frameSize, roleEase, t90Of, tokens, typeStyle} from '@slate/kit/core';
import {actStart, defineScene, sceneAct, tween} from '@slate/web';
import {createRenderer, renderOnSeek} from '@slate/web/three';

const size = frameSize();
const t = tokens();
const act = sceneAct();
const shot = act.shots[0];
const start = actStart(); // film seconds of the act's first frame

// ---------------------------------------------------------------------------------------------------- the page
for (const [name, value] of Object.entries(cssVars(t, size.short))) document.documentElement.style.setProperty(name, value);
document.body.style.background = t.color.page;
document.body.style.width = `${size.width}px`;
document.body.style.height = `${size.height}px`;

const line = document.getElementById('line') as HTMLElement;
const type = typeStyle(t, 'h1', size.short, t.color.ink);
Object.assign(line.style, {
  fontFamily: type.fontFamily,
  fontWeight: String(type.fontWeight),
  fontSize: `${type.fontSize}px`,
  lineHeight: String(type.lineHeight),
  letterSpacing: type.letterSpacing,
  textTransform: type.textTransform,
  color: type.color,
});
line.textContent = 'One clock for every frame.';

// ---------------------------------------------------------------------------------------------- the 3D object
const renderer = createRenderer({parent: document.getElementById('stage') as HTMLElement});
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(32, size.width / size.height, 0.1, 100);
camera.position.set(0, 0, 7);
const mesh = new THREE.Mesh(
  new THREE.IcosahedronGeometry(1.05, 1),
  new THREE.MeshStandardMaterial({color: t.color.accent, roughness: 0.5, metalness: 0.05, flatShading: true}),
);
mesh.position.set(0, 0.45, 0);
scene.add(mesh);
scene.add(new THREE.HemisphereLight(t.color.surface, t.color.ink, 1.6));
const key = new THREE.DirectionalLight(t.color.surface, 2.2);
key.position.set(2.5, 3, 4);
scene.add(key);

// ------------------------------------------------------------------------------------------------- the moves
// The object turns from rest to rest over the brand's longest duration (its "move" role ease); the line enters a tick
// after the act starts (its "entrance" role ease), from a short drop and nothing scaled.
const turn = durRank(5);
tween(mesh.rotation, {y: [-0.7, 0.7]}, {id: 'template.object-turn', shot, at: start, dur: turn, prop: 'rotation.y', unit: 'rad'});
const enter = durRank(2);
const lineIn = start + durRank(0);
tween(line, {opacity: [0, 1], y: [Math.round(size.short * 0.03), 0]}, {id: 'template.line-in', shot, at: lineIn, dur: enter, entrance: true, unit: 'px'});

// The line's landing is a beat: "land" means 90% of travel, everywhere in Slate.
defineEvent({shot, id: 'template.line-lands', t: lineIn + t90Of(roleEase('entrance')) * enter, kind: 'land', size: 's', note: 'the line of text settles into place'});

renderOnSeek(renderer, scene, camera);
defineScene();
