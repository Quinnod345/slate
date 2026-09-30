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
| `handin.act<N>.events` | the events file exists, is valid, and matches what the act says now |
| `handin.act<N>.contracts` | the act's boundary frames are bit-identical to its neighbours' and the rest state, and held |
| `handin.act<N>.fonts` | the brand's faces loaded (a substituted font fails) |

Only PASS counts. A gate that couldn't run is UNCHECKED, never a quiet pass. `slate timing misses` lists any word
lookups that fell back to an estimate. `slate check all` checks every built act.

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
