// '@slate/web': the runtime every web scene imports.
//
//   env      where the scene is: its film, act and frames (window.__SLATE__), act time ↔ film time
//   scene    defineScene(): window.slateScene = {duration, fps, seek(t), ready()}, timelines seeked per frame, hooks
//   motion   brand eases for GSAP with exact endpoints, and tween(), which writes the act's motion trace
//   media    image sequences for plates and captures (one image per film frame, never <video>)
//   fonts    the web font gate (the kit's width test, faces loaded locally)
//
// three.js helpers live at '@slate/web/three' and React Three Fiber's at '@slate/web/r3f', so a scene that uses
// neither doesn't bundle them. The kit's React-free core ('@slate/kit/core': tokens, the motion law, word timings,
// defineEvent) and the film's data ('@film/data') are available to every scene too.
export * from './env';
export * from './scene';
export * from './motion';
export * from './media';
export * from './fonts';
