/*
 * Slate's virtual clock for web captures.
 *
 * Injected at the very start of a captured page (before its own scripts run), it replaces the page's sense of time:
 * Date, performance.now, setTimeout/setInterval and requestAnimationFrame all follow a virtual clock that moves only
 * when the capture says so (window.__slateClock.advanceTo(ms)). CSS animations, transitions and media are paused and
 * set to the same time. So every frame of a capture is taken at exactly its film time, however long a screenshot
 * takes, and the capture has exactly the film's frame rate.
 *
 * `realRaf` and `realSetTimeout` keep the browser's own, so the capture can still wait for a real paint or a real
 * timeout (fonts and images load in real time).
 */
(function () {
  'use strict';
  if (window.__slateClock) return;
  var RealDate = Date;
  var realRaf = window.requestAnimationFrame.bind(window);
  var realSetTimeout = window.setTimeout.bind(window);
  var epoch = RealDate.now();
  var vnow = 0;
  var timers = [];
  var timerSeq = 0;
  var rafs = [];
  var rafSeq = 0;
  var starts = new WeakMap();

  function VDate(a, b, c, d, e, f, g) {
    if (!(this instanceof VDate)) return new RealDate(epoch + vnow).toString();
    switch (arguments.length) {
      case 0: return new RealDate(epoch + vnow);
      case 1: return new RealDate(a);
      default: return new RealDate(a, b, c === undefined ? 1 : c, d || 0, e || 0, f || 0, g || 0);
    }
  }
  VDate.prototype = RealDate.prototype;
  VDate.now = function () { return epoch + vnow; };
  VDate.parse = RealDate.parse;
  VDate.UTC = RealDate.UTC;
  window.Date = VDate;

  try {
    Object.defineProperty(performance, 'now', { value: function () { return vnow; }, configurable: true });
  } catch (err) {
    performance.now = function () { return vnow; };
  }

  window.setTimeout = function (fn, ms) {
    var args = Array.prototype.slice.call(arguments, 2);
    var id = ++timerSeq;
    timers.push({ id: id, at: vnow + Math.max(0, Number(ms) || 0), fn: fn, args: args, every: 0 });
    return id;
  };
  window.setInterval = function (fn, ms) {
    var args = Array.prototype.slice.call(arguments, 2);
    var id = ++timerSeq;
    var every = Math.max(1, Number(ms) || 0);
    timers.push({ id: id, at: vnow + every, fn: fn, args: args, every: every });
    return id;
  };
  window.clearTimeout = window.clearInterval = function (id) {
    timers = timers.filter(function (t) { return t.id !== id; });
  };
  window.requestAnimationFrame = function (cb) {
    var id = ++rafSeq;
    rafs.push({ id: id, cb: cb });
    return id;
  };
  window.cancelAnimationFrame = function (id) {
    rafs = rafs.filter(function (r) { return r.id !== id; });
  };

  // A CSS animation, transition or media element starts on the virtual clock when Slate first sees it, which is at
  // the virtual moment it was created (adopt runs after every timer and frame callback). Real time spent loading or
  // taking screenshots never shows up in a frame.
  function adopt() {
    if (document.getAnimations) {
      document.getAnimations().forEach(function (a) {
        if (!starts.has(a)) {
          starts.set(a, vnow);
          try { a.pause(); } catch (err) { /* already finished */ }
        }
      });
    }
    var media = document.querySelectorAll('video,audio');
    for (var i = 0; i < media.length; i++) {
      if (!starts.has(media[i])) {
        starts.set(media[i], vnow);
        try { media[i].pause(); } catch (err) { /* not ready */ }
      }
    }
  }

  function runTimers(target) {
    for (var guard = 0; guard < 100000; guard++) {
      var next = null;
      for (var i = 0; i < timers.length; i++) {
        var t = timers[i];
        if (t.at <= target && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) next = t;
      }
      if (!next) return;
      vnow = Math.max(vnow, next.at);
      if (next.every) next.at += next.every;
      else timers = timers.filter(function (t2) { return t2 !== next; });
      try {
        if (typeof next.fn === 'function') next.fn.apply(window, next.args);
        else (0, eval)(String(next.fn));
      } catch (err) { console.error(err); }
      adopt();
    }
  }

  function syncMedia() {
    adopt();
    if (document.getAnimations) {
      document.getAnimations().forEach(function (a) {
        try { a.pause(); a.currentTime = vnow - starts.get(a); } catch (err) { /* finished or detached */ }
      });
    }
    var media = document.querySelectorAll('video,audio');
    for (var i = 0; i < media.length; i++) {
      try { media[i].pause(); media[i].currentTime = (vnow - starts.get(media[i])) / 1000; } catch (err) { /* not seekable */ }
    }
  }

  window.__slateClock = {
    version: 1,
    realRaf: realRaf,
    realSetTimeout: realSetTimeout,
    now: function () { return vnow; },
    advanceTo: function (ms) {
      if (ms < vnow) return vnow;
      runTimers(ms);
      vnow = ms;
      var due = rafs;
      rafs = [];
      due.forEach(function (r) { try { r.cb(vnow); } catch (err) { console.error(err); } });
      adopt();
      syncMedia();
      return vnow;
    },
  };
})();
