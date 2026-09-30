// A half-written act: a word the read never says, an ease the brand doesn't have, and an event of an unknown kind.
// It must still load (nothing throws at module level) and list every problem.
import {defineEvent, defineMotion, word} from '@slate/kit/core';

export const MISSING = word('K03', 'elephant');

export const wobble = defineMotion({id: 'wobble', shot: 'K03', prop: 'x', ease: 'bouncy', frame_in: 230, frame_out: 250, from: 0, to: 10});

defineEvent({shot: 'K03', id: 'boom', t: MISSING, kind: 'kaboom', size: 'm'});
