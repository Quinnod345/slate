#!/bin/sh
# The Chrome Slate's Remotion renders launch (render_mb, stills, the hand-in check's contract stills): Remotion's own
# chrome-headless-shell from this engine's node_modules/.remotion, told to finish every compositor stage before it
# draws a frame. --run-all-compositor-stages-before-draw makes it rasterise every tile, and --disable-checker-imaging
# decode every image, before the screenshot is taken. Without them a tile that isn't ready in time is simply left out
# of that sub-frame, and whatever is behind it shows through for one sample in sixteen: a faint grey veil for one frame
# (the films lost a 256 px band of a chat panel's face that way on 2026-09-30). It costs about 6% more render time.
#
# Remotion's openBrowser takes no extra Chrome flags, so the flags ride on the executable, as the films' renderer did.
# The open web renderer passes the same two flags itself (engine/web/lib/browser.mjs).
here=$(cd "$(dirname "$0")" && pwd)
for shell in "$here"/../node_modules/.remotion/chrome-headless-shell/*/chrome-headless-shell-*/chrome-headless-shell; do
  if [ -x "$shell" ]; then
    exec "$shell" --run-all-compositor-stages-before-draw --disable-checker-imaging "$@"
  fi
done
echo "chrome-complete.sh: Remotion's chrome-headless-shell isn't in $here/../node_modules/.remotion; run \`node cli.mjs browser-ensure\` (or \`slate doctor --install\`)." >&2
exit 127
