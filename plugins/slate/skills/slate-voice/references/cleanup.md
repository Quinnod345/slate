# Cleaning a voice

Most takes need little or nothing. When a voice carries noise, the cleanup must remove the noise and keep the voice:
the owner chooses between variants by ear.

## Diagnose first

```
slate voice clean --diagnose
```

- Measures each take's noise floor and where it lives in the spectrum.
- A designed voice can carry a baked-in artifact (a faint buzz between about 5 and 12 kHz) that's present even in
  lossless output: it's in the voice, not the codec, so a better format won't remove it.
- Request the best format the plan allows (lossless PCM needs a higher plan); Slate records any fallback.

## The options

| Method | What it does | When |
|---|---|---|
| The default chain (`slate voice clean`) | a denoiser that tracks the take's own floor, then a gentle gate | most takes with a steady floor |
| Voice Isolator (`--method isolator`) | the vendor's isolator | background noise that isn't in the voice; it lowered a baked-in buzz only about 2 dB |
| Voice Remix (`--remix --text "<line>"`) | re-generates the recording with the same voice | an artifact in the voice itself; prompt it to keep this exact voice and change only the recording, at a modest prompt strength (about 0.25 to 0.5), across a few seeds, lossless output |
| A custom chain (`--chain`) | your own filter chain | when the owner picked one |

## Letting the owner choose

Offer three to five labelled variants of the same line (level-matched, side by side) and let the owner pick by ear. On
the first films, the owner's pick was a floor-tracking denoiser with a gentle expander, chosen from five. Record the
chosen chain and use it for every take of that voice.

## Rules

- Clean every take of a voice the same way, so lines match.
- Never clean an approved take in a way that changes it without a new approval: the approval is bound to its bytes.
- Clean before pacing; pacing works on the clean take.
- Whisper still has to hear every word after cleaning.
