"""The spend ledger: one append-only record of every paid call Slate makes.

The flow for any paid call (docs/ARCHITECTURE.md §1 rule 7):

    est = ledger.estimate("elevenlabs", "tts", model="eleven_v4", chars=412)
    with ledger.spend(est, film=film_dir, mode="supervised") as row:     # budget check + in-flight row
        request_id = vendor.submit(...)
        row.request(request_id)                                            # recorded BEFORE waiting
        result = vendor.wait(request_id)
        row.actual(credits=result.cost)

Because the in-flight row carries the vendor's request id before Slate waits on it, a restart (a dead agent, a sleeping
Mac, a rate limit) finds the row with `inflight()` and polls that request instead of paying for a second one.

Budgets, enforced here whatever film.json says:

- per film: `film.json` "budget" (hard stop at the budget; 1.5× only for explicitly marked rework);
- per month and mode: `DATA/budget.json` (Oneiro's autonomous films default to 20,000 ElevenLabs credits a month);
- autonomous Higgsfield generations need an approval id (the person's yes) on every call.

Slate never buys credits, upgrades a plan or starts a licence. When money runs out, the work stops and asks.
Replay-mode rows are marked `replay` and never count as spend.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from slate import config, jsonio, paths
from slate.errors import SpendRefused, UsageError
from slate.replay import fake_vendors

UNITS = {"elevenlabs": "credits", "elevenlabs_oneiro": "credits", "higgsfield": "usd"}
MODES = ("supervised", "express", "draft", "autonomous")

DEFAULT_BUDGET = {
    "monthly": {
        # None means no monthly cap for that mode (film budgets still apply).
        "supervised": {"elevenlabs": None, "higgsfield": None},
        "express": {"elevenlabs": None, "higgsfield": None},
        "draft": {"elevenlabs": None, "higgsfield": 0},
        "autonomous": {"elevenlabs": 20000, "higgsfield": 0},
    },
    # Providers whose every call needs a person's approval id in that mode.
    "approval_required": {"autonomous": ["higgsfield"]},
    "rework_factor": 1.5,
}


@dataclass
class Estimate:
    provider: str
    op: str
    amount: float
    unit: str
    basis: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def month_key(when: _dt.datetime | None = None) -> str:
    return (when or _dt.datetime.now()).strftime("%Y-%m")


def _unit(provider: str) -> str:
    return UNITS.get(provider, "credits")


# ---------------------------------------------------------------------------------------------------------------
# Estimates


def estimate(provider: str, op: str, *, model: str | None = None, chars: int | None = None,
             seconds: float | None = None, count: int = 1, amount: float | None = None,
             basis: str | None = None, **details) -> Estimate:
    """Estimate a call from registry prices, or pass `amount` when the vendor quoted it (Higgsfield's free /estimate).

    Price entries in registry.json look like {"unit": "credits", "per_char": 1} or {"unit": "usd", "per_second": 0.46}
    or {"unit": "credits", "per_minute": 1000} or {"unit": "usd", "per_generation": 0.006}.
    """
    unit = _unit(provider)
    if amount is not None:
        return Estimate(provider, op, float(amount), unit, basis or "vendor quote", {"model": model, **details})
    info = config.model_info(model) if model else {}
    price = info.get("price") or {}
    if not price:
        raise UsageError(f"No price for {provider} {op} with model {model!r} in registry.json.",
                         hint="Add a price with its verified_on date, or pass the vendor's own estimate.")
    unit = price.get("unit", unit)
    total = 0.0
    parts = []
    if "per_char" in price:
        if chars is None:
            raise UsageError(f"{model} is priced per character; pass chars=")
        total += price["per_char"] * chars
        parts.append(f"{chars} chars × {price['per_char']}")
    if "per_second" in price:
        if seconds is None:
            raise UsageError(f"{model} is priced per second; pass seconds=")
        total += price["per_second"] * seconds
        parts.append(f"{seconds:g} s × {price['per_second']}")
    if "per_minute" in price:
        if seconds is None:
            raise UsageError(f"{model} is priced per minute; pass seconds=")
        total += price["per_minute"] * seconds / 60
        parts.append(f"{seconds / 60:.2f} min × {price['per_minute']}")
    if "per_generation" in price:
        total += price["per_generation"]
        parts.append(f"{price['per_generation']} per generation")
    total *= max(1, count)
    if count > 1:
        parts.append(f"× {count}")
    total = math.ceil(total) if unit == "credits" else round(total, 4)
    verified = info.get("verified_on", "unknown date")
    return Estimate(provider, op, total, unit, basis or f"registry price verified {verified}: " + ", ".join(parts),
                    {"model": model, "count": count, **details})


# ---------------------------------------------------------------------------------------------------------------
# Storage


def _lock_path() -> Path:
    return paths.data() / "ledger.lock"


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _append(event: dict) -> None:
    path = paths.ledger_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _events() -> list[dict]:
    path = paths.ledger_file()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn last line after a crash is ignored, never fatal
    return out


def rows(**filters) -> list[dict]:
    """Every spend, with its events folded together. Filter by provider, film, mode, status, month."""
    folded: dict[str, dict] = {}
    snapshots: list[dict] = []
    for ev in _events():
        if ev.get("event") == "snapshot":
            snapshots.append(ev)
            continue
        rid = ev.get("id")
        if not rid:
            continue
        row = folded.setdefault(rid, {"id": rid})
        kind = ev.get("event")
        if kind == "begin":
            row.update({k: v for k, v in ev.items() if k != "event"})
            row["status"] = "inflight"
        elif kind == "request":
            row.setdefault("request_ids", []).append(ev.get("request_id"))
            row["request_id"] = ev.get("request_id")
        elif kind == "finish":
            row["status"] = ev.get("status", "done")
            row["finished_at"] = ev.get("at")
            if ev.get("actual") is not None:
                row["actual"] = ev["actual"]
            if ev.get("result") is not None:
                row["result"] = ev["result"]
    out = list(folded.values())
    for key, value in filters.items():
        if value is None:
            continue
        if key == "month":
            out = [r for r in out if str(r.get("at", "")).startswith(value)]
        else:
            out = [r for r in out if r.get(key) == value]
    return out


def inflight() -> list[dict]:
    """Paid calls that started and never finished: poll these by request id before paying again."""
    return [r for r in rows() if r.get("status") == "inflight"]


def _cost(row: dict) -> float:
    if row.get("replay"):
        return 0.0
    if row.get("status") in ("failed", "cancelled") and row.get("actual") is None:
        return 0.0  # the vendors don't bill failed or moderated jobs
    actual = row.get("actual")
    if isinstance(actual, (int, float)):
        return float(actual)
    return float((row.get("estimate") or {}).get("amount") or 0.0)


def spent(provider: str, *, month: str | None = None, mode: str | None = None, film: str | None = None) -> float:
    """Spend so far (finished actuals plus in-flight estimates), for budget checks and `slate budget`."""
    total = 0.0
    for row in rows(provider=provider, mode=mode, film=film):
        if month and not str(row.get("at", "")).startswith(month):
            continue
        total += _cost(row)
    return total


# ---------------------------------------------------------------------------------------------------------------
# Budgets


def budget_config() -> dict:
    """DATA/budget.json merged over the defaults (so a partial file still works)."""
    user = jsonio.read(paths.budget_file(), default={}) or {}
    merged = json.loads(json.dumps(DEFAULT_BUDGET))
    for mode, caps in (user.get("monthly") or {}).items():
        merged["monthly"].setdefault(mode, {}).update(caps)
    if "approval_required" in user:
        merged["approval_required"].update(user["approval_required"])
    if "rework_factor" in user:
        merged["rework_factor"] = user["rework_factor"]
    return merged


def _film_info(film: Any) -> tuple[str | None, dict]:
    if film is None:
        return None, {}
    film_dir = paths.film_dir(film) if not isinstance(film, dict) else None
    fj = film if isinstance(film, dict) else jsonio.read(film_dir / "film.json")
    return fj.get("id"), fj


def check_budget(est: Estimate, *, film: Any = None, mode: str = "supervised", approval_id: str | None = None,
                 rework: bool = False) -> None:
    """Raise SpendRefused when this call would break a rule. Returns quietly when it may go ahead."""
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    if fake_vendors():
        return  # replay spends nothing
    cfg = budget_config()
    provider = "elevenlabs" if est.provider == "elevenlabs_oneiro" else est.provider
    if provider in (cfg.get("approval_required", {}).get(mode) or []) and not approval_id:
        raise SpendRefused(
            f"{provider} calls in {mode} mode wait for a person's yes, and this one has no approval id.",
            hint="Ask for approval (Oneiro sends the request by iMessage), then pass --approval <id>.",
            details={"estimate": est.to_dict()},
        )
    cap = (cfg["monthly"].get(mode) or {}).get(provider)
    if cap is not None:
        used = spent(est.provider, month=month_key(), mode=mode)
        if used + est.amount > cap:
            raise SpendRefused(
                f"This month's {mode} cap for {provider} is {cap:g} {est.unit}; {used:g} is used and this call "
                f"needs about {est.amount:g}.",
                hint="The cap lives in Slate's budget file and only a person changes it (`slate budget set`).",
                details={"estimate": est.to_dict(), "cap": cap, "used": used},
            )
    film_id, fj = _film_info(film)
    budget = (fj.get("budget") or {}) if fj else {}
    limit = budget.get(provider) if isinstance(budget.get(provider), (int, float)) else None
    if limit is not None:
        factor = cfg.get("rework_factor", 1.5) if rework else 1.0
        used = spent(est.provider, film=film_id)
        if used + est.amount > limit * factor:
            raise SpendRefused(
                f"Film {film_id}'s {provider} budget is {limit:g} {est.unit}"
                + (f" (×{factor:g} for rework)" if rework else "")
                + f"; {used:g} is spent and this call needs about {est.amount:g}.",
                hint="Spend past a film's budget needs a person's yes: raise it with `slate budget film --raise`.",
                details={"estimate": est.to_dict(), "limit": limit, "used": used},
            )


# ---------------------------------------------------------------------------------------------------------------
# Rows


def begin(est: Estimate, *, film: Any = None, mode: str = "supervised", approval_id: str | None = None,
          rework: bool = False, note: str | None = None, check: bool = True) -> str:
    """Check the budget and write the in-flight row, atomically. Returns the row id."""
    film_id, _ = _film_info(film)
    with _locked():
        if check:
            check_budget(est, film=film, mode=mode, approval_id=approval_id, rework=rework)
        rid = f"sp_{uuid.uuid4().hex[:12]}"
        _append({"event": "begin", "id": rid, "at": _now(), "provider": est.provider, "op": est.op,
                 "film": film_id, "mode": mode, "estimate": est.to_dict(), "approval_id": approval_id,
                 "rework": rework, "note": note, "replay": fake_vendors()})
    return rid


def request(row_id: str, request_id: str) -> None:
    """Record the vendor's request id as soon as it exists, before waiting on it."""
    with _locked():
        _append({"event": "request", "id": row_id, "at": _now(), "request_id": request_id})


def finish(row_id: str, *, status: str = "done", actual: float | None = None, result: Any = None) -> None:
    if status not in ("done", "failed", "cancelled"):
        raise UsageError(f"Unknown spend status {status!r}.")
    with _locked():
        _append({"event": "finish", "id": row_id, "at": _now(), "status": status, "actual": actual,
                 "result": result})


class _Spend:
    def __init__(self, row_id: str):
        self.id = row_id
        self._actual: float | None = None
        self._result: Any = None

    def request(self, request_id: str) -> None:
        request(self.id, request_id)

    def actual(self, amount: float | None = None, **result) -> None:
        self._actual = amount
        if result:
            self._result = result


@contextlib.contextmanager
def spend(est: Estimate, **kw) -> Iterator[_Spend]:
    """Wrap one paid call: budget check and in-flight row on entry, a finish row on exit (failed on an exception)."""
    handle = _Spend(begin(est, **kw))
    try:
        yield handle
    except BaseException:
        finish(handle.id, status="failed", actual=handle._actual, result=handle._result)
        raise
    finish(handle.id, status="done", actual=handle._actual, result=handle._result)


# ---------------------------------------------------------------------------------------------------------------
# Chat-lane reconciliation


def snapshot(provider: str, balance: float, *, session: str | None = None, source: str = "api") -> None:
    """Record a vendor balance. Snapshots at the start and end of a session reveal spend made outside Slate
    (for example generations in a Claude chat through the hosted connectors)."""
    with _locked():
        _append({"event": "snapshot", "at": _now(), "provider": provider, "balance": balance, "unit": _unit(provider),
                 "session": session, "source": source})


def reconcile(provider: str) -> dict:
    """Compare the last two balance snapshots with what the ledger recorded between them."""
    snaps = [e for e in _events() if e.get("event") == "snapshot" and e.get("provider") == provider]
    if len(snaps) < 2:
        return {"provider": provider, "status": "UNCHECKED", "summary": "need two balance snapshots"}
    a, b = snaps[-2], snaps[-1]
    used_by_balance = float(a["balance"]) - float(b["balance"])
    recorded = sum(_cost(r) for r in rows(provider=provider)
                   if a["at"] <= str(r.get("at", "")) <= b["at"] and r.get("status") != "inflight")
    unexplained = round(used_by_balance - recorded, 4)
    return {"provider": provider, "from": a["at"], "to": b["at"], "balance_drop": used_by_balance,
            "ledger": recorded, "unexplained": unexplained,
            "status": "PASS" if abs(unexplained) < 1e-6 else "REVIEW",
            "summary": ("balance matches the ledger" if abs(unexplained) < 1e-6 else
                        f"{unexplained:g} {_unit(provider)} spent outside Slate (chat lane or another tool)")}
