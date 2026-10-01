// Act 2's beats, React-free: every move and every sound beat at module level, from the words the voice says. The
// engine runs this same module in Node for emit-events, the motion trace and the hand-in check, so the beats land on
// the frames the picture uses.
import {defineEvent, defineMotion, landOn, word} from '@slate/kit/core';

/** Film seconds: "checked" in D03's read, and "Studio" in D04's. */
export const CHECKED = word('D03', 'checked');
export const STUDIO = word('D04', 'Studio');

/** The card rises into place and lands as "checked" is said, on the brand's entrance ease and its base duration. */
export const cardRise = defineMotion({id: 'demo.card-rise', shot: 'D03', prop: 'y', ...landOn(CHECKED, 'base'), from: 48, to: 0, entrance: true});
export const cardIn = defineMotion({id: 'demo.card-in', shot: 'D03', prop: 'opacity', ...landOn(CHECKED, 'base'), from: 0, to: 1, entrance: true});

/** The studio's name rises under the card and lands on "Studio". */
export const nameRise = defineMotion({id: 'demo.name-rise', shot: 'D04', prop: 'y', ...landOn(STUDIO, 'base'), from: 32, to: 0, entrance: true});
export const nameIn = defineMotion({id: 'demo.name-in', shot: 'D04', prop: 'opacity', ...landOn(STUDIO, 'base'), from: 0, to: 1, entrance: true});

defineEvent({shot: 'D03', id: 'card_lands', t: CHECKED, kind: 'land', size: 'm', note: 'the card settles as "checked" is said'});
defineEvent({shot: 'D04', id: 'name_lands', t: STUDIO, kind: 'land', size: 's', note: 'the studio name settles on "Studio"'});
