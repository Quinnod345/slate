// Act 1's beats, React-free: every move and every sound beat at module level, from the words the voice says. The
// engine runs this same module in Node for emit-events, the motion trace and the hand-in check, so the beats land on
// the frames the picture uses.
import {defineEvent, defineMotion, shot, word} from '@slate/kit/core';

/** Film seconds: "clock" in E01's read, and "64" (said "sixty four") in E02's. */
export const CLOCK = word('E01', 'clock');
export const SIXTY_FOUR = word('E02', '64');

/** The card rises into place over its shot's first four frames, on the brand's entrance ease. */
export const cardRise = defineMotion({id: 'e2e.card-rise', shot: 'E01', prop: 'y', frame_in: shot('E01').frameIn, frame_out: shot('E01').frameIn + 4, from: 24, to: 0, entrance: true});

/** The dot crosses the frame through the second shot, on the brand's move ease. */
export const dotCross = defineMotion({id: 'e2e.dot-cross', shot: 'E02', prop: 'x', frame_in: shot('E02').frameIn, frame_out: shot('E02').frameOut - 1, from: 40, to: 260});

defineEvent({shot: 'E01', id: 'card_lands', t: CLOCK, kind: 'land', size: 's', note: 'the card settles as "clock" is said'});
defineEvent({shot: 'E02', id: 'dot_sets_off', t: SIXTY_FOUR, kind: 'whoosh', size: 's', note: 'the dot sets off across the frame on "64"'});
