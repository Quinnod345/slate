// Plates and captures in a web scene: image sequences, one image per film frame, never <video>.
//
// A browser can't seek a video to an exact frame on demand: decoders skip frames, or sit at readyState 0 under load.
// Slate already conforms plates and captures to image sequences at the film's rate (the plate path), so a web scene
// shows them one image per frame. Every motion-blur sample of a frame shows that frame's image; the footage carries
// its own blur.
import {sceneAct} from './env';
import {onSeek, type SeekInfo} from './scene';

export type SequenceOptions = {
  /**
   * Where the images are: a folder of the film ('captures/app/frames', served read-only at /_slate/film/), a folder
   * beside the scene ('./frames'), or a path on the local server ('/_slate/brand/…').
   */
  dir: string;
  /** file names, printf-style (default 'f%05d.jpg') */
  pattern?: string;
  /** the film frame the first image belongs to (default: the act's first frame) */
  filmFrameIn?: number;
  /** the number in the first image's name (default 0) */
  first?: number;
  /** how many images there are; frames past the end hold the last one, frames before the start the first */
  count?: number;
};

function fileName(pattern: string, n: number): string {
  return pattern.replace(/%(0?)(\d*)d/, (_m, zero: string, width: string) => {
    const s = String(n);
    const w = Number(width || 0);
    return zero && s.length < w ? s.padStart(w, '0') : s;
  });
}

function base(dir: string): string {
  const clean = dir.replace(/\/+$/, '');
  if (clean.startsWith('./') || clean.startsWith('../') || clean.startsWith('/')) return clean;
  return `/_slate/film/${clean.split('/').map(encodeURIComponent).join('/')}`;
}

/** The URL of the image a film frame shows. */
export function sequenceUrl(opts: SequenceOptions, filmFrame: number): string {
  const first = opts.first ?? 0;
  const start = opts.filmFrameIn ?? sceneAct().frameIn;
  let n = first + Math.round(filmFrame - start);
  if (n < first) n = first;
  if (opts.count !== undefined && n > first + opts.count - 1) n = first + Math.max(0, opts.count - 1);
  return `${base(opts.dir)}/${fileName(opts.pattern ?? 'f%05d.jpg', n)}`;
}

/**
 * Show a sequence in an <img>: on every seek the image switches to the frame's own picture and is decoded before the
 * screenshot. Returns a function that stops it.
 */
export function bindSequence(img: HTMLImageElement, opts: SequenceOptions): () => void {
  img.decoding = 'sync';
  return onSeek(async (_t: number, info: SeekInfo) => {
    const url = sequenceUrl(opts, sceneAct().frameIn + info.frame);
    if (img.getAttribute('src') !== url) {
      img.src = url;
      await img.decode().catch(() => {
        throw new Error(`the sequence image ${url} didn't load (is it in the film folder?)`);
      });
    }
  });
}
