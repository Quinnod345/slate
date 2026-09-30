# Prompting generators well

Text-only prompts had the first films' generator "flying blind": it could only make atmosphere. Given full visual context
and explicit prompts on the top models, it matched keyframes.

## Give the model everything it's allowed to see

When the brand allows uploads (`grounding.uploads_allowed`):
- **a reference pack**: the brand's look (its kit's reference frames), UI frames and reference-film frames where they
  help set the look (never to be reproduced on screen);
- **start and end frames**: the film's own frames either side of the shot, so the generation joins the film;
- **reference-to-video** inputs for continuity: an earlier selected plate, or stills from it (plates.md);
- the shot's **duration**, **aspect** and **size** (Slate fills these from film.json).

When uploads aren't allowed, the prompt carries the brand in words (grounding.md), and continuity comes from the film's
own takes, which are the film's material, not the brand's.

## Write long, explicit prompts

- Describe the shot as a cinematographer would: subject, setting, time of day, light, lens and camera movement, depth,
  colour, texture, mood, pace.
- Spell out the brand's palette tokens and rules (the colours to grade toward, what never appears).
- Say what must **not** appear: no text, no UI, no logos, no extra elements, no invented guide lines.
- One shot per prompt. Don't ask a clip to do two things.
- Keep the wording of a successful prompt as the template for the next shot of the same kind.

## Templates per brand

Plate briefs are templates, and every brand keeps its own reference pack for generations. Once a plate prompt has
produced an approved take, record it (with its model, seed and references) in the film's records and reuse it as the
starting point for the brand's next plates.

## First and last frames

- Generate **between the film's own frames**: the model then works inside the film's geometry.
- Don't hand the model background-only frames and hope it keeps the film's elements: it will draw its own versions of
  them, off by tens of pixels.
- The film's code draws its own lines and type over a plate; the generation never draws them.

## Stills

Keyframes and stills (for a plate to animate, or a look test) follow the same rules; image models vary in how they take
a palette lock, a background colour or type (never use generated type as final type).
