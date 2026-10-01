---
name: slate-generate
description: Decide whether a Slate film should use generated footage or images, and make it well when it should. Covers the policy (rarely by default, as much as the requester wants, code first for anything designed), generating real people, real scenes and stock-style shots where code can't go, the generate block in the script, estimates, takes and judging them (at least two, ugly or AI-looking takes never ship), the best models, grounding in the brand, conforming plates, mattes that use a generation only for its shape, and spend on the Higgsfield Cloud API. Use when a Slate film might need a generated shot, plate, bridge or still, when takes need judging, or when someone asks what generation will cost.
---

# slate-generate

Generated shots can lift a film or cheapen it. On the first films, generated pixels where code or real footage belonged
read as AI and were rejected; generated atmosphere, used carefully, earned its place. So Slate generates rarely by
default, only what code can't make, judges every take, and never ships an ugly one.

This is stage 8 of the production. Every generation runs on the Higgsfield **Cloud API** (the one funded lane), through
Slate's ledger, estimated first.

## 1. When to generate

- **The requester decides how much**: each film has a generation setting, `none`, `rarely` (the default) or
  `where-it-helps`.
- **Code first for anything designed**: UI, type, data, charts, logos and brand graphics are drawn by code, always. A
  generated plate never carries UI, text, charts or logos.
- **Generation is for what code can't make**: real-looking people, a real place or scene, a stock-style shot. On
  `where-it-helps` it can also be used where it genuinely adds something, or as a matte (its shape only), each with a
  written reason.
- **Drafts use no plates.** Autonomous films may generate under the same rules, but every generation waits for the
  owner's yes.
- **Generated people on screen need the requester's sign-off**, and a generated shot never implies a claim or a
  testimonial.

See [policy](references/policy.md).

## 2. Ask for it in the script

A shot that may be generated carries a `generate` block in `script/script.json`:

```json
"generate": {"reason": "real-scene", "why": "a real coastline at dawn, which code can't draw",
             "prompt": "…", "duration": 5, "image": "refs/start.png", "takes": 2}
```

`reason` is one of `real-people`, `real-scene`, `stock`, `adds-something`, `bridge-matte`; `contains` must list nothing
designed. File inputs are film-relative paths, `take:<shot>` (another shot's approved take) or `brand:<path>` (a file in
the brand pack, sent only when the brand allows uploads).

## 3. Plan, estimate, generate, judge, pick, conform

```
slate plates plan                          # every generate block: allowed or not and why, the model, takes, the estimate
slate gen estimate --shot S05              # the free estimate for this shot's takes
slate gen run --shot S05                   # at least two takes, each submitted once, ledger row first
slate gen judge --shot S05                 # a contact sheet and numbers for each take, and a REVIEW gate
slate gen pick --shot S05 --take t2 --by <the person who chose>
slate plates conform --shot S05            # the approved take, at the film's frame rate and size
```

- A rerun polls takes still running; it never pays twice. `--detach` runs a batch as a queued job.
- **Only a person picks.** The pick records the take's SHA-256 and locks it; the conform refuses a take that no longer
  matches the pick.
- Autonomous films ask first: `slate gen request-approval --shot S05`, then run with the approval id once the owner's yes
  is recorded.

## 4. Judge every take

At least two takes per shot, judged side by side at full size and in motion, against the brand's references. **Ugly or
AI-looking takes never ship, however much they cost.** Reject takes that drift off-look (painterly, off-hue, the wrong
sky), invent their own elements (a guide line drawn beside the film's line), or break the brief (an extra element).
Inspect interpolation on every conformed plate. See [judging takes](references/judging-takes.md).

## 5. Make it good

- **The best models by default** (from `registry.json`, pinned per film at first use), overridable per film or shot.
  See [models](references/models.md).
- **Ground every generation in the brand**: its references wired in, its tokens and rules spelled into the prompt. See
  [grounding](references/grounding.md).
- **Full visual context and long, explicit prompts** when the brand allows uploads: start and end frames, reference
  frames, the look. See [prompting](references/prompting.md).
- **Continuity across plates**: generate later plates from the film's own frames or an earlier selected plate. See
  [plates](references/plates.md).

## 6. Mattes: a generation's shape, never its pixels

Where the film's code-built world meets real footage, a generated transition can help, but its pixels on screen read
as AI. Use it as a **matte**: the generation's shape reveals real frames inside and the film's own ground outside, and
the code draws its lines and words on top. `slate plates matte --shot S05 --base <image> --real <shot>` builds it. See
[mattes](references/mattes.md).

## 7. Spend

Every call is estimated first (the estimate's `usd` is the price; `discount.usd` is only the saving), checked against
the film's budget and the month's cap, recorded in the ledger with its request id before waiting, and sent with an
Idempotency-Key, so a lost answer is resent with the same key and body and charged once. Video plates are 720p unless
the owner chooses 1080p (2.46x the price). Slate never buys credits. Higgsfield's own skills (the fast lane)
bill app credits, a separate pool that isn't funded by default. See [spend](references/spend.md).

## References

- [policy](references/policy.md): the generation setting, reasons, what's never generated, autonomous approvals.
- [prompting](references/prompting.md): full references, explicit prompts, templates per brand.
- [grounding](references/grounding.md): wiring the brand kit into every generation.
- [models](references/models.md): choosing and pinning models, routing, what the API can't do.
- [judging-takes](references/judging-takes.md): two takes or more, the review sheet, what gets rejected.
- [plates](references/plates.md): atmosphere plates, continuity, conforming, type over footage.
- [mattes](references/mattes.md): bridges as mattes over real frames.
- [spend](references/spend.md): estimates, the ledger, two wallets, costs from the first films.
