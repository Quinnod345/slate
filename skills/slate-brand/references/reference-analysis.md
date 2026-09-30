# Analysing reference films

Reference films teach a brand's motion before anyone animates anything: what good looks like for this brand, measured
rather than guessed.

## Running it

```
slate analyze <reference-film>
```

- extracts the audio and splits voice from music (Demucs) when they're mixed;
- transcribes the voice with word timestamps;
- breaks the film into scenes and grabs a frame at each change and a settled frame;
- reads on-screen text by OCR;
- measures eases: clean moves fitted to cubic-bezier curves, frame by frame, with their fit error and the nearest
  standard curve.

## Method

- **Record a method table** (each step, what ran, what came out) so every result can be re-derived.
- **Study the best moments at full frame rate** (or dense samples), as moment sheets: what happens, when, and what to take
  and leave for this brand.
- **Fit only clean moves.** A move blurred, clipped at the frame's edge or contaminated by other motion is read
  qualitatively ("a long deceleration"), not fitted.
- **Take the voice's text from the transcript**, with a second model agreeing on disputed lines. A generation vendor's
  scene analysis is fine for segmentation and visual notes, but its per-scene audio field once paraphrased the on-screen
  text instead of the voice.

## What a review of a reference records

- **What to keep and what to fix.** Common faults in references: deck-like pacing, on-screen text repeating the voice,
  hard cuts, tiny body copy, a master far under its loudness target (one was 16 LU low), and no call to action.
- **Claims that conflict** with the brand's current website or product, in a table, for the claim authority.
- **The measured curves**, and what they prove: good references use hand-tuned curves, nowhere near a template's
  default ease.

## Feeding the motion law

The fitted curves, the product's own motion tokens and the brand's taste become the brand's named eases, each with its
lineage (which reference move or product token it came from). Keep the fits in a measured-eases file with their errors,
so the law's numbers can always be traced back to what was measured.
