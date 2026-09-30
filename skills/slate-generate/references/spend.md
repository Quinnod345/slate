# Spend on generation

## How a generation is paid for

1. **Estimate first**: `slate gen estimate --shot <S>` asks the API's free estimate endpoint (nothing spent, no ledger
   row); `slate plates plan` totals every shot.
2. **Budget check**: the film's Higgsfield budget (a hard stop) and the month's cap for the film's mode.
3. **An in-flight ledger row** with the request id, written before waiting.
4. **The result**: the take, its cost, its record.

- **Submits are never retried blindly.** An ambiguous timeout closes the row as failed but counts the quoted price (it may
  have created a billed job), and says to check the console.
- **A rerun polls, never pays twice**: a request id that reached the ledger but not the take record is adopted and polled.
- **Cancel only reaches queued requests**; stopping polling doesn't cancel. `slate gen cancel --shot <S>` cancels properly.
- **Failed and moderated jobs aren't charged.**
- **Download outputs at once**: the vendor keeps output URLs only for a limited time. Slate does.

## Two wallets

| Wallet | Pays for | Status |
|---|---|---|
| **Cloud API balance** | every Slate generation (`slate gen`) | the one funded lane |
| **App credits** | Higgsfield's CLI, app and hosted connector, including its own skills (the fast lane) and its After Effects recipes | not funded by default |

Generations made in a chat through a hosted connector bypass Slate's ledger; the session's balance snapshots catch them
(slate, budgets.md). The ElevenLabs connector's third-party image and video models bill ElevenLabs credits, and bypass the
ledger too.

**Slate never buys credits, tops up a balance or upgrades a plan.** It can say when a plan would pay for itself (the
smallest plan, one month first), and the owner decides.

## What the first films cost

| Use | Roughly |
|---|---|
| A still (keyframe or look test) | cents |
| Animating a plate, 5 seconds, on a workhorse model | about $0.25 to $1 |
| A long, high-quality plate (10 to 12 seconds on a top model) | about $11 to $15 |
| A hybrid film: about 40 s of generated plates, a few takes each | about $8 to $30 |
| An all-generated six-minute cut | roughly $55 to $95 |
| An explainer's 0 to 2 generated shots | about $15 to $30 each, including one reject |

Prices move (discounts come and go); budget at list price, and check `registry.json`'s verified-on date before a big
batch.
