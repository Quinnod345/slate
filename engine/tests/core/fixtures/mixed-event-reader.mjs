// This neutral reader is an in-memory boundary double. It cannot start a browser, renderer or network request.
export const calls = [];
let page = null;

export function configure(value) {
  page = value;
  calls.length = 0;
}

export async function webRender(args) {
  if (!page) throw new Error('The neutral test did not configure its page response.');
  calls.push(structuredClone(args));
  return {acts: [structuredClone(page)], skipped: []};
}
