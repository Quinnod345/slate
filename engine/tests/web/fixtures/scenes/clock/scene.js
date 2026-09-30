// The clock fixture: four bands whose grey level is the time, read four ways, so a test can read it off a frame.
//   a: the t seek() was given          b: performance.now()
//   c: Date.now() from the epoch       d: the timestamp an animation-frame callback receives
// level = BASE + STEP x (time in frames): whole frames are STEP levels apart, and a sub-frame sample shows as a fraction.
// It speaks the raw protocol (no @slate/web), so the renderer's own contract is what's tested.
const g = window.__SLATE__;
const fps = g.film.fps_num / g.film.fps_den;
const EPOCH = 946684800000; // the renderer's default epoch, 2000-01-01 UTC
const BASE = 30;
const STEP = 20;
const paint = (id, seconds) => {
  const v = BASE + STEP * seconds * fps;
  document.getElementById(id).style.background = `rgb(${v}, ${v}, ${v})`;
};
window.slateScene = {
  duration: g.act.frames / fps,
  fps,
  seek(t) {
    paint('a', t);
    paint('b', performance.now() / 1000);
    paint('c', (Date.now() - EPOCH) / 1000);
    requestAnimationFrame((ts) => paint('d', ts / 1000));
  },
};
