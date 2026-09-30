// The edges fixture: one bar stands still (top), one moves 16 px a frame (bottom). Averaging sub-frames must blur the
// moving bar's edges and leave the still bar's pixels exactly as they are.
const g = window.__SLATE__;
const fps = g.film.fps_num / g.film.fps_den;
const moving = document.getElementById('moving');
window.slateScene = {
  duration: g.act.frames / fps,
  fps,
  seek(t) {
    moving.style.transform = `translateX(${16 * t * fps}px)`;
  },
};
