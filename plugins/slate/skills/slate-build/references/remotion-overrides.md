# Where Slate overrides Remotion's own skills

Remotion publishes agent skills of its own. Slate installs them inside its engine folder (never globally) so agents
can read Remotion's API guidance, but where they disagree with Slate, Slate wins. Read theirs for API details; follow
Slate for anything below.

| Remotion's skill says | Slate does instead | Why |
|---|---|---|
| Voice-over with an older multilingual TTS model and its settings | Slate's voice stages on the brand's pinned model (slate-voice) | the best current model, pinned per film, with the voice checks |
| Sound effects from a library that points at meme sounds | designed sound from the picture's own events (slate-sound) | sound is designed, never stock |
| Captions from a GPU Whisper that doesn't run everywhere | captions built from the voice's word timings (slate-voice), burned in by the kit in the brand's font | word-exact, on the film's clock, portable |
| Preview in Remotion's Studio | stills and draft renders through `slate render`; the Studio, if a person ever starts it by hand, runs with `--no-open` and never opens a browser | nothing opens a browser |
| `CameraMotionBlur` for motion blur | the renderer's 16-sample average across a 180-degree shutter, in 16-bit | Remotion's blur is 8-bit additive and shifts flat colours |
| `OffthreadVideo` for footage | JPEG sequences played from film time | decoders crash long parallel renders |
| Render with the Remotion CLI | `slate render` (chunked, resumable, proven splices, the job queue, disk reserved, bundle cleaned up) | renders must survive a dying agent and never fill the disk |

## The engine

- Remotion is pinned exactly in Slate's engine (upgraded only after a parity check proves nothing changed, and never to a
  new major version unplanned).
- One bundle per film per source hash, reused for every act and still, then deleted.
- Scenes reach the kit and the film's data by alias (`@slate/kit`, `@film/data`), never by relative paths out of the
  film folder.
- Scenes are type-checked from the film folder with the engine's own TypeScript.

## Licences

Remotion is free for individuals and small teams and needs a company licence above that (slate, licences.md). Slate
never redistributes it; the open web renderer and After Effects are the routes that need no Remotion licence.
