# Demo Studio (the neutral demo pack)

An invented studio that makes short, calm films from data. It isn't a real company: it exists so `slate demo`, Slate's
tests and anyone trying Slate have a complete, neutral brand to work with, without touching anyone's real brand.

| Part | What's here |
|---|---|
| Palette | Warm stone neutrals (`neutral.50`–`neutral.900`), white cards (`chalk`), one clay accent (`clay.100/500/700`), a deep harbour slate for inverse surfaces. Every text pair in `contrast_pairs` meets its bar. |
| Type | Public Sans (SIL Open Font License 1.1) from Google Fonts, at 400 and 700, named by reference. |
| Logo | `logos/mark.svg`: three tiles and a round clay keystone, drawn for this pack. Its build lays the tiles left to right and drops the keystone in last. `mark-inverse.svg` is the same mark for dark surfaces. |
| Motion | A neutral law drawn for this pack (`motion.json`, explained in `MOTION.md`): arrive, carry, switch, depart, make-room, settle and linear, a six-step duration scale, capped staggers, no overshoot, the keystone build as the signature motion. |
| Voice | A public ElevenLabs library voice, so the demo can be voiced for real with `--live`. |
| Sound | Proof 2's taste as a starting point: soft pads, small felt notes, hollow wood clicks and knocks, no chimes. |
| Truth | Two invented claims, a banned-word list, a never-claim list and labelled sample data, all sourced to this pack. |
| Skills | `skills/demo-studio-taste/`: an example of a brand's own taste skill living inside its pack. |

Its four references are **placeholders, unapproved by design**, so `slate brand status demo` says "needs
onboarding" and autonomous films refuse it. Supervised demo films don't need the references.

The pack lives in Slate's repo, so commands never change it. To try onboarding, make your own copy in the private
store:

```
slate brand init mystudio --from-pack demo
slate brand onboard mystudio
```
