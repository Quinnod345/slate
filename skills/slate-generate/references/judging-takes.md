# Judging takes

Every generated shot is judged from at least two takes, by a person, at full size and in motion. "Sometimes the AI
videos are bad, ugly, and they don't help": an ugly or AI-looking take never ships, however much it cost.

## The review

```
slate gen judge --shot S05
```

builds a contact sheet of every take side by side and measures each one: black or flat frames, freezes, flicker, size
and length against the request. It records a REVIEW gate: nothing passes until a person picks.

Look at each take:
- **in motion**, at full speed and slower: interpolation artifacts, morphing, swimming textures, flicker;
- **at full size**: detail, edges, faces, hands, text-like shapes the model invented;
- **against the brand's approved look**, side by side: palette, light, texture, mood;
- **in its place**: with the shots either side, and the film's line and type over it where they'll be.

## Reject a take when

- it reads as AI: a floating or drifting line, a synthetic texture, morphing forms, uncanny people;
- it drifts off-look: painterly when the film isn't, off-hue, the wrong sky;
- it invents its own elements: a guide line drawn beside the film's line, mirrored forms, an extra branch;
- it breaks the brief: an element the prompt didn't ask for, the wrong subject, the wrong move;
- it fails a measurement: black or frozen frames, flicker, the wrong length or size.

## Picking

```
slate gen pick --shot S05 --take t2 --by <who chose> --reject t1 --why "painterly, off-palette"
```

The pick records the approved take's SHA-256 (the conform refuses a take that no longer matches) and the rejections with
their reasons. If every take is rejected, the gate fails: generate again with a better prompt or references (and a new
estimate), or give the shot to code.

## After picking

- Conform the approved take (plates.md) and check every shot that uses it for interpolation artifacts.
- Grade it toward the brand's palette tokens.
- Check the joins numerically where a plate meets code-built frames (the tone of the ground per frame, the steps across
  the boundary).
- Keep every take and its record: the rejected ones are evidence of what the model does.
