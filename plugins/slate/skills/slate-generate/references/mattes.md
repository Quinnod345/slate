# Mattes: using a generation's shape, not its pixels

## The lesson

The requester suggested using generation "to bridge gaps between clips". In a code-built film the acts join on shared
contract frames, so there are no literal gaps; the one place a generated bridge adds something code can't is where the
code's world meets photographic footage (a paper world opening into a real landscape).

The first version played the generated bridge on screen, under the code's words. The owner liked the paper bleeding into
the landscape, and not what followed: "you can obviously tell it's AI-generated. That line that's tracing the mountain
keeps floating." A version generated between background-only frames did worse: it drew its own guide line 60 to 100 px
off the film's line, and mirrored forms.

**What worked:** only the generation's **shape** survived. It became a matte:
- the generated clip becomes a monotonic, hole-filled matte (its difference from its first frame), spread with a soft,
  organic front;
- inside the matte, the **real** plate (an approved take, or real captured frames);
- outside it, the film's own ground;
- the generation's own texture shows only in the thin edge band, where it reads as ink or water;
- the code draws the line, the dot and the words on top, exactly where they belong.

## Building one

```
slate plates matte --shot S05 --base <the film's ground frame> --real <shot, capture:<name> or a folder of frames>
```

The generate block for the bridge says `"reason": "bridge-matte"` with a `why`, which needs the film's generation setting
to be `where-it-helps`. `--preview` writes a quick look before the full sequence.

## Rules

- **Never put generated motion on screen where code or real footage exists.** Mine generations for shape, timing or
  texture, and composite real pixels through them.
- **Generate between the film's own frames**, never between background-only frames.
- **Judge at least two takes** of the bridge, as with any generation.
- **Check every cut numerically**: the ground's tone per frame across the reveal, and the steps at the hand-over to the
  plate, which should match the plate's own motion.
- The film's code still draws everything designed.
