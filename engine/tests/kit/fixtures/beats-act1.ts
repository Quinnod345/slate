// A clean act's beats, the way a scene's beats module is written: every beat at module level, from the read's words.
import {defineEvent, defineMotion, landOn, word} from '@slate/kit/core';

export const LANDS = word('K01', 'lands');
export const SIXTY_FOUR = word('K02', '64');

export const titleRise = defineMotion({id: 'title-rise', shot: 'K01', prop: 'y', ...landOn(LANDS, 'base', 'enter'), from: 24, to: 0, entrance: true});
export const titleScale = defineMotion({id: 'title-scale', shot: 'K01', prop: 'scale', ...landOn(LANDS, 'base', 'enter'), from: 0.9, to: 1, entrance: true});
export const partsCarry = defineMotion({id: 'parts-carry', shot: 'K02', prop: 'position', ...landOn(SIXTY_FOUR, 'camera', 'move'), from: [0, 0], to: [120, -40]});

defineEvent({shot: 'K01', id: 'title_lands', t: LANDS, kind: 'type', size: 'l', note: 'the title lands on "lands"'});
defineEvent({shot: 'K02', id: 'parts_arrive', t: SIXTY_FOUR, kind: 'land', size: 'm', note: 'the parts arrive on "64"'});
