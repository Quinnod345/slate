// '@slate/web' by name for the engine's own type check (npm run typecheck), which maps '@slate/kit' but not yet
// '@slate/web'. Scenes import the runtime by name and the renderer's bundler resolves it (lib/bundle.mjs); these
// declarations let tsc follow the same names through the kit's mapping ('@slate/kit/*' is engine/remotion/kit/*).
declare module '@slate/web' {
  export * from '@slate/kit/../../web/runtime/index';
}
declare module '@slate/web/three' {
  export * from '@slate/kit/../../web/runtime/three';
}
declare module '@slate/web/r3f' {
  export * from '@slate/kit/../../web/runtime/r3f';
}
