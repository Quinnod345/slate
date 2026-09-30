// The network fixture: before it's ready, the scene tries to leave the machine every common way (fetch, an image, a
// WebSocket, a stylesheet in the page, and another server on this very Mac). Every attempt must be refused and
// reported. The frame is green when nothing got through, red when anything did.
const g = window.__SLATE__;
const fps = g.film.fps_num / g.film.fps_den;
const settle = (p) => p.then(() => 'reached', () => 'refused');
async function attempt() {
  const canary = await (await fetch('./canary.json')).json(); // a local file: allowed
  const image = new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve('reached');
    img.onerror = () => resolve('refused');
    img.src = 'https://example.com/pixel.png';
  });
  const socket = new Promise((resolve) => {
    let ws;
    try {
      ws = new WebSocket('ws://example.com/socket');
    } catch {
      resolve('refused');
      return;
    }
    ws.onopen = () => resolve('reached');
    ws.onerror = () => resolve('refused');
    ws.onclose = () => resolve('refused');
    setTimeout(() => resolve('timeout'), 8000);
  });
  window.__attempts = await Promise.all([
    settle(fetch('https://example.com/')),
    settle(fetch(`http://127.0.0.1:${canary.port}/leak`)),
    image,
    socket,
  ]);
}
window.slateScene = {
  duration: g.act.frames / fps,
  fps,
  ready: attempt,
  seek() {
    const clean = (window.__attempts ?? []).every((a) => a !== 'reached');
    document.documentElement.style.background = clean ? 'rgb(0, 160, 0)' : 'rgb(255, 0, 0)';
  },
};
