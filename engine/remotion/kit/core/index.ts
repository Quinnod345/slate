// `@slate/kit/core`: everything in the kit that needs no React and no browser. An act's beats module imports from
// here, so the engine can load it in Node (emit-events, the hand-in check, the motion trace) exactly as the picture
// reads it.
export * from './types';
export * from './version';
export * from './film';
export * from './brand';
export * from './color';
export * from './eases';
export * from './spline';
export * from './motion';
export * from './events';
export * from './tokens';
export * from './fontplan';
export * from './looks';
export * from './svg';
export * from './logo';
export * from './motif';
export * from './margins';
export * from './captions';
