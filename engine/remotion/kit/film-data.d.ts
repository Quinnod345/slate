// `@film/data` is generated per film by the engine (docs/ARCHITECTURE.md §8): one module whose default export is the
// film's resolved data (core/types.ts FilmData). The kit reads it as unknown and checks its shape at runtime, so a
// film's generated tsconfig can point this alias at the real module without the kit changing.
declare module '@film/data' {
  const data: unknown;
  export default data;
}
