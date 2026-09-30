// The kit's review entry: bundle this (with `@slate/kit` and `@film/data` aliased) to render review sheets and the
// golden stills. An entry for a brand whose fonts come from Google Fonts imports and registers them first, then
// registers this same root:
//   import {loadFont} from '@remotion/google-fonts/Inter';
//   import {registerGoogleFont} from '@slate/kit';
//   registerGoogleFont('Inter', loadFont);
//   registerRoot(KitReviewRoot);
import {registerRoot} from 'remotion';
import {KitReviewRoot} from './root';

registerRoot(KitReviewRoot);
