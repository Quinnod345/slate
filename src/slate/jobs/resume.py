"""Resume without paying twice.

Every paid call writes an in-flight ledger row, with the vendor's request id, before it waits (slate.ledger). If the
process dies while it waits (a crash, a reboot, an agent killed by a rate limit), the row stays in flight with a pid
that's no longer running: it's *abandoned*. When the job comes back, it must find that row and poll the request it
already paid for, never submit a second one.

Vendor modules plug in here:

    from slate.jobs import resume
    resume.register("higgsfield", poll)          # poll(request_id, row) -> {"status": "running"|"done"|"failed"|
                                                 #                            "unknown", "actual": 2.31, "result": {...}}
    resume.register_balance("elevenlabs", read)  # read() -> the current balance (credits or USD), for snapshots

and make every paid call through `paid_call`, which adopts an abandoned row for the same request key instead of paying
again:

    result = resume.paid_call(est, key=replay.request_key("higgsfield", "plate", body),
                              submit=lambda: client.submit(path, body),      # returns the request id
                              wait=client.wait,                              # request id -> result
                              cost=lambda r: r["cost"], film=film_dir, mode=mode, approval_id=approval)

The queue calls `recover(job=...)` when it requeues a job whose supervisor died, so rows a hook can settle are settled
before the job runs again. Hooks are looked up lazily: `slate.vendors.elevenlabs` and `slate.vendors.higgsfield` are
imported the first time a hook for their provider is needed, so they can register themselves at import. When the
Higgsfield module registers none, Slate's default hook asks it for one status poll (`Client().status(request_id)`).
ElevenLabs answers in the same request it bills, so its abandoned rows rarely have a request id to poll: they're closed
as orphaned and counted at their estimate until `slate ledger reconcile` settles them against the balance.

Balances for `slate ledger snapshot` come from a registered reader, or for ElevenLabs from the vendor client's
`subscription()` (the account's remaining credits). Higgsfield's Cloud API has no balance endpoint: pass --balance.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from slate import ledger, log
from slate.errors import MissingPrerequisite
from slate.jobs import proc

Poller = Callable[[str | None, dict], dict]
BalanceReader = Callable[[], float]

HOOK_MODULES = {
    "elevenlabs": "slate.vendors.elevenlabs",
    "higgsfield": "slate.vendors.higgsfield",
}
POLL_STATES = ("running", "done", "failed", "unknown")

_POLLERS: dict[str, Poller] = {}
_BALANCES: dict[str, BalanceReader] = {}
_tried: set[str] = set()


def register(provider: str, poll: Poller) -> None:
    """Register how to poll a provider's request by id: poll(request_id, ledger_row) -> outcome dict."""
    _POLLERS[provider] = poll


def register_balance(provider: str, read: BalanceReader) -> None:
    """Register how to read a provider's balance (replay-aware: the vendor client answers from fixtures in replay)."""
    _BALANCES[provider] = read


def _load(provider: str) -> None:
    module = HOOK_MODULES.get(ledger.family(provider))
    if not module or module in _tried:
        return
    _tried.add(module)
    try:
        importlib.import_module(module)
    except ImportError:
        pass  # that vendor module isn't built yet: no hook, and callers say so
    except Exception as exc:  # noqa: BLE001 - a broken vendor module must not stop recovery of the others
        log.warn(f"{module} failed to import ({type(exc).__name__}: {exc}); its requests can't be polled")


def _higgsfield_poll(request_id: str | None, row: dict) -> dict:
    """The default Higgsfield hook: one status GET through the vendor module's own client (replay-aware).

    Completed requests book the quoted price (the Cloud API has no balance endpoint); failed, moderated and cancelled
    ones aren't billed.
    """
    if not request_id:
        return {"status": "unknown", "why": "no request id to poll"}
    from slate.vendors import higgsfield  # noqa: PLC0415

    answer = higgsfield.Client().status(str(request_id))
    state = answer.get("status")
    if state == "completed":
        est = row.get("estimate") or {}
        discounted = (est.get("details") or {}).get("discounted_usd")
        actual = float(discounted) if isinstance(discounted, (int, float)) else float(est.get("amount") or 0.0)
        return {"status": "done", "actual": actual, "result": {"status": state, "recovered": True}}
    if state in getattr(higgsfield, "UNBILLED", ("failed", "nsfw", "canceled")):
        return {"status": "failed", "actual": 0.0, "why": f"Higgsfield finished it as {state} (not billed)"}
    return {"status": "running"}


