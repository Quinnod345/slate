// A neutral film for the kit's tests and golden stills: 25 fps and 960 × 540 on purpose, so nothing in the kit can
// lean on a 30000/1001 rate or a 1920 × 1080 frame. Two acts, four shots; one read says "64" as two words.
import brand from './neutral-brand.json';

const W = (w: string, start: number, end: number) => ({w, start, end});

export const FILM = {
 film: {id: 'kit-fixture', title: 'Kit fixture', fps_num: 25, fps_den: 1, width: 960, height: 540, language: 'en', brand: 'kit-fixture'},
 shots: {
  fps_num: 25,
  fps_den: 1,
  width: 960,
  height: 540,
  total_frames: 400,
  acts: [
   {index: 1, name: 'Act 1', shots: ['K01', 'K02']},
   {index: 2, name: 'Act 2', shots: ['K03', 'K04']},
  ],
  shots: [
   {id: 'K01', act: 1, frame_in: 0, frame_out: 100, frames: 100, vo: 'Every move lands on its word.', on_screen: 'Every move lands on its word'},
   {id: 'K02', act: 1, frame_in: 100, frame_out: 225, frames: 125, vo: '64 parts move on one clock.', on_screen: '64 parts, one clock'},
   {id: 'K03', act: 2, frame_in: 225, frame_out: 325, frames: 100, vo: 'Nothing here is a brand, and nothing here is a word twice. Nothing.', on_screen: 'Neutral by design'},
   {id: 'K04', act: 2, frame_in: 325, frame_out: 400, frames: 75, vo: 'The end card holds.', on_screen: 'The end'},
  ],
 },
 vo_cues: {
  lead: 0.3,
  cues: [
   {shot: 'K01', at: 0.4},
   {shot: 'K02', at: 0.3},
   {shot: 'K03', at: 0.3},
  ],
 },
 vo_words: {
  shots: {
   K01: {line: 'Every move lands on its word.', words: [W('Every', 0.0, 0.3), W('move', 0.32, 0.6), W('lands', 0.64, 1.0), W('on', 1.02, 1.12), W('its', 1.14, 1.3), W('word.', 1.34, 1.8)]},
   K02: {line: 'Sixty-four parts move on one clock.', words: [W('Sixty', 0.0, 0.34), W('four', 0.34, 0.6), W('parts', 0.66, 1.0), W('move', 1.04, 1.3), W('on', 1.32, 1.42), W('one', 1.46, 1.7), W('clock.', 1.74, 2.2)]},
   K03: {
    line: 'Nothing here is a brand, and nothing here is a word twice. Nothing.',
    words: [W('Nothing', 0.0, 0.4), W('here', 0.42, 0.6), W('is', 0.62, 0.7), W('a', 0.72, 0.76), W('brand,', 0.8, 1.2), W('and', 1.3, 1.44), W('nothing', 1.46, 1.8), W('here', 1.82, 2.0), W('is', 2.02, 2.1), W('a', 2.12, 2.16), W('word', 2.2, 2.5), W('twice.', 2.54, 3.0), W('Nothing.', 3.2, 3.7)],
   },
  },
 },
 spoken: {'64': [['sixty-four'], ['six', 'four']], '&': [['and']]},
 captions: [
  {shot: 'K01', text: 'Every move lands on its word.', lines: ['Every move lands on its word.'], start: 0.4, end: 2.4},
  {shot: 'K02', text: 'Sixty-four parts move on one clock.', lines: ['Sixty-four parts move', 'on one clock.'], start: 4.3, end: 6.7},
  {shot: 'K03', text: 'Nothing here is a brand, and nothing here is a word twice. Nothing.', start: 9.3, end: 13.2},
 ],
 brand,
};

export default FILM;
