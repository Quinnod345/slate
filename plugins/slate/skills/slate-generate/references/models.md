# Models

Slate doesn't work without the best models, so the best one for each role is the default: recorded in `registry.json`
with the date it was verified, pinned per film at first use, and overridable per film (film.json `models`), per user
(`slate.toml`) or per shot (the generate block's `model`).

## Roles

The role follows from what a shot's generate block gives it:

| Role | When | Typical inputs |
|---|---|---|
| a still | no image, video or duration | a prompt |
| animate a plate | an `image` (and optionally an `end_image`), or a `duration` | a start frame, an end frame |
| style-locked from references | `reference_images` | several stills that set the look |
| extend a plate | a `video` | an earlier selected plate |

`slate plates plan` shows each shot's role, model and estimate before anything is spent.

## Routing

- **Keep a routing table and a plate review sheet.** Models differ in brief fidelity: on the first films one family came
  out painterly and off-palette, another broke the brief with an extra element, and a third matched the keyframes.
- **Resolution and length caps differ**: some video models stop at 720p; use a higher-resolution model for hero plates.
- **Availability varies**: a model can be temporarily unavailable. Have a fallback per shot.
- **Native frame rates differ** (plates often arrive at 24 fps); the conform handles it (plates.md).
- **Re-check the catalogue** at the start of a project and before assuming a gap: the catalogue changes monthly, and
  model knowledge in any agent is older than the catalogue.

## What the Cloud API can't do

- **Voice and music**: no voice or music models. Voice and music come from ElevenLabs (slate-voice, slate-sound).
- **Product-true UI or exact brand type**: generated UIs aren't the product; UI and type stay in code.
- Some app-only models and workflows (a cloud video editor, narrated explainer workflows) exist only on the app-credit
  side, which isn't funded (spend.md).

## Pinning

`slate gen run` pins a shot's model at first use. A registry refresh never changes a pinned film; re-pinning is
explicit, tested and approved, so a re-generation in week three matches week one.
