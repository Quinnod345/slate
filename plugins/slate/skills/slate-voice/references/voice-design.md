# Designing a voice

When no library voice fits (a brand character, a narrator with a very particular presence), design one from a brief
with Voice Design. The design model is always named explicitly in the request (Slate does this).

## The voice brief

Write it before any preview:
1. **Who they are**: in a few sentences, as a person.
2. **Values**, each with a source (the brand's own words, the product's copy).
3. **How they speak**: warmth, pace, breath at full stops and turns, pitch register, delivery per sentence type
   (statements, questions, the call to action), the opening line, the microphone and the room.
4. **What they never sound like.**
5. **A philosophy-to-sound table**: each value, and how it sounds.
6. **The model**, the prompts, the preview text.
7. **The measured comparison** of the candidates, the production-model check, a recommendation, and the credits spent.

If you can run sub-agents, one can turn the brand's philosophy into the brief while you prepare the tools; otherwise
write it yourself first. Either way, every value cites its source.

A voice brief that quotes company code or internal material is company material: it stays with the company's files and
never travels into a personal or public package.

## Prompts

Voice Design reads prompts in a set order: native language, gender and age range, quality, a two-to-five-word persona,
two or three emotion adjectives, then timbre, pacing and delivery.
- Say "thick" rather than "strong" for an accent; don't use "accent" to mean intonation.
- Avoid "reverb" and "echo": they design the room into the voice.
- Longer preview text gives more stable previews.
- Higher guidance adheres to the prompt more closely but can cost quality.

## Previews and the production-model check

```
slate voice design --description "<the prompt>" --text "<the preview text>"
```

- Previews are measured and ranked on proxies: a pace of about 150 to 170 words a minute, sentence pauses of about 0.35
  to 0.7 s, a pitch range of about 9.5 to 13 semitones, statement endings that fall (at least a semitone, never rising
  much), a clean floor, and every word heard (brand words included).
- **Keep a candidate, then check it on the production model** (`--save`, then `--check`). Designed voices can perform
  differently there: higher in pitch and faster than their previews. Render the candidates on the film's own model
  before choosing.
- The owner picks by ear.

## Designed voices and direction

Designed voices can be less performative on the newest model than on older ones, and tags land more readily when the
delivery is already in the voice. Keep a designed voice's tags inside its designed character; never ask it for a
performance it wasn't designed for (laughing, big emotion). See v4-tags.md.

## Cleanup

A designed voice can carry a baked-in artifact (a faint buzz high in the spectrum, present even in lossless output),
because it's in the voice, not the codec. Diagnose it, then offer cleanup variants (cleanup.md).
