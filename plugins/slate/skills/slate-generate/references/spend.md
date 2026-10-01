# Spend on generation

## How a generation is paid for

1. **Estimate first**: `slate gen estimate --shot <S>` asks the API's free estimate endpoint (nothing spent, no ledger
   row); `slate plates plan` totals every shot. In the answer, **`usd` is the price** (after any discount) and
   **`discount.usd` is the saving**, never the price: the ledger books `usd`, and the list price is the two added up.
   Some models (Seedance 2.5) answer with a description instead of a number; then the registry's price for the
   resolution asked for stands in.
2. **Budget check**: the film's Higgsfield budget (a hard stop) and the month's cap for the film's mode.
3. **An in-flight ledger row** with the request id, written before waiting.
4. **The result**: the take, its cost, its record.

- **Every submit carries an Idempotency-Key**, derived from its ledger row, so the same request always has the same
  key. When an answer is lost (a timeout, a dropped connection, a 5xx), Slate sends it again **with the same key and
  the same body**, a few times: Higgsfield answers a repeat with the original request and charges once. A rerun after
  a crash does the same. Only a submit sent without a key is never retried.
- **An answer that never came** after those tries closes the row as failed but counts the quoted price (it may have
  created a billed job). With a key it's safe to run again later, so a queued job goes back in the queue by itself
  (exit 75); without one, check the console first.
- **A rate limit or the account's concurrency cap** created nothing: it's busy (exit 5), and the queue retries later.
- **A rerun polls, never pays twice**: a request id that reached the ledger but not the take record is adopted and polled.
- **A yes covers what it asked for, one call each**: an autonomous film's approval request for N takes spends as N
  slots (`<yes>#1` to `<yes>#N`), each recorded on its own ledger row, so one yes can't be spent twice. A slot whose
  call failed or was cancelled (nothing billed) is free again.
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

Prices move (discounts come and go, and `registry.json`'s price notes say when each one ends); budget at list price,
and check `registry.json`'s verified-on date before a big batch.

**Resolution is a spend decision.** Video plates render at 720p by default. Seedance 2.5 also offers 1080p on every
plate workflow, at 2.46x the price (about $5.69 for a 5-second plate instead of $2.31); moving up is the owner's call,
recorded once in Slate's settings (`[gen] video_resolution`) or on one shot (`resolution` in its generate block).