DEFAULT_POLLERS: dict[str, Poller] = {"higgsfield": _higgsfield_poll}


def poller(provider: str) -> Poller | None:
    """The poll hook for a provider: one its vendor module registered, else Slate's default for it, else None."""
    fam = ledger.family(provider)
    if provider not in _POLLERS and fam not in _POLLERS:
        _load(provider)
    return _POLLERS.get(provider) or _POLLERS.get(fam) or DEFAULT_POLLERS.get(provider) or DEFAULT_POLLERS.get(fam)


def balance_reader(provider: str) -> BalanceReader | None:
    if provider not in _BALANCES:
        _load(provider)
    return _BALANCES.get(provider)


def abandoned(row: dict) -> bool:
    """An in-flight row whose writer is gone (its pid isn't the process that wrote it any more)."""
    if row.get("status") != "inflight":
        return False
    pid = row.get("pid")
    if not pid:
        return True  # written before rows carried a pid: nobody can still be waiting on it
    if pid == os.getpid():
        return False
    return not proc.alive(pid, row.get("proc_start"))


def pending(*, job: str | None = None, key: str | None = None, provider: str | None = None,
            film: str | None = None) -> list[dict]:
    """Abandoned in-flight rows, optionally only one job's, one request key's, one provider's or one film's."""
    out = []
    for row in ledger.inflight():
        if job is not None and row.get("job") != job:
            continue
        if key is not None and row.get("key") != key:
            continue
        if provider is not None and ledger.family(str(row.get("provider"))) != ledger.family(provider):
            continue
        if film is not None and row.get("film") != film:
            continue
        if abandoned(row):
            out.append(row)
    return out


def poll_row(row: dict) -> dict:
    """Ask the provider's hook about one row. Never raises: a failure to ask is an "unknown" outcome."""
    hook = poller(str(row.get("provider")))
    if hook is None:
        return {"status": "unknown", "why": f"no poll hook is registered for {row.get('provider')}"}
    try:
        outcome = hook(row.get("request_id"), row) or {}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "why": f"the poll failed: {type(exc).__name__}: {exc}"[:300]}
    status = outcome.get("status")
    if status not in POLL_STATES:
        return {"status": "unknown", "why": f"the hook answered {status!r}"}
    return outcome


def recover(*, job: str | None = None, key: str | None = None, provider: str | None = None,
            film: str | None = None) -> list[dict]:
    """Settle abandoned rows through their provider hooks. Returns one outcome per row:

    - done or failed at the vendor: the row is finished with the vendor's answer (nothing is paid again);
    - still running: the row stays in flight, and the resumed job adopts it (`paid_call`) and waits on it;
    - no request id and no way to find out: the row is closed as orphaned (it keeps counting at its estimate, and
      `slate ledger reconcile` settles it against the balance);
    - a request id but no hook yet: left in flight for a process that has the vendor module.
    """
    outcomes = []
    for row in pending(job=job, key=key, provider=provider, film=film):
        rid = row.get("request_id")
        outcome = poll_row(row)
        state = outcome.get("status")
        entry = {"row": row["id"], "provider": row.get("provider"), "request_id": rid, "job": row.get("job"),
                 "status": state}
        if state == "done":
            ledger.finish(row["id"], status="done", actual=outcome.get("actual"), result=outcome.get("result"))
            entry["action"] = "finished from the vendor's answer; nothing paid twice"
        elif state == "failed":
            ledger.finish(row["id"], status="failed", actual=outcome.get("actual"),
                          reason=str(outcome.get("why") or "the vendor reports it failed"))
            entry["action"] = "closed as failed (vendors don't bill failed jobs)"
        elif state == "running":
            entry["action"] = "still running at the vendor; the resumed job will wait on it"
        elif not rid:
            ledger.orphan(row["id"], "its process died before the vendor gave a request id, so Slate can't tell "
                                     "whether it billed")
            entry["status"] = "orphaned"
            entry["action"] = "closed as orphaned (counted at its estimate until a reconcile settles it)"
        else:
            entry["action"] = f"left in flight: {outcome.get('why')}"
        outcomes.append(entry)
    return outcomes


