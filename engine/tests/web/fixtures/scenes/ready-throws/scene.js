// A scene that breaks while it gets ready, the way a React tree does when a component throws: the error surfaces on
// its own and ready() never settles. The renderer must stop at the error, not wait out its timeout.
const g = window.__SLATE__;
const fps = g.film.fps_num / g.film.fps_den;
window.slateScene = {
  duration: g.act.frames / fps,
  fps,
  ready() {
    setTimeout(() => {
      throw new Error('the fixture fails while it gets ready');
    }, 50);
    return new Promise(() => {});
  },
  seek() {},
};
