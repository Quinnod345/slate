# Formats: what "any video" means in practice

A format is a template in Slate's `formats/` folder. It sets the runtime, aspect and size, frame rate, pace, structure,
loudness target, caption defaults, per-platform safe zones and the default budget. `slate new --format <name>` copies
its values into film.json, which is then the one source of the film's clock and size. Nothing downstream assumes a
frame size or rate; everything reads film.json.

| Format | Runtime | Aspect | Notes |
|---|---|---|---|
| Brand film | 2 to 6 minutes | 16:9 | The long form: several acts, a composed score, the AE finish when supervised. |
| Explainer | 60 to 120 seconds | 16:9 (or 9:16) | One idea, told clearly; 0 to 2 generated shots at most. |
| Product demo | 30 to 120 seconds | per destination | Real app captures through `slate capture ios` or `slate capture web`. Capture for truth and speed; rebuild in code when a shot needs camera moves, restaging, motion blur or exact type. |
| Social reel | 15 to 45 seconds | 9:16 | Captions burned in by the renderer in the brand's font. Instagram, TikTok, YouTube Shorts. The autonomous default. |
| Ad | 6, 15 or 30 seconds | 16:9, 1:1 or 9:16 | Every second counts: one idea, one call to action. |
| LinkedIn cut | 15 to 60 seconds | 1:1 and 4:5 | Usually derived from a larger film. |
| Website hero loop | per page | per page | Loops seamlessly; no voice; to the platform's spec. |
| App Store preview | to the store's spec | device | The store's size, length and safe areas. |
| Launch teaser or feature announcement | 10 to 45 seconds | any | A single reveal, often a signature moment and a sting. |
| Kinetic type or logo sting | 3 to 15 seconds | any | Type-led; the editorial look or the logo build. |
| Character-led | any | any | The brand's motif or mark as a character (slate-motion, character-and-motif-motion). |

The format files are the source of truth for exact values; this table is the overview. `slate formats list` names them
all, and `slate formats show <name> [--aspect 9:16]` gives one in full: its runtime and clock, the read time and the
planning rate the script gates use, its caption rules at that aspect, its channels and their safe zones, its budget,
and the platform files `slate deliver` makes for it (each platform's size, clock and limits).

## Derived films: cut-downs, aspects and languages

A cut-down, a new aspect or a new language is a **derived film** with a parent. It reuses the parent's approved takes,
timings, beats and assets by reference (nothing is copied), keeps the parent's clock, re-stages its layout with its own
type scale and safe zones, re-plans its sound, runs every gate, and needs its own approval:

```
slate variant <parent-id> <child-id> --aspect 9:16
```

Other derivations: `--cut 15` for a cut-down, `--language fr` for a new language (v1 speaks English; the code carries no
English literals, so languages arrive as data). `slate new` with `--parent` and `--derivation` makes the same film from
its own command line. See slate-finish, derived-films.md.

## Variants by channel

- **Hero placements** (website, App Store, YouTube masters) are derived films at master quality, through the whole
  pipeline.
- **Social cut-downs** (Instagram, TikTok, Shorts, LinkedIn) can take a faster route. A faster engine is kept only if a
  benchmark on the same film shows it's genuinely faster at acceptable quality.

## Choosing when the requester didn't say

- A length and an aspect usually settle it; a destination ("for our App Store page") settles the rest.
- If only the destination is known, ask the one missing thing (length, or who it's for).
- An autonomous run with no format gets the 15 to 45 second 9:16 reel.
- Broadcast destinations (film, TV, radio) flag the composed score: Eleven Music's terms need an Enterprise plan there
  (licences.md).
