// The tween fixture: one bar slides 200 px on the brand's "move" ease between film frames 5 and 15, and lands with a
// beat. The test reads the bar's edge off every rendered frame and holds it against the motion trace the render wrote.
import {defineEvent, secOf} from '@slate/kit/core';
import {defineScene, tween} from '@slate/web';

const bar = document.getElementById('bar') as HTMLElement;
tween(bar, {x: [0, 200]}, {id: 'bar.slide', shot: 'W01', frame_in: 5, frame_out: 15, ease: 'move', unit: 'px'});
defineEvent({shot: 'W01', id: 'bar.lands', t: secOf(15), kind: 'land', size: 's', note: 'the bar comes to rest on the right'});
defineScene();
