// The deterministic clock the renderer puts into every page before any of the page's own scripts run.
//
// A frame of a web scene must depend on its time and nothing else, so the renderer takes the page's sense of time
// away from the wall clock:
//   - Date and performance.now read the frame time (Date counts from a fixed epoch, 2000-01-01 UTC by default);
//   - requestAnimationFrame callbacks queue up and run once per sample, after the seek, with the frame time;
//   - Math.random is a seeded generator (mulberry32), so "random" layouts come out the same on every run;
//   - CSS animations, transitions and Web Animations are paused and set to the frame time (those already running
//     when the scene was ready count from the scene's start; later ones from when the renderer first saw them);
//   - GSAP, when it has put itself on window, gets its lag smoothing turned off, so a jump never slows its clock.
// Timers (setTimeout, setInterval) stay real: libraries use them for plumbing. Never animate with them.
//
// installPageClock is serialised into the page by puppeteer (page.evaluateOnNewDocument), so it must not reach
// outside its own body.

/** Where the page's fixed epoch sits: 2000-01-01T00:00:00Z. Obviously not "now", and the same on every run. */
export const DEFAULT_EPOCH_MS = 946684800000;
export const DEFAULT_SEED = 1;

export function installPageClock(config) {
  const epoch = Number(config && config.epoch) || 0;
  let state = (Number(config && config.seed) >>> 0) || 1;
  const RealDate = Date;
  const realRaf = window.requestAnimationFrame.bind(window);
  const realPerformanceNow = performance.now.bind(performance);
  let now = 0; // ms of scene time
  let started = false;
  let gsapTamed = false;

  // ---------------------------------------------------------------------------------------------------- Date
  function SlateDate(...args) {
    if (!new.target) return new RealDate(epoch + now).toString();
    return args.length ? new RealDate(...args) : new RealDate(epoch + now);
  }
  Object.setPrototypeOf(SlateDate, RealDate);
  SlateDate.prototype = RealDate.prototype;
  SlateDate.now = () => epoch + now;
  SlateDate.parse = RealDate.parse;
  SlateDate.UTC = RealDate.UTC;
  Object.defineProperty(window, 'Date', {value: SlateDate, writable: true, configurable: true});

  // ------------------------------------------------------------------------------------------ performance.now
  Object.defineProperty(performance, 'now', {value: () => now, writable: true, configurable: true});

  // ---------------------------------------------------------------------------------------- animation frames
  let queue = new Map();
  let nextId = 1;
  window.requestAnimationFrame = (cb) => {
    const id = nextId++;
    queue.set(id, cb);
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    queue.delete(id);
  };

  // ----------------------------------------------------------------------------------------------- randomness
  Math.random = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  // ------------------------------------------------------------------------------- CSS and Web Animations
  const firstSeen = new WeakMap();
  function syncAnimations() {
    if (typeof document.getAnimations !== 'function') return 0;
    let n = 0;
    for (const a of document.getAnimations()) {
      let start = firstSeen.get(a);
      if (start === undefined) {
        start = started ? now : 0;
        firstSeen.set(a, start);
      }
      try {
        a.pause();
        a.currentTime = Math.max(0, now - start);
        n++;
      } catch {
        // an animation that can't be paused (already finished and removed) has nothing to show
      }
    }
    return n;
  }

  function tameGsap() {
    const g = window.gsap;
    if (gsapTamed || !g || !g.ticker || typeof g.ticker.lagSmoothing !== 'function') return;
    g.ticker.lagSmoothing(0);
    gsapTamed = true;
  }

  // Images the frame shows must be decoded before the screenshot (an SVG data-URI mask once decoded after it).
  function pendingImages() {
    const out = [];
    for (const img of document.images) {
      if (img.src && typeof img.decode === 'function') out.push(img.decode().catch(() => {}));
    }
    return out;
  }

  Object.defineProperty(window, '__slateClock', {
    configurable: false,
    enumerable: false,
    value: Object.freeze({
      /** Move the clock to `ms` of scene time. */
      set(ms) {
        now = Number(ms) || 0;
        tameGsap();
      },
      now: () => now,
      /** The scene is ready: animations that appear from here on count from when they're first seen. */
      markStarted() {
        started = true;
      },
      /** Run the queued animation-frame callbacks once, at the current time. Returns how many ran. */
      flush() {
        tameGsap();
        const current = queue;
        queue = new Map();
        for (const cb of current.values()) {
          try {
            cb(now);
          } catch (e) {
            setTimeout(() => {
              throw e;
            });
          }
        }
        return current.size;
      },
      syncAnimations,
      /** Fonts ready, images decoded, then two real frames: what the screenshot shows is what was just drawn. */
      async settle() {
        await document.fonts.ready;
        const imgs = pendingImages();
        if (imgs.length) await Promise.all(imgs);
        await new Promise((resolve) => realRaf(() => realRaf(() => resolve())));
      },
      realNow: realPerformanceNow,
    }),
  });
}
