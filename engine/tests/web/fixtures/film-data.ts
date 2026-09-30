// The web fixture film as '@film/data' for the runtime's unit tests (vitest aliases it): 25 fps and 320 x 180, one act
// of two shots, in the neutral web-fixture brand. The browser tests read the same film from fixtures/film/.
import brand from './brand/brand.json';

const shots = {
 fps_num: 25,
 fps_den: 1,
 width: 320,
 height: 180,
 total_frames: 50,
 acts: [{index: 1, name: 'One', shots: ['W01', 'W02']}],
 shots: [
  {id: 'W01', act: 1, frame_in: 0, frame_out: 25, frames: 25, sec_in: 0, sec_out: 1, vo: '', on_screen: ''},
  {id: 'W02', act: 1, frame_in: 25, frame_out: 50, frames: 25, sec_in: 1, sec_out: 2, vo: '', on_screen: ''},
 ],
};

export const FILM = {
 film: {id: 'web-fixture', title: 'Web renderer fixture', fps_num: 25, fps_den: 1, width: 320, height: 180, language: 'en', brand: 'web-fixture'},
 shots,
 brand,
};

export default FILM;
