# The brand pack template

Every key a brand pack can hold, with empty values. `slate brand init <name>` copies this into the private brand store
(`~/code/slate-brands/<name>`, or wherever `SLATE_BRANDS` points), never into Slate's repo, and onboarding fills it in.
The schema with every type is `src/slate/schemas/brand.schema.json`; `slate brand validate <name>` checks a pack
against it and against the rules below.

A pack is a folder:

```
<name>/
  brand.json          the brand as data: the one source of truth every engine reads
  logos/              the brand's SVGs (the engines read them from brand.json's logos)
  references/         the four approved references, once a person approves them
  onboarding/         each onboarding step's checklist and outputs (truth/, look/, voice/, sound/)
  motion.json         the motion law on its own, written from brand.json by the look step
  MOTION.md           the motion law explained, written from brand.json by the look step
  skills/             the brand's own agent skills, if it has any (brand taste lives here)
```

Never put font files in a pack: fonts are named by reference, and `slate brand validate` fails a pack that holds one.

## The keys of brand.json

| Key | What it holds |
|---|---|
| `schema` | The pack format's version: `1`. |
| `name` | The pack's folder name, which is how films name the brand (`slate new --brand <name>`). |
| `display_name` | The brand as it's written and said. |
| `about` | What the brand is, in a sentence or two. |
| `note` | Anything else a person should know about the pack. |
| `ownership` | `kind`: `own` (a brand the owner owns, who may rule on its claims), `client` (a brand the owner doesn't own: its claim authority is a named person at that `organisation`), `neutral` (Slate's own). `company_material: true` keeps the pack on this Mac only, never in a repository with a remote, and means its source code is read in place, never copied. |
| `palette` | Colour tokens, flat or nested (`"neutral": {"50": "#…"}`), as `#RRGGBB`. `roles` names the kit's roles by token or colour: `page`, `surface`, `ink`, `ink2`, `ink3`, `hairline`, `accent`, `accentInk`, `softAccent`, `inverse`, `inverseDeep`, `line`. `tones` gives `ok`, `warn` and `risk` as `{dot}` or `{bg, ink, dot}`. |
| `contrast_pairs` | Every text colour pair the brand uses: `{fg, bg, role}` by token, role or colour. Body text needs 4.5:1 and large text 3:1; the accessibility gate measures exactly these. |
| `type` | `families`: `[{role, family, weights, source, licence, postscript}]`. `source` is by reference only: `installed` (a font on the machine, used in place), `google` (Google Fonts), or `{"kind": "kit", "url": "https://…"}` (a type service's stylesheet, never a font file). `licence` is required for every family. `postscript` is the face's PostScript name, which After Effects sets fonts by. `scale` optionally overrides the kit's text roles; `reference` is the short side its px sizes were written for. |
| `logos` | `{name: {file, build, on, clear_space, marks, colourways}}`. `file` is an SVG in the pack (shapes, not text: outline type first). `build` is the logo build: `guides`, `vertices`, `order`, `keystone` (the shape id that lands last), `keystone_from`, `outline`, `impact`, `timing`. The first logo is the default. Every mark comes in each colourway a film needs, ink and white at least: a sibling logo drawn for the inverse ground (`"mark-inverse": {"file": …, "on": "inverse"}`), or `colourways` naming them (`{"ink": "mark", "white": "mark-inverse"}`). The kit draws the one for its ground, so a builder never recolours a mark, and `slate brand validate` asks for the missing one. |
| `motif` | Optional: a brand mark made of parts, which the kit's motif rig moves as one system (`parts`, `shape`, `corner`, `part_size`, `ring`, `groups`, `mark_ratio`, `orient`, `curves`, `gestures`). |
| `look` | `id` (from the looks library, or the brand's own), `engine` (`remotion`, `web` or `ae`), `references` (the films and frames the look draws on), `moodboards` (each direction, its files and the brand kit it was grounded in), `overrides`. |
| `motion` | The motion law. `eases`: named eases, each a curve with its `lineage` (where it comes from) and `job`. `durations`: the duration scale in seconds. `staggers` and `stagger_cap`. `exit_factor`. `roles`: which ease does each job (`entrance`, `move`, `state`, `exit`, `relayout`). `overshoot`: the policy (`allowed`, `max`, `where`). `signature`: the brand motif's signature motion. `linear_props`, `loops`, `banned`: the law's other rules. `extra_curves`: the generic curves (`sway`, `land`, `spring0`, `even`) any move may use besides the brand's eases (by default only rig parts may use the first three and carries `even`). `speed_limits`: `{rig\|ui\|carry: {speed, accel}}`, the brand's own limits per motion class for `slate qa motion`, as fractions of the short side or as px with the `short_side` they were measured on (Slate's defaults: rig 30 and 8, ui 60 and 16, carry 170 and 45 px per frame and per frame squared at 1080). A `null` section lets the kit's neutral law stand in until onboarding writes one. |
| `voice` | The voice profile (the voice module's schema, `voice_profile.schema.json`): `provider`, `source` (`library`, `designed`, the owner's own verified `clone`, or real `recording`s), `voice_id`, `voice_name`, `model` (`null` means the registry's current best, pinned per film), `settings`, `seed`, `lexicon` (brand words Whisper must hear in full). |
| `sound` | The brand's own sound palette, starting from proof 2's taste: `key`, `tempo` (bpm or `[lo, hi]`), `materials` (what the effects are made of; the score never plays them), `banned_timbres`, `instrumentation`, `motif` (`{instrument, …}`: the character's own sound), `palette` (overrides of the starting palette, key by key), `reference_levels` (measured against the voice on the sound reference). |
| `truth` | `sources` (each `{id, kind, where, pin, read}`: pinned, and company code read in place), `claim_authority` (the named person who rules on claims), `approved_claims` (`{id, text, source}`), `banned_words` (one list, used everywhere: a word, or `{word, forms, instead}` with its inflections and the approved substitute a failure names), `self_commentary` (`false` when the brand's graphics never caption themselves, "Illustration" or "Example": the script and banned-words gates then ban those captions on screen), `never_claim` (what films never say or show, with why), `sample_data` (the one set of sample numbers, always labelled), `rulings` (conflicts and how they were decided). |
| `grounding` | Brand-kit ids that ground generations: `elevenlabs` and `higgsfield`, each `{brand_kit_id, name}`. |
| `allow_generator_uploads` | May brand material (logos, UI frames, references) go to generators? Only when the brand owner allows it; `false` by default. |
| `references` | The four onboarding references, `truth`, `look_motion`, `voice` and `sound`, each `null` until a person approves it. An approval records `{file, sha256, approved_by, approved_at, covers, covers_sha256}`. |

## Onboarding, once per brand

```
slate brand init <name> [--from-app PATH] [--from-website URL] [--from-pdf FILE]
slate brand onboard <name>                       the four steps and where each stands
slate brand onboard <name> --step truth          sources, the claim authority, approved claims
slate brand onboard <name> --step look           the brand's own components and motion values, reference films,
                                                 moodboards, motion.json and MOTION.md, the kit review sheet
slate brand onboard <name> --step voice          an approved take with its model, settings and seed
slate brand onboard <name> --step sound          a 10–20 second reference proof, its levels measured against the voice
slate brand approve <name> <reference> --file F --by NAME
slate brand status <name>                        "needs onboarding" until all four references are approved
```

A reference stays approved only while its file and the part of brand.json it covers are unchanged. Change either and
it needs a person again; autonomous films refuse a brand until all four hold.