def paid_call(est: ledger.Estimate, *, key: str, submit: Callable[[], Any],
              wait: Callable[[str], Any] | None = None, cost: Callable[[Any], float | None] | None = None,
              summarize: Callable[[Any], dict] | None = None, request_id_of: Callable[[Any], str | None] | None = None,
              film: Any = None, mode: str = "supervised", approval_id: str | None = None, rework: bool = False,
              note: str | None = None) -> Any:
    """One paid call that never pays twice for the same request.

    Asynchronous vendors (Higgsfield): `submit()` returns the request id and `wait(request_id)` the result. If an
    abandoned in-flight row with the same `key` has a request id, this adopts it and waits on that request instead of
    submitting again.

    Synchronous vendors (ElevenLabs TTS answers in the same request): pass `wait=None`; `submit()` returns the result,
    and `request_id_of(result)` gives the vendor's id to record. An abandoned row without a request id can't be polled,
    so it's closed as orphaned (counted at its estimate) and the call is made again.
    """
    if not key:
        raise ValueError("paid_call needs a stable request key (slate.replay.request_key(provider, op, body))")
    for row in pending(key=key, provider=est.provider):
        rid = row.get("request_id")
        if rid and wait is not None:
            log.info(f"resuming: {row.get('provider')} request {rid} was already paid for; waiting on it instead "
                     "of paying again")
            ledger.adopt(row["id"])
            try:
                result = wait(rid)
            except BaseException as exc:
                ledger.finish(row["id"], status="failed", reason=f"{type(exc).__name__}: {exc}"[:300])
                raise
            ledger.finish(row["id"], status="done", actual=cost(result) if cost else None,
                          result=summarize(result) if summarize else None)
            return result
        outcome = poll_row(row) if not rid else {"status": "unknown"}
        if outcome.get("status") in ("done", "failed"):
            ledger.finish(row["id"], status=outcome["status"], actual=outcome.get("actual"),
                          result=outcome.get("result"), reason=outcome.get("why"))
        else:
            ledger.orphan(row["id"], "its process died before the vendor gave a request id, so Slate can't tell "
                                     "whether it billed")
    with ledger.spend(est, film=film, mode=mode, approval_id=approval_id, rework=rework, note=note, key=key) as row:
        if wait is not None:
            rid = submit()
            row.request(str(rid))
            result = wait(str(rid))
        else:
            result = submit()
            rid = request_id_of(result) if request_id_of else None
            if rid:
                row.request(str(rid))
        row.actual(cost(result) if cost else None, **(summarize(result) if summarize else {}))
    return result


def balance_of(provider: str) -> float:
    """The provider's balance right now, from its vendor module (replay-aware). Raises MissingPrerequisite (exit 3)
    when Slate can't read it by itself (Higgsfield's Cloud API has no balance endpoint: pass --balance)."""
    reader = balance_reader(provider)
    if reader is not None:
        return float(reader())
    if ledger.family(provider) == "elevenlabs":
        try:
            from slate.vendors import elevenlabs  # noqa: PLC0415

            # The subscription is the account's: both keys (yours and Oneiro's capped one) spend the same credits.
            sub = elevenlabs.Client(key=provider).subscription()
        except ImportError:
            sub = None
        if isinstance(sub, dict):
            limit, used = sub.get("character_limit"), sub.get("character_count")
            if isinstance(limit, (int, float)) and isinstance(used, (int, float)):
                return float(limit) - float(used)
            for name in ("balance", "credits_remaining", "remaining"):
                if isinstance(sub.get(name), (int, float)):
                    return float(sub[name])
    raise MissingPrerequisite(
        f"Slate can't read the {provider} balance by itself yet.",
        hint=f"Read it from the vendor's dashboard and pass it: slate ledger snapshot --provider {provider} "
             "--balance <amount>",
    )
