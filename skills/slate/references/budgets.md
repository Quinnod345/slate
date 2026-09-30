# Budgets and spend

Spend is explicit. Every paid call goes through Slate's ledger: an estimate, a budget check, an in-flight row with its
request id written before waiting, then the result. A restart polls the row instead of paying twice.

## The rules

- **Estimate first.** Generations are priced with the vendor's free estimate before anything is submitted; voice and
  music are priced from the registry's per-character and per-second rates.
- **A film's budget is a hard stop.** 1.5 times is allowed only for rework that's marked as rework. Going past it needs
  the owner's recorded yes (`slate budget film --film <id> --raise <n> --approved-by <name>`), which an autonomous film
  can never give itself.
- **Monthly caps per mode** live in Slate's own budget file and are changed only by a person in their own Terminal.
  `slate budget show` has the month and every film's budget.
- **Never buy.** Slate never buys credits, upgrades a plan, tops up a balance or starts a licence. It can say when a
  plan would pay for itself (smallest plan, one month first) and let the person decide. When a balance runs out, the
  work stops and asks.
- **Replay by default in tests and demos** (`SLATE_FAKE_VENDORS=1`); `--live` spends for real.

## Default budgets by format

Hard stops, from what the first three films actually spent. Prices live in `registry.json` with verified-on dates and
move often, so re-check them before a big spend.

| Format | ElevenLabs credits | Higgsfield (Cloud API) | Notes |
|---|---|---|---|
| Reel or ad (6 to 45 s) | voice 500 to 3,000; score 1,000 to 2,000 | $0 by default | Generation only when it's allowed and adds something. |
| Explainer (60 to 120 s) | voice 1,000 to 5,000; score 2,500 to 5,000 | $0 to 60 | 0 to 2 generated shots at about $15 to 30 each, including one reject. |
| Brand film (2 to 6 min) | voice 4,000 to 12,000 (tag A/Bs included); score 9,000 to 28,000 | $40 to 85 | |
| Brand onboarding (once) | Voice Design about 5,000 | $15 to 20 for look tests | The sound reference is made locally. |

For scale: a code-only film spends nothing on generation; a hybrid film with about 40 seconds of generated plates, a
few takes each, spent roughly $8 to 30; an all-generated six-minute cut would be roughly $55 to 95.

## Spend Slate can't see

Generations made in a chat through the hosted connectors (Higgsfield's or ElevenLabs') bypass the ledger. The ledger
catches them by comparing balance snapshots:

```
slate ledger snapshot --all --session <label>        # at the start of a working session
slate ledger snapshot --all --session <label>        # at the end, with the same label
slate ledger reconcile --session <label>
```

`--all` records every balance Slate can read by itself (ElevenLabs'); it skips Higgsfield, which has no balance
endpoint. When a Higgsfield connector or Higgsfield's own skills ran in the session, record its balance at both ends
from the connector's balance tool or the dashboard:

```
slate ledger snapshot --provider higgsfield --balance <n> --session <label>
```

A reconcile that finds unexplained spend is REVIEW, for the owner to look at. A provider with only one snapshot in the
session is UNCHECKED, never a pass.

## Two wallets at Higgsfield

The **Cloud API** balance pays for every Slate generation and is the one funded lane. **App credits** pay for
Higgsfield's CLI, app and hosted connector, including its own skills; that pool isn't funded by default, so the fast
lane may stop for credits. Neither is ever topped up by an agent.

## Watching spend during a film

- `slate ledger list --film <id>` has every paid call for the film; `slate ledger inflight` shows calls still waiting.
- Before a batch (a full voicing, a score, several takes), say what it will cost and what's left of the budget.
- Before spending past an approval (a recomposition, a re-voice of approved lines), try what's on hand first: an unused
  take often measures better than a new one, and costs nothing.
