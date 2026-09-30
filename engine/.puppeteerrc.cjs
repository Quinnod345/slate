// Slate's engine uses one headless Chrome: Remotion's chrome-headless-shell (the web renderer passes its path to
// puppeteer). Never let puppeteer download its own Chrome on install; it's large and unused.
module.exports = { skipDownload: true };
