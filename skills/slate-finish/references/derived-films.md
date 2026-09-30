# Derived films: cut-downs, aspects and languages

One film often needs to exist in several shapes: a 6-second cut, a 9:16 reel from a 16:9 film, a square cut for a feed,
a version in another language. Each is a **derived film**: its own folder, with a parent.

## Making one

```
slate new launch-reel --brand <brand> --format <format> --parent launch-film --derivation '{"aspect": "9:16"}'
slate new launch-15 --brand <brand> --format <format> --parent launch-film --derivation '{"cut_s": 15}'
slate new launch-fr --brand <brand> --format <format> --parent launch-film --derivation '{"language": "fr"}'
```

## What it reuses, and what it owes

- **Reuses by reference** (nothing is copied): the parent's approved takes, timings, beats, plates and score takes. It
  keeps the parent's clock.
- **Re-stages its layout**: its own type scale and safe zones for its aspect and platforms. A new aspect is never a crop:
  titles, faces and UI are re-placed inside the new frame's safe areas, and the camera's framing is rethought where it
  must be.
- **Re-plans its sound**: a cut-down needs its own score edit and joins; a new aspect keeps the mix; a new language needs
  new voice takes.
- **Runs every gate** again: no gate carries over from the parent.
- **Needs its own approval**, at its own final cut.

## Channels

| Channel | Route |
|---|---|
| Hero placements: website, App Store, a video platform's master | derived at master quality, through the whole pipeline |
| Social cut-downs: reels, shorts, feeds | can take a faster route, once one is proven faster at acceptable quality |

## A faster route for social cut-downs (not adopted yet)

A cloud timeline editor can make fast, timing-exact variants (cut-downs, re-timed or re-voiced versions, animatics)
driven by the same events and word timings. It's to be benchmarked against a kit-built cut-down of the same film and
kept only if it's genuinely faster at acceptable quality. What's known about it:
- it suits variants, not the flagship: no 3D, limited motion blur, no visual editing;
- it refuses tracks with a single key, zero-width masks, and shader code that paints outside its box;
- motion blur on frames whose children have shadows makes hard, fringed halos: skip blur there;
- it rasterizes a node's mask at canvas size from the node's own origin, and mask width grows from the mask's centre;
- its 16-sample blur cap shows steps on whips: ramp the blur instead;
- its upload slots expire in under an hour: upload right before use;
- sandbox runs cost no credits and rendered about 67 ms a frame at 1080p, but a 100-second render took 94 to 577 seconds
  end to end;
- its workflow docs sit behind the vendor's connector: record the version used with every variant;
- per-character type animated outside a browser needs real glyph shaping to keep kerning.

## Checking a derived film

Everything in slate-qa, plus: the safe zones for its platforms on the contact sheet, captions re-flowed for its width,
and a side-by-side against the parent at the moments that matter (the signature moments, the call to action).
