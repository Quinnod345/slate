# Handing an act in

An act is handed in when its hand-in check passes and its checklist is done, not when it looks finished in a preview.

## The check

```
slate emit-events --act <N>
slate check <N>
```

| Gate | What it proves |
|---|---|
| `handin.act<N>.typecheck` | TypeScript, run from the film folder, over the act and everything it imports |
| `handin.act<N>.module` | the act's modules load in Node without throwing (a throw at load breaks every composition in a bundle) |
| `handin.act<N>.word_misses` | every word lookup found its word; none fell back to an estimate |
| `handin.act<N>.kit_problems` | what the kit had to assume or refuse (an unknown ease name, a missing token), for a person to read |
| `handin.act<N>.banned_words` | none of the brand's banned words in the act's strings or its shots' on-screen text |
| `handin.act<N>.never_claim` | every on-screen line that uses something the brand or the film never claims, printed for you to read |
| `handin.act<N>.literals` | no literal film or act frame numbers in the act's sources: every time is keyed to a word, a shot or a contract constant |
| `handin.act<N>.margins` | every timing margin the act records (the kit's `margin()`: the slack between beats the picture depends on) is positive, and the rig lint is clean; `slate emit-events` writes nothing until both hold |
| `handin.act<N>.character` | each mark the act registers (`registerMotifTrace`) passes the character's acceptance checks: calm at both ends and after every gesture, still between gestures, clear of the split point and of every clip |
| `handin.act<N>.events` | the events file exists, is valid, and matches what the act says now |
| `handin.act<N>.contracts` | the act's boundary frames are bit-identical to its neighbours' and the rest state, and held |
| `handin.act<N>.fonts` | the brand's faces loaded (a substituted font fails) |
| `handin.act<N>.motion` | the act's motion trace keeps the brand's motion law: every check `slate qa motion` runs on a trace (eases, durations, staggers, entrances, settled type, speed and acceleration, loops, rig symmetry), with no pixels, so a law the act breaks fails here before anything renders. The trace must be the act as it is now: once its sources change, emit again. An act with no trace (drawn without the kit's motion registry) has nothing for it to read; `slate qa motion` judges it from its render |

Only PASS counts. A gate that couldn't run is UNCHECKED, never a quiet pass. `slate timing misses` lists any word
lookups that fell back to an estimate. `slate check all` checks every built act, and `slate --dry-run check <N>` says
what it would run without running it. Two checks run when you ask for them:
`slate check <N> --markup` reads the act's static markup at every frame and four sub-frame samples (NaN, Infinity or
undefined fails; with `--against <git ref or kit folder>` a rendering-only change proves it changes nothing), and
`--determinism` renders the act's first frames alone and in sequence and fails on the first frame that differs. A web
shot inside a Remotion act is handed in with its act (`handin.act<N>.shot.<ID>.*`). After a re-voice, emit-events
reports what moved in `timing/visual_events/changes/act<N>.json`.

## Handing in a web act

An act built for the open web renderer (`scenes/act<N>/web/`, started with `slate engine scaffold --act <N>`) hands in
with the same `slate emit-events --act <N>` and `slate check <N>`, through that renderer. Its scene scripts are
type-checked from the film folder like any act, and its page is opened once in a dry-run emit (nothing written), which
hands back its beats, its word lookups and what the kit had to assume. On top of the gates above:

| Gate | What it proves |
|---|---|
| `handin.act<N>.page` | the page loads and draws its first frame in the open web renderer without an error (a page the renderer can't open is UNCHECKED, with its next step) |
| `handin.act<N>.network` | the page reached for nothing outside its own folders (the renderer can't reach the network, and a request that tried is a finding) |
| `handin.act<N>.fonts` | the brand's faces loaded in the page (a substituted font fails) |

Its banned words are read from its scripts, its page's text and its copy files. A web act's boundary frames aren't yet
compared with a neighbour's declared rest state (the contracts gate says UNCHECKED when asked), so check those joins by
eye and with `slate render stills` until it is.

## Checking your work

- **Stills**: `slate render stills --act <N> --frames <f1,f2,…> --scale 0.5`. Put them on a contact sheet and look hard:
  hierarchy, emptiness, legibility, the brand's look, sync with the words.
- **A draft preview**: `slate render --act <N> --draft --out <your scratch folder>/act<N>.mov` (4 motion-blur samples
  instead of 16; not registered as the act's render).
- **Previews and stills go in your own scratch folder**, never in the film folder or another builder's.
- **Scan previews, not just stills**: some renderer faults show only in sequence (a card face dropping for two frames).
- **Confirm any flicker at full size before fixing it**: half-scale previews can flicker a level or two on slowly
  drifting cards because the browser picks a different raster scale.
- **Review every transition of a product surface at 2 to 4 frame steps** as well as at full speed; a one-frame scan can't
  see a card that stays wrong.
- **Pixel-diff every hand-over** at full resolution: a flat card to its 3D twin, a morph to its final component, frames
  at rest.

Full-resolution finals are rendered centrally, one at a time, through the job queue (slate-finish). A builder never
starts one.

## The hand-in checklist

- [ ] Every shot's on-screen text matches the script's on-screen column (restaging the picture is fine; NOTES.md says
      so).
- [ ] No banned words; the claims and sample-data rules hold (slate-story, truth-packs).
- [ ] Read times hold, and body text is at least 28 px at a 1080-line frame.
- [ ] Both contracts match, held at least 6 frames.
- [ ] Any character is calm at the act's ends; its track lints clean.
- [ ] The word-miss list is empty.
- [ ] The events file lists every beat that lands (or the act says why it's silent).
- [ ] `slate check <N>` passes.
- [ ] NOTES.md says what you decided, what you restaged and what you couldn't do, and the brief's words were re-read
      against the act's checklist.
