# The generation policy

The owner's rule: generation is rare by default, and the person asking decides how much. Code builds anything designed;
generation is for real people, real scenes and stock-style shots code can't make, or where it genuinely adds something.
Every take is judged, and ugly or AI-looking takes never ship. The best models by default, configurable.

## The film's setting (the requester's choice)

| Setting | What may generate |
|---|---|
| `none` | nothing |
| `rarely` (default) | only what code can't make: `real-people`, `real-scene`, `stock` |
| `where-it-helps` | also `adds-something` and `bridge-matte`, each with a written `why` of at least a sentence |

Set it at intake (`slate new --generation`) from what the requester says; ask when a brief implies generated footage
("a shot of a crowded street at night") and the setting doesn't allow it.

## What's never generated

Anything designed: UI, app screens, text and type, copy, logos and wordmarks, charts and graphs, data, numbers,
diagrams, icons, brand graphics. A generate block whose `contains` lists any of these is refused: code draws it. A
generated plate is atmosphere only, with only the film's line and type over it.

## Where generation has earned its place

- **Atmosphere plates**: a landscape, weather, a city at night, where the film's world opens up.
- **Real people and real places** the film needs and can't capture or draw (with the requester's sign-off for people).
- **Stock-style shots** a film would otherwise license.
- **A bridge used as a matte** where the code's world meets real footage (mattes.md).
- **Native ambience**: some video models return usable ambience with a plate (wind and air, no music), at a small extra
  cost.

## Where it failed

- Generated pixels on screen where code or real footage existed read as AI: a generated line tracing a ridge "keeps
  floating" and was rejected; only the generation's shape survived, as a matte.
- Stylized generated explainers aren't brand work: they're stylized clips, not the brand's type or real UI.
- Background-only first and last frames made a model draw its own guide line 60 to 100 px off the film's line.

## Autonomous films

Autonomous films generate under the same rules, and every generation waits for the owner's yes:
1. `slate gen request-approval --shot <S>` writes the request (never edited afterwards) and asks the owner by iMessage and
   push (never in tests or replay).
2. The owner's yes is recorded as a spend approval with an id.
3. `slate gen run --shot <S> --approval <id>` spends; without the approval the ledger refuses (exit 4).

## Brand material and uploads

- Brand, UI and video material goes to a generator only when the brand's owner allows it: the pack's
  `grounding.uploads_allowed`. Text-only prompts are always fine.
- A film's own generator outputs (its takes) can be sent back to the generator (for continuity); that isn't brand
  material.
- Company source code never goes anywhere; credentials are never uploaded, printed or written to a file.
- Only generic design queries go to third-party inspiration services.

## The lane

Every Slate generation runs on the Higgsfield Cloud API: the one funded lane, callable by any agent with a key, pay per
generation. When the API has a real gap (it has no voice or music models), the next direct vendor fills it (ElevenLabs
for voice and music), never an unfunded lane. Re-check the API catalogue before assuming a gap still exists.

Other routes bill other pools, and Slate doesn't use them:
- **Higgsfield's CLI** (and the skills built on it) bills the app-credit pool, separate from the Cloud API; it estimates
  with its own cost command and signs in by browser OAuth, which only a person runs, in their own Terminal.
- **Higgsfield's hosted connector** serves After Effects recipes as free text, but generating inside them spends the
  app-credit pool, not the Cloud API balance.
- **ElevenLabs' connector** also exposes third-party image and video models and lipsync; generations there bill
  ElevenLabs credits and bypass Slate's ledger.

Spend made in a chat through a connector is caught by the session's balance snapshots, never by the ledger.
