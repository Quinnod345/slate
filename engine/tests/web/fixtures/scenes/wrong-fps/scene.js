// A scene that insists on its own frame rate (24 fps against the film's 25). The film clock wins: the renderer refuses it.
window.slateScene = {fps: {num: 24, den: 1}, seek() {}};
