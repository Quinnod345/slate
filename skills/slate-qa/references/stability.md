# Render stability: what a viewer feels before they can name it

The `stability` gate watches every act's render, frame by frame and tile by tile at full resolution, for the faults
that read as judder: pops (a step that holds), whole-pixel snaps, shakes, reversals, wobbles, flicker and shimmer. It
runs with `slate qa` like any gate (it's one of the slow ones `--quick` skips) and has its own command for its tools.

## What counts: the triage order

Each flag is triaged in this order, so what's left is what a person has to look at:

1. **Planned beats.** A pop within 3 frames of a planned beat is the beat, not a fault. Only pops: a motion fault near a
   beat still counts.
2. **The reference render.** A flag the act's reference render shares (same class, within 2 frames and one tile) isn't
   new.
3. **The act's baseline.** Flags a person looked at and accepted, with their name and reason. A baseline survives
   re-renders.
4. **Limits.** New flags an act may carry per class, from `qa.stability.limits` in slate.toml: 0 for every class by
   default, the bar for a final.

A whole-pixel reversal always fails, whatever the triage says. The open flags are REVIEW items keyed
`<class>:frame:<film frame>` (they show in `slate qa triage`), with before-and-after strips in `qa/stability/strips/`.

## The commands

```
slate qa stability                                    # the gate on every act (recorded in qa/gates.json)
slate qa stability --act 3                            # one act: reports what it finds, can't pass the gate
slate qa stability --quick                            # reuse the scans already made; never starts one
slate qa stability baseline --act 3 --by <name> --reason "<why>"   # accept act 3's open flags (never a reversal)
slate qa stability reference --act 3 <render>         # the render act 3 is compared with
slate qa stability strip --act 3 --frame 1166         # a before-and-after strip around one frame, for eyes
slate qa stability compare --act 3 --rest-only        # a render-only change against the approved render
slate qa stability selftest                           # the scan's own positive control
slate qa stability controls <controls.json>           # real faults the scan must still catch
slate qa stability conformance <renders folder>       # the engine kit's stability bench, checked
slate qa stability conformance --render --detach      # render the bench's variants on the render slot, then check them
```

- **baseline** needs the person's name and reason, and only accepts flags from a report of the act's current render: a
  person accepts what they looked at. Say why in the act's NOTES.md too.
- **compare** is for a re-render that should change the rendering only (more motion-blur samples, a new engine): nothing
  moved, missing or doubled, and nothing softer at rest. It compares with the act's reference unless you name the
  approved render.
- **strip** centres on the worst flag at the frame unless `--box x,y,w,h` says where; `--count` and `--zoom` set its
  length and size.
- **conformance** checks the kit itself, not a film: the stability bench (`engine/remotion/kit/bench`) draws one card
  with type, a curve and a chip through slow pushes, landings, zooms, free type and morphing cards, in both raster
  modes, at 16 samples through the real render path. Run it after any change to the kit's `paper.tsx`, `type.tsx`,
  `camera.tsx` or the renderer, and before any act converts to tilt mode: no act converts until it passes.
- **selftest** and **controls** prove the scan still sees: faults injected into a still land on their frames, and the
  real faults in a controls file are still caught. Run them after changing the scan or its settings.

The motion scan uses a quarter of the cores, at most 4, unless `--jobs` (or `qa.stability.jobs` in slate.toml) says
otherwise. `--detach` queues the scan as a job.
