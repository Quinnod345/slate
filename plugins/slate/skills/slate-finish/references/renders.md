# Renders

## What a final render is

- Each act is rendered on its own, one at a time, with **true motion blur**: every frame drawn 16 times across a
  180-degree shutter centred on the frame, the samples averaged in 16-bit, then encoded as ProRes 422 HQ, 10-bit 4:2:2,
  BT.709, at the film's own rate and size.
- Draft renders use 4 samples (`--draft`).
- Every output is written beside its destination, its frames counted, and only an exact count replaces the act's render;
  a short render is kept aside for a look and reported as refused.
- Renders register in `renders/registry.json` (act, engine, file, frames, samples, SHA-256, when, the bundle's hash).
  The first registered act render locks the film's timeline: after that, re-timing needs the owner's decision.

## The commands

| Command | Does |
|---|---|
| `slate render` | every act, one at a time |
| `slate render --act 3` | one act |
| `slate render --act 3 --from 120 --to 260` | re-render that range (the act's own frames, inclusive) and splice it into the registered render, with proof |
| `slate render --from 900 --to 960` | the same with film frames; Slate finds the acts |
| `slate render --act 3 --out <path>` | a range or act to a file of your choosing, not registered (a review copy) |
| `slate render --resume` | pick up renders that didn't finish, keeping every good chunk |
| `slate render --fresh` | ignore kept chunks |
| `slate render --detach` | queue it and return a job id at once |
| `slate render --engine ae --act 2 --go` | an After Effects act, on the owner's go only |
| `slate render list [--verify]` | what's rendered, moved, missing or unfinished |
| `slate render splice <patch> --act 3 --from F --to T` | splice a patch you rendered yourself, with proof |
| `slate render stills --frames 120,240 --act 3` | review stills from one bundle |
| `slate render clean` | leaked bundles, caches, finished chunks, safety copies |

## Chunks and resuming

A render is split into chunks of lossless frames. A chunk with the right frame count, hash and settings is kept; a short
or missing one is rendered again. Chunks are joined into one encode with each join proven by a PSNR scan either side.
So a render interrupted by a dying agent, a sleeping Mac or a crash resumes where it stopped: `slate render --resume`.

## Patches and splices

When one range of an act changes (a fix after review), render only that range and splice it in:
- ProRes is intra-frame, so the splice is a stream copy: nothing else is re-encoded;
- the proof: the frame count, frame hashes at the frames either side of the patch, and PSNR at the edges;
- the previous render is kept aside until the new one is proven and registered.

On the first films, splice joins measured 54 to 93 dB, and untouched frames were bit-identical.

## Engines

| Engine | When | Notes |
|---|---|---|
| `remotion` (default) | acts built with the kit | pinned Remotion; one bundle per film per source hash, reused, then deleted |
| `web` | the open web renderer, where a film uses it | no Remotion licence needed; a local page rendered frame by frame |
| `ae` | acts built in After Effects | whole acts only, `--go` only, never autonomous, AE closed otherwise |

## The queue and the machine

- Any render longer than a small foreground limit goes through the job queue automatically, holding the render slot and
  its disk reservation; `--detach` sends any render there. Follow it with `slate job status <id>` and `slate job log
  <id>`.
- The queue waits while the machine is overloaded (the first films once drove the load to 293 on 14 cores).
- Renders reserve their disk first (ProRes is large: a 6-minute master is about 10 GB) and remove their bundles when they
  end, success or failure. Temporary bundles once filled 14 GB.
- **Renders run on this machine only.** Never on a borrowed machine or the owner's work computer.

## After a render

Run the gates that read the picture: `slate qa frames glitch boundary motion` (slate-qa). Triage every glitch flag;
review the contact sheet yourself.
