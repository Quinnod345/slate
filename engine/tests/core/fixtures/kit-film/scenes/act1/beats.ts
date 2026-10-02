// Act 1's beats, React-free: every move and every sound beat at module level, from the words the voice says. The
// engine runs this same module in Node for emit-events, the motion trace and the hand-in check, so the beats land on
// the frames the picture uses.
//
// Both moves keep the motion law at this film's size, which the hand-in checks: each lasts a tick (the brand's shortest
// step, 5 frames at 25 fps) and travels as far as its ease allows inside a UI element's limits at 320 x 180 (10 px a
// frame, and 2.7 px a frame² of change in speed: 60 and 16 at 1080p).
import {defineEvent, defineMotion, durFrames, shot, word} from '@slate/kit/core';

/** Film seconds: "clock" in E01's read, and "64" (said "sixty four") in E02's. */
export const CLOCK = word('E01', 'clock');
export const SIXTY_FOUR = word('E02', '64');

/**
 * The card rises into place from its shot's first frame, over a tick on the brand's entrance ease. Its 4 px (24 px at
 * 1080p) are the most that ease's firm start allows here: it covers 64% of its travel in the first frame.
 */
export const cardRise = defineMotion({id: 'e2e.card-rise', shot: 'E01', prop: 'y', frame_in: shot('E01').frameIn, frame_out: shot('E01').frameIn + durFrames('tick'), from: 4, to: 0, entrance: true});

/** The dot sets off along its line on "64": 17 px over a tick on the brand's move ease, a sine that starts at rest. */
export const dotSlide = defineMotion({id: 'e2e.dot-slide', shot: 'E02', prop: 'x', frame_in: shot('E02').frameIn, frame_out: shot('E02').frameIn + durFrames('tick'), from: 40, to: 57});

defineEvent({shot: 'E01', id: 'card_lands', t: CLOCK, kind: 'land', size: 's', note: 'the card settles as "clock" is said'});
defineEvent({shot: 'E02', id: 'dot_sets_off', t: SIXTY_FOUR, kind: 'whoosh', size: 's', note: 'the dot sets off along its line on "64"'});
