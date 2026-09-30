# MOTION.md: how Demo Studio moves

*Generated from brand.json's `motion` by `slate brand onboard demo --step look`. Edit the law in brand.json; this page and motion.json are rewritten from it.*

Drawn for the demo pack as a sound, neutral law to start from. A real brand's law is measured from its own product and two or three reference films during onboarding.

## Named eases

| Name | Curve | Job | Lands (T90) | After Effects handles | Lineage |
|---|---|---|---|---|---|
| **arrive** | cubic-bezier(0.2, 0.9, 0.3, 1) | everything that enters: type, cards, UI, count-ups, draw-ons | 39.1% of the move | out 20% at 4.5·v; in 70% at 0·v | drawn for the demo pack: a firm, soft landing that settles early (not measured from any product) |
| **carry** | cubic-bezier(0.6, 0, 0.4, 1) | rest-to-rest moves of things already on screen, and the camera | 72.8% of the move | out 60% at 0·v; in 60% at 0·v | drawn for the demo pack: a symmetric in-out that starts and ends at rest |
| **switch** | cubic-bezier(0.7, 0, 0.2, 1) | state changes: a chip flips, a check lands, a toggle commits | 66.1% of the move | out 70% at 0·v; in 80% at 0·v | drawn for the demo pack: a held breath, then a quick commit |
| **depart** | cubic-bezier(0.5, 0, 0.9, 0.6) | anything leaving: accelerates away | 97.0% of the move | out 50% at 0·v; in 10% at 4·v | drawn for the demo pack: gathers speed and leaves without slamming |
| **make-room** | cubic-bezier(0.4, 0.1, 0.25, 0.95) | short re-layouts: a line pushes a block, a list re-sorts | 66.7% of the move | out 40% at 0.25·v; in 75% at 0.067·v | drawn for the demo pack: a small, even lift |
| **settle** | spring0 (stiffness 8) | one persistent container changing size | 48.2% of the move | — | the kit's zero-bounce spring, a little softer: a settle, never a bounce |
| **linear** | linear | only things that show time: progress, a playhead, a constant spin | 90.0% of the move | — | time is linear |

AE handles: speed is a multiple of the move's average speed v (distance ÷ duration); influence is the handle's reach as a share of the segment.

**Roles:** entrance → arrive, move → carry, state → switch, exit → depart, relayout → make-room.

## Duration scale

| Token | Seconds | Use |
|---|---|---|
| **tick** | 0.18 | chip flips, checks, a dot lighting |
| **quick** | 0.3 | small UI elements, re-layouts |
| **base** | 0.5 | standard entrances: a line of type, a card |
| **broad** | 0.75 | panels, wipes, short count-ups |
| **camera** | 1.1 | camera moves and carried pieces |
| **signature** | 1.6 | the logo build and the act's one signature moment |

Seconds are the source of truth; frames come from each film's own rate.

**Exits** run 0.75× the duration of their entrance.

## Staggers

- **char**: 0.04 s
- **word**: 0.08 s
- **line**: 0.12 s
- **block**: 0.18 s
- **Cap:** a group's whole spread is at most 0.5 s; at most 50% of the group's own entrance; more than 8 items are grouped, never staggered one by one.

## Overshoot

None: nothing bounces, and nothing passes its target.
Nothing bounces. A container that grows uses settle, which lands without passing its target.

## Signature motion

- **name**: keystone build
- **what**: the mark builds tile by tile, left to right, and its clay keystone drops in last and lands
- **logo**: mark
- **ease**: arrive
- **duration**: signature
- **sound**: one hollow wood knock as the keystone lands, with a small felt note under it

## Rules

- Linear only for things that show time: progress, playhead, spin.
- Loops: min_period_s 0.8.
- Never scale anything from zero: entrances start from at least 85% of their size.
- Never bounce or overshoot: the studio's motion is calm; settle lands without passing its target.
- Never cut hard between shots: pieces are carried from beat to beat (exception: a cut hidden inside a fast move's blur).
- Never crossfade between shots: it double-exposes type into mud.
- Never use an editor's default ease: every visible move uses one of the named eases.
