// A scene that breaks part-way: seek throws from 0.1 s on (frame 3 at 25 fps). The render must stop and name the frame.
const g = window.__SLATE__;
const fps = g.film.fps_num / g.film.fps_den;
window.slateScene = {
  duration: g.act.frames / fps,
  fps,
  seek(t) {
    if (t > 0.1) throw new Error('the fixture breaks here on purpose');
    document.body.style.background = 'rgb(40, 40, 40)';
  },
};
