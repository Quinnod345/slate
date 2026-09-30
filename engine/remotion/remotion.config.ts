// Remotion's own settings, for the rare time a person starts Remotion's tools by hand (Slate's commands pass these
// options themselves). Use it with --config=remotion/remotion.config.ts from the engine folder:
//
//   BROWSER=none npx --no-install remotion studio <film>/.slate/entry.tsx --no-open --config=remotion/remotion.config.ts
//
// Studio never opens a browser (this Mac's default browser is a personal one, and agents never drive it); previews
// are stills and renders, or a page opened through Aside.
import {Config} from '@remotion/cli/config';

Config.setShouldOpenBrowser(false);
// The GPU through ANGLE (Metal on a Mac), as the films rendered. Pixel contracts and golden stills use 'swangle'.
Config.setChromiumOpenGlRenderer('angle');
// Lossless frames: the ProRes (or lossless chunk) encode is the only compression step.
Config.setVideoImageFormat('png');
Config.setOverwriteOutput(true);
