"""The spend ledger: one append-only record of every paid call Slate makes, and the budgets that bound them.

The flow for any paid call (docs/ARCHITECTURE.md §1 rule 7):

    est = ledger.estimate("elevenlabs", "tts", model="eleven_v4", chars=412)
    with ledger.spend(est, film=film_dir, mode="supervised", key=request_key) as row:   # budget check + in-flight row
        request_id = vendor.submit(...)
        row.request(request_id)                                            # recorded BEFORE waiting
        result = vendor.wait(request_id)
        row.actual(credits=result.cost)

Because the in-flight row carries the vendor's request id (and the pid, job and request key of whoever made it) before
Slate waits on it, a restart (a dead agent, a sleeping Mac, a rate limit) finds the row and polls that request instead
of paying for a second one. `slate.jobs.resume.paid_call` wraps this whole dance for vendor modules.

Budgets, enforced here whatever film.json says:

- per film: its "budget" in film.json, or its format's default budget when it has none (hard stop at the budget; 1.5×
  only for explicitly marked rework). A person's yes raises it: `slate budget film --raise`, recorded here;
- per month and mode: `DATA/budget.json` (autonomous films default to 20,000 ElevenLabs credits and $0 of Higgsfield a
  month). A film.json that says "autonomous" is always held to the autonomous caps, whatever mode a caller passes;
- autonomous Higgsfield generations need an approval id (the person's yes) on every call, and each yes covers one call;
- autonomous ElevenLabs spend goes through Oneiro's own capped key, so a vendor-side quota backs Slate's cap.

Slate never buys credits, upgrades a plan or starts a licence. When money runs out, the work stops and asks
(`out_of_balance`). Replay-mode rows are marked `replay` and never count as spend.

Months are UTC calendar months. Rows and snapshots carry UTC timestamps.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import getpass
import json
import math
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from slate import config, jsonio, log, paths
from slate.errors import MissingPrerequisite, Refused, SpendRefused, UsageError
from slate.replay import fake_vendors

UNITS = {"elevenlabs": "credits", "elevenlabs_oneiro": "credits", "higgsfield": "usd"}
# Keys that bill the same account share a budget: Oneiro's capped ElevenLabs key spends ElevenLabs credits.
FAMILY = {"elevenlabs_oneiro": "elevenlabs"}
PROVIDERS = ("elevenlabs", "higgsfield")
MODES = ("supervised", "express", "draft", "autonomous")
ROW_STATUSES = ("inflight", "done", "failed", "cancelled", "orphaned")
# Rounding slack when comparing a balance drop with the ledger (a credit, or a cent).
TOLERANCE = {"credits": 1.0, "usd": 0.01}
PRICE_STALE_DAYS = 45

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
    # Autonomous ElevenLabs spend must go through Oneiro's own key, created with a credit quota and only TTS, music
    # and alignment scopes, so the vendor's own cap backs Slate's (plan §7, autonomous mode).
    "require_key": {"autonomous": {"elevenlabs": "elevenlabs_oneiro"}},
}

# Default film budgets by format, from what films A-C actually spent (plan §8). The format templates in formats/*.json
# are the source of truth when they carry a budget; this table is the fallback, and what `slate budget show` explains.
PLAN_FORMAT_BUDGETS: dict[str, dict] = {
    "reel": {"label": "Reel or ad (6-45 s)", "elevenlabs": {"vo": [500, 3000], "score": [1000, 2000]},
             "higgsfield": [0, 0], "note": "Generation only when it's allowed and adds something. About 1% of a Pro month"},
    "explainer": {"label": "Explainer (60-120 s)", "elevenlabs": {"vo": [1000, 5000], "score": [2500, 5000]},
                  "higgsfield": [0, 60], "note": "0-2 generated shots at about $15-30 each, including one reject"},
    "brand_film": {"label": "Brand film (2-6 min)", "elevenlabs": {"vo": [4000, 12000], "score": [9000, 28000]},
                   "higgsfield": [40, 85], "note": "As on films A and B (VO includes tag A/Bs)"},
    "onboarding": {"label": "Brand onboarding (once)", "elevenlabs": {"voice_design": [5000, 5000]},
                   "higgsfield": [15, 20], "note": "Voice Design about 5,000 credits; look tests $15-20; the sound "
                                                  "reference is made locally"},
}
_FORMAT_WORDS = (
    ("onboarding", ("onboard",)),
    ("brand_film", ("brand-film", "brand_film", "brandfilm", "character")),
    ("explainer", ("explainer", "demo")),
    ("reel", ("reel", "ad", "ads", "social", "sting", "teaser", "announcement", "linkedin", "loop", "preview",
              "kinetic", "logo", "short", "shorts", "tiktok", "instagram")),
)


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
    """The budget month (UTC), matching the UTC timestamps on every row."""
    moment = when or _dt.datetime.now(_dt.timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(_dt.timezone.utc)
    return moment.strftime("%Y-%m")


def family(provider: str) -> str:
    """The account a provider bills (elevenlabs_oneiro spends ElevenLabs credits)."""
    return FAMILY.get(provider, provider)


def _unit(provider: str) -> str:
    return UNITS.get(provider, UNITS.get(family(provider), "credits"))


def money(amount: float | None, unit: str) -> str:
    """An amount the way people read it: $2.31, or 4,200 credits."""
    if amount is None:
        return "none"
    return f"${amount:,.2f}" if unit == "usd" else f"{amount:,.0f} credits"


def _finite(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise UsageError(f"{what} should be a number (got {value!r}).") from None
    if math.isnan(number) or math.isinf(number):
        raise UsageError(f"{what} should be a finite number (got {value!r}).")
    return number


# ---------------------------------------------------------------------------------------------------------------
# Estimates


def _price_age_days(verified: str | None) -> int | None:
    if not verified:
        return None
    try:
        day = _dt.date.fromisoformat(str(verified)[:10])
    except ValueError:
        return None
    return (_dt.datetime.now(_dt.timezone.utc).date() - day).days


def estimate(provider: str, op: str, *, model: str | None = None, chars: int | None = None,
             seconds: float | None = None, count: int = 1, amount: float | None = None,
             basis: str | None = None, **details) -> Estimate:
    """Estimate a call from registry prices, or pass `amount` when the vendor quoted it (Higgsfield's free /estimate).

    Price entries in registry.json look like {"unit": "credits", "per_char": 1} or {"unit": "usd", "per_second": 0.46}
    or {"unit": "credits", "per_minute": 1000} or {"unit": "usd", "per_generation": 0.006}. Each carries the date it
    was verified; an estimate from a price older than 45 days says so (refresh registry.json before production).
    """
    unit = _unit(provider)
    if amount is not None:
        return Estimate(provider, op, _finite(amount, "An estimate"), unit, basis or "vendor quote",
                        {"model": model, **details})
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
    extra: dict[str, Any] = {"model": model, "count": count, "price_verified_on": info.get("verified_on"), **details}
    age = _price_age_days(info.get("verified_on"))
    if age is not None and age > PRICE_STALE_DAYS:
        extra["price_stale"] = True
        log.warn(f"the registry price for {model} was verified {age} days ago; refresh registry.json before "
                 "trusting this estimate")
    return Estimate(provider, op, total, unit, basis or f"registry price verified {verified}: " + ", ".join(parts),
                    extra)


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


def _identity() -> dict:
    """Who is writing: pid and start time (so a restart can tell an abandoned row from a live one), job, agent."""
    from slate.jobs import proc  # noqa: PLC0415 (slate.jobs is a light package; lazy keeps the ledger import-cheap)

    return {"pid": os.getpid(), "proc_start": proc.own_stamp(), "job": os.environ.get("SLATE_JOB_ID") or None}


def _fold(events: list[dict]) -> list[dict]:
    folded: dict[str, dict] = {}
    for ev in events:
        kind = ev.get("event")
        if kind in ("snapshot", "raise", "cap"):
            continue
        rid = ev.get("id")
        if not rid:
            continue
        row = folded.setdefault(rid, {"id": rid})
        if kind == "begin":
            row.update({k: v for k, v in ev.items() if k != "event"})
            row["status"] = "inflight"
        elif kind == "request":
            row.setdefault("request_ids", []).append(ev.get("request_id"))
            row["request_id"] = ev.get("request_id")
        elif kind == "adopt":
            # A restarted job took this row over: it now waits on the same request instead of paying again.
            row["adopted_by"] = {k: ev.get(k) for k in ("pid", "job", "at")}
            row["pid"] = ev.get("pid")
            row["proc_start"] = ev.get("proc_start")
            row["job"] = ev.get("job") or row.get("job")
        elif kind == "finish":
            row["status"] = ev.get("status", "done")
            row["finished_at"] = ev.get("at")
            if ev.get("actual") is not None:
                row["actual"] = ev["actual"]
            if ev.get("result") is not None:
                row["result"] = ev["result"]
            if ev.get("reason"):
                row["reason"] = ev["reason"]
    return list(folded.values())


def rows(**filters) -> list[dict]:
    """Every spend, with its events folded together. Filter by provider, film, mode, status, month, key, job."""
    out = _fold(_events())
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
    # in flight, orphaned (we can't tell whether it billed) or done without an actual: count the estimate
    return float((row.get("estimate") or {}).get("amount") or 0.0)


def spent(provider: str, *, month: str | None = None, mode: str | None = None, film: str | None = None,
          _rows: list[dict] | None = None) -> float:
    """Spend so far (finished actuals plus in-flight and orphaned estimates), for budget checks and `slate budget`.

    Counts every key that bills the same account (Oneiro's capped key counts as ElevenLabs).
    """
    fam = family(provider)
    total = 0.0
    for row in (_rows if _rows is not None else rows()):
        if family(str(row.get("provider"))) != fam:
            continue
        if mode is not None and row.get("mode") != mode:
            continue
        if film is not None and row.get("film") != film:
            continue
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
        merged["monthly"].setdefault(mode, {}).update(caps or {})
    if "approval_required" in user:
        merged["approval_required"].update(user["approval_required"] or {})
    if "rework_factor" in user:
        merged["rework_factor"] = user["rework_factor"]
    if "require_key" in user:
        for mode, keys in (user["require_key"] or {}).items():
            merged["require_key"].setdefault(mode, {}).update(keys or {})
    return merged


def _film_info(film: Any) -> tuple[str | None, dict]:
    if film is None:
        return None, {}
    if isinstance(film, dict):
        return film.get("id"), film
    fj = jsonio.read(paths.film_dir(film) / "film.json")
    return fj.get("id"), fj


def effective_mode(mode: str | None, film_json: dict | None = None) -> str:
    """The mode spend is judged in. A film that says autonomous is always autonomous, whatever the caller passes."""
    film_mode = (film_json or {}).get("mode")
    if film_mode == "autonomous" or mode == "autonomous":
        return "autonomous"
    chosen = mode or film_mode or "supervised"
    if chosen not in MODES:
        raise UsageError(f"Unknown mode {chosen!r}; one of {', '.join(MODES)}.")
    return chosen


def _amount(value: Any) -> float | None:
    """A budget amount from a number, a [low, high] range (the high end is the hard stop), a {max|cap|limit|total}
    dict, or a dict of parts (vo, score, ...) that add up."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        numbers = [float(v) for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return max(numbers) if numbers else None
    if isinstance(value, dict):
        for key in ("max", "cap", "limit", "total", "high"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
        parts = [_amount(v) for k, v in value.items() if k not in ("unit", "note", "label", "min", "low")]
        parts = [p for p in parts if p is not None]
        return sum(parts) if parts else None
    return None


def _normalize_budget(raw: Any) -> dict[str, float]:
    """{provider: amount} from any of the budget shapes film.json or a format template may use."""
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        name = str(key)
        for suffix in ("_credits", "_usd", "_dollars"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        name = family(name)
        if name not in PROVIDERS:
            continue
        amount = _amount(value)
        if amount is not None:
            out[name] = amount
    return out


def _plan_family(fmt: str) -> str | None:
    text = str(fmt).lower().replace(" ", "-")
    words = set(text.replace("_", "-").split("-")) | {text}
    for name, keys in _FORMAT_WORDS:
        if any(k in words or (len(k) > 3 and k in text) for k in keys):
            return name
    return None


def format_budget(fmt: str | None) -> dict:
    """A format's default film budget: {"elevenlabs": credits, "higgsfield": usd, "source": ...}.

    Reads formats/<fmt>.json through slate.formats.load when that module exists and the template carries a budget;
    otherwise falls back to the plan's table (PLAN_FORMAT_BUDGETS). Empty when neither knows the format.
    """
    if not fmt:
        return {}
    try:
        from slate import formats  # noqa: PLC0415 (built by another module; optional)

        template = formats.load(fmt)
    except Exception:  # noqa: BLE001 - no formats module yet, or no such format: fall back to the plan's table
        template = None
    if isinstance(template, dict):
        raw = template.get("budget") or template.get("budgets") or template.get("default_budget")
        amounts = _normalize_budget(raw)
        if amounts:
            return {**amounts, "source": f"formats/{fmt}.json"}
    plan = _plan_family(fmt)
    if plan:
        amounts = _normalize_budget({k: v for k, v in PLAN_FORMAT_BUDGETS[plan].items() if k in PROVIDERS})
        return {**amounts, "source": f"plan default: {PLAN_FORMAT_BUDGETS[plan]['label']}"}
    return {}


def film_budget(film_json: dict | None) -> dict:
    """A film's base budget per provider: film.json's own numbers, with any provider it leaves out taken from its
    format's default. Raises (a person's yes) are added by `film_limits`."""
    fj = film_json or {}
    own = _normalize_budget(fj.get("budget") or {})
    out: dict[str, Any] = dict(own)
    sources = {p: "film.json" for p in own}
    missing = [p for p in PROVIDERS if p not in own]
    if missing and fj.get("format"):
        defaults = format_budget(fj.get("format"))
        for p in missing:
            if p in defaults:
                out[p] = defaults[p]
                sources[p] = defaults.get("source", "format default")
    out["sources"] = sources
    return out


def raises(film_id: str | None = None, provider: str | None = None, *, _events_list: list[dict] | None = None
           ) -> list[dict]:
    """Budget raises a person approved (`slate budget film --raise`)."""
    out = [e for e in (_events_list if _events_list is not None else _events()) if e.get("event") == "raise"]
    if film_id is not None:
        out = [e for e in out if e.get("film") == film_id]
    if provider is not None:
        out = [e for e in out if family(str(e.get("provider"))) == family(provider)]
    return out


def film_limits(film: Any, *, _events_list: list[dict] | None = None) -> dict:
    """Per provider: the base budget, approved raises, the budget (base + raises) and what's spent so far."""
    film_id, fj = _film_info(film)
    events = _events_list if _events_list is not None else _events()
    folded = _fold(events)
    base = film_budget(fj)
    out: dict[str, Any] = {"film": film_id, "mode": effective_mode(None, fj), "providers": {}}
    for p in PROVIDERS:
        raised = sum(float(e.get("amount") or 0) for e in raises(film_id, p, _events_list=events)) if film_id else 0.0
        amount = base.get(p)
        out["providers"][p] = {
            "unit": _unit(p),
            "base": amount,
            "source": base["sources"].get(p),
            "raised": raised,
            "budget": None if amount is None else amount + raised,
            "spent": spent(p, film=film_id, _rows=folded) if film_id else 0.0,
        }
    return out


def rework_factor(film_json: dict | None = None, cfg: dict | None = None) -> float:
    """How far past its budget a film may go for rework (1.5× by default). A supervised film's own film.json may set
    it; an autonomous film's can only tighten it, never loosen it."""
    base = float((cfg or budget_config()).get("rework_factor", 1.5))
    own = ((film_json or {}).get("budget") or {}).get("rework_factor")
    if isinstance(own, (int, float)) and not isinstance(own, bool) and own >= 1:
        if effective_mode(None, film_json) == "autonomous":
            return min(base, float(own))
        return float(own)
    return base


def approval_used(approval_id: str, *, _rows: list[dict] | None = None) -> bool:
    """Has this approval id already paid for a call? (A failed or cancelled call leaves it unused.)"""
    for row in (_rows if _rows is not None else rows()):
        if row.get("approval_id") == approval_id and row.get("status") not in ("failed", "cancelled"):
            return True
    return False


def brand_approved(brand: Any) -> bool | None:
    """Are all four of a brand's references approved? None when Slate can't tell (the brand module or pack isn't
    there); the brand module and `slate new` enforce it too, this is the spend-side check."""
    try:
        from slate.brand import pack  # noqa: PLC0415 (built by another module; optional)

        return bool(pack.references_approved(pack.load(brand)))
    except Exception:  # noqa: BLE001 - unknown is not a refusal here
        return None


def secret_name(provider: str, mode: str) -> str:
    """The key a vendor module should use for this mode (autonomous ElevenLabs spend uses Oneiro's capped key)."""
    need = (budget_config().get("require_key", {}).get(mode) or {}).get(family(provider))
    return need or provider


def check_budget(est: Estimate, *, film: Any = None, mode: str = "supervised", approval_id: str | None = None,
                 rework: bool = False) -> None:
    """Raise SpendRefused when this call would break a rule. Returns quietly when it may go ahead."""
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    if fake_vendors():
        return  # replay spends nothing
    cfg = budget_config()
    fam = family(est.provider)
    film_id, fj = _film_info(film)
    mode = effective_mode(mode, fj)
    events = _events()
    folded = _fold(events)
    if mode == "autonomous" and fj.get("brand") and brand_approved(fj["brand"]) is False:
        raise SpendRefused(
            f"Film {film_id}'s brand {fj['brand']!r} doesn't have all four references approved, and autonomous films "
            "only spend for approved brands.",
            hint="The film parks with a question for a person: onboard the brand and approve its references first.",
            details={"estimate": est.to_dict(), "brand": fj["brand"]},
        )
    if fam in (cfg.get("approval_required", {}).get(mode) or []) and not approval_id:
        raise SpendRefused(
            f"{fam} calls in {mode} mode wait for a person's yes, and this one has no approval id.",
            hint="Ask for approval (Oneiro sends the request by iMessage), then pass --approval <id>.",
            details={"estimate": est.to_dict()},
        )
    if approval_id and approval_used(approval_id, _rows=folded):
        raise SpendRefused(
            f"Approval {approval_id} already paid for another call; each yes covers one call.",
            hint="Ask the person again for this one.",
            details={"estimate": est.to_dict(), "approval_id": approval_id},
        )
    cap = (cfg["monthly"].get(mode) or {}).get(fam)
    if cap is not None:
        used = spent(fam, month=month_key(), mode=mode, _rows=folded)
        if used + est.amount > float(cap) + 1e-9:
            raise SpendRefused(
                f"This month's {mode} cap for {fam} is {float(cap):g} {est.unit}; {used:g} is used and this call "
                f"needs about {est.amount:g}.",
                hint="The cap lives in Slate's budget file and only a person changes it, in Terminal "
                     "(`slate budget set`). The work stops here and asks.",
                details={"estimate": est.to_dict(), "cap": cap, "used": used, "mode": mode},
            )
    if film_id:
        limits = film_limits(fj if fj else film, _events_list=events)["providers"].get(fam) or {}
        budget = limits.get("budget")
        if budget is not None:
            factor = rework_factor(fj, cfg) if rework else 1.0
            used = limits.get("spent", 0.0)
            if used + est.amount > budget * factor + 1e-9:
                if mode == "autonomous":
                    hint = ("Autonomous films park here and ask: their spend is capped, and only a person raises the "
                            "monthly cap (`slate budget set`, in Terminal).")
                else:
                    hint = ("Spend past a film's budget needs a person's yes: record it with "
                            f"`slate budget film --film {film_id} --provider {fam} --raise N --approved-by NAME`.")
                raise SpendRefused(
                    f"Film {film_id}'s {fam} budget is {budget:g} {est.unit}"
                    + (f" (×{factor:g} for rework)" if rework else "")
                    + f"; {used:g} is spent and this call needs about {est.amount:g}.",
                    hint=hint,
                    details={"estimate": est.to_dict(), "limit": budget, "used": used, "film": film_id},
                )
    need = (cfg.get("require_key", {}).get(mode) or {}).get(fam)
    if need and need != est.provider:
        from slate import secrets  # noqa: PLC0415

        if not secrets.present(need):
            raise MissingPrerequisite(
                f"{mode.capitalize()} {fam} spend goes through its own capped key ({need}), and that key isn't "
                "connected.",
                hint=f"Create a key with a credit quota in the vendor's dashboard, then run `slate connect "
                     f"elevenlabs --oneiro` in your own Terminal.",
                details={"estimate": est.to_dict(), "needs_key": need},
            )


# ---------------------------------------------------------------------------------------------------------------
# Rows


def begin(est: Estimate, *, film: Any = None, mode: str = "supervised", approval_id: str | None = None,
          rework: bool = False, note: str | None = None, check: bool = True, key: str | None = None) -> str:
    """Check the budget and write the in-flight row, atomically. Returns the row id.

    `key` is a stable id for the request (slate.replay.request_key, say): a restarted job finds its own in-flight row
    by it and polls that request instead of paying again.
    """
    film_id, fj = _film_info(film)
    mode_used = effective_mode(mode, fj) if mode in MODES else mode
    with _locked():
        if check:
            check_budget(est, film=fj if fj else film, mode=mode, approval_id=approval_id, rework=rework)
        rid = f"sp_{uuid.uuid4().hex[:12]}"
        _append({"event": "begin", "id": rid, "at": _now(), "provider": est.provider, "op": est.op,
                 "film": film_id, "mode": mode_used, "estimate": est.to_dict(), "approval_id": approval_id,
                 "rework": rework, "note": note, "replay": fake_vendors(), "key": key, **_identity()})
    return rid


def request(row_id: str, request_id: str) -> None:
    """Record the vendor's request id as soon as it exists, before waiting on it."""
    with _locked():
        _append({"event": "request", "id": row_id, "at": _now(), "request_id": request_id})


def adopt(row_id: str) -> None:
    """Take over an abandoned in-flight row: this process will wait on its request instead of paying again."""
    with _locked():
        _append({"event": "adopt", "id": row_id, "at": _now(), **_identity()})


def finish(row_id: str, *, status: str = "done", actual: float | None = None, result: Any = None,
           reason: str | None = None) -> None:
    if status not in ("done", "failed", "cancelled", "orphaned"):
        raise UsageError(f"Unknown spend status {status!r}.")
    with _locked():
        _append({"event": "finish", "id": row_id, "at": _now(), "status": status, "actual": actual,
                 "result": result, "reason": reason})


def orphan(row_id: str, reason: str) -> None:
    """Close a row whose caller died before the vendor gave a request id: nobody can tell whether it billed, so it
    keeps counting at its estimate, and `slate ledger reconcile` settles it against the balance."""
    finish(row_id, status="orphaned", reason=reason)


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
    except BaseException as exc:
        finish(handle.id, status="failed", actual=handle._actual, result=handle._result,
               reason=f"{type(exc).__name__}: {exc}"[:300])
        raise
    finish(handle.id, status="done", actual=handle._actual, result=handle._result)


def out_of_balance(provider: str, detail: str | None = None) -> SpendRefused:
    """The error a vendor module raises when the vendor says the balance is gone (HTTP 402, "quota exceeded").

    Slate never buys credits or upgrades a plan, so the work stops here and asks a person.
    """
    return SpendRefused(
        f"The {provider} balance has run out" + (f" ({detail})" if detail else "") + ". Slate never buys credits or "
        "upgrades a plan, so the work stops here.",
        hint="Top up in the vendor's own dashboard yourself, then retry (`slate job retry <id>` for a queued job).",
        details={"provider": provider},
    )


# ---------------------------------------------------------------------------------------------------------------
# Budget changes (a person's decisions, recorded)


def person_at_terminal() -> tuple[bool, str]:
    """(True, "") only for a person typing in their own Terminal: no agent around, and stdin is a real TTY."""
    from slate import procs  # noqa: PLC0415

    ctx = procs.agent_context()
    if ctx:
        return False, f"this is running under {ctx}"
    try:
        tty = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        tty = False
    if not tty:
        return False, "this isn't an interactive Terminal (stdin isn't a TTY)"
    return True, ""


def set_cap(mode: str, provider: str, cap: float | None, *, approval_required: bool | None = None) -> dict:
    """Change a monthly cap in DATA/budget.json. Only a person, in their own Terminal, may do this."""
    ok, why = person_at_terminal()
    if not ok:
        raise Refused(
            f"Monthly caps are changed only by a person, in their own Terminal ({why}).",
            hint=f"Open Terminal and run: slate budget set --mode {mode} --provider {provider} --cap <amount|none>",
        )
    if mode not in MODES:
        raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(MODES)}.")
    fam = family(provider)
    if fam not in PROVIDERS:
        raise UsageError(f"Unknown provider {provider!r}; one of {', '.join(PROVIDERS)}.")
    if cap is not None:
        cap = _finite(cap, "A cap")
        if cap < 0:
            raise UsageError("A cap can't be negative (use 0 to stop that spend, or none for no cap).")
    path = paths.budget_file()
    with _locked():
        user = jsonio.read(path, default={}) or {}
        before = budget_config()
        was = (before["monthly"].get(mode) or {}).get(fam)
        user.setdefault("monthly", {}).setdefault(mode, {})[fam] = cap
        if approval_required is not None:
            listed = list((user.get("approval_required") or before.get("approval_required", {})).get(mode) or [])
            if approval_required and fam not in listed:
                listed.append(fam)
            if not approval_required and fam in listed:
                listed.remove(fam)
            user.setdefault("approval_required", {})[mode] = listed
        change = {"at": _now(), "mode": mode, "provider": fam, "from": was, "to": cap,
                  "approval_required": approval_required, "by": getpass.getuser()}
        user.setdefault("history", []).append(change)
        jsonio.write_atomic(path, user)
        _append({"event": "cap", **change})
    return change


def raise_film_budget(film: Any, provider: str, amount: float, *, approved_by: str, note: str | None = None) -> dict:
    """Record a person's yes to spend past a supervised film's budget. Refused for autonomous films: their cap is the
    monthly budget file, which only a person changes."""
    film_id, fj = _film_info(film)
    if not film_id:
        raise UsageError("That film has no id in its film.json, so a raise can't be recorded against it.")
    mode = effective_mode(None, fj)
    if mode == "autonomous":
        raise SpendRefused(
            f"Film {film_id} is autonomous: its spend is capped by the monthly budget file, never by a per-film raise.",
            hint="The film parks and asks. A person can change the autonomous cap in Terminal with `slate budget set`.",
            details={"film": film_id, "mode": mode},
        )
    fam = family(provider)
    if fam not in PROVIDERS:
        raise UsageError(f"Unknown provider {provider!r}; one of {', '.join(PROVIDERS)}.")
    amount = _finite(amount, "--raise")
    if amount <= 0:
        raise UsageError("--raise adds to the film's budget, so it has to be more than 0.")
    who = (approved_by or "").strip()
    if not who:
        raise UsageError("--approved-by needs the name of the person who said yes.")
    from slate import procs  # noqa: PLC0415

    event = {"event": "raise", "id": f"rb_{uuid.uuid4().hex[:10]}", "at": _now(), "film": film_id, "provider": fam,
             "amount": amount, "unit": _unit(fam), "approved_by": who, "note": note,
             "recorded_by": procs.agent_context() or "person"}
    with _locked():
        _append(event)
    return event


# ---------------------------------------------------------------------------------------------------------------
# Chat-lane reconciliation


def snapshot(provider: str, balance: float, *, session: str | None = None, source: str = "api",
             note: str | None = None) -> dict:
    """Record a vendor balance. Snapshots at the start and end of a session reveal spend made outside Slate
    (for example generations in a Claude chat through the hosted connectors)."""
    event = {"event": "snapshot", "at": _now(), "provider": provider, "balance": _finite(balance, "A balance"),
             "unit": _unit(provider), "session": session, "source": source, "note": note, "replay": fake_vendors()}
    with _locked():
        _append(event)
    return event


def snapshots(provider: str | None = None, *, session: str | None = None, replay: bool | None = None) -> list[dict]:
    out = [e for e in _events() if e.get("event") == "snapshot"]
    if provider is not None:
        out = [e for e in out if e.get("provider") == provider]
    if session is not None:
        out = [e for e in out if e.get("session") == session]
    if replay is not None:
        out = [e for e in out if bool(e.get("replay")) == replay]
    return out


def reconcile(provider: str, *, session: str | None = None) -> dict:
    """Compare a balance drop with what the ledger recorded over the same span.

    With `session`, the span runs from that session's first snapshot to its last; otherwise it's the last two
    snapshots. PASS when they agree (within a credit or a cent); REVIEW when they don't, which usually means spend
    made outside Slate (a chat-lane generation through a hosted connector), a top-up or the monthly reset; UNCHECKED
    with fewer than two snapshots. Snapshots taken in replay mode are only compared with each other.
    """
    snaps = snapshots(provider, session=session, replay=fake_vendors())
    unit = _unit(provider)
    if len(snaps) < 2:
        where = f" in session {session}" if session else ""
        return {"provider": provider, "status": "UNCHECKED", "unit": unit, "session": session,
                "summary": f"need two balance snapshots{where} (slate ledger snapshot --provider {provider})"}
    a, b = (snaps[0], snaps[-1]) if session else (snaps[-2], snaps[-1])
    used_by_balance = float(a["balance"]) - float(b["balance"])
    span = [r for r in rows() if family(str(r.get("provider"))) == family(provider)
            and a["at"] <= str(r.get("at", "")) <= b["at"]]
    recorded = sum(_cost(r) for r in span if r.get("status") != "inflight")
    pending = [r["id"] for r in span if r.get("status") == "inflight"]
    unexplained = round(used_by_balance - recorded, 4)
    ok = abs(unexplained) <= max(TOLERANCE.get(unit, 0.0), 1e-6)
    if ok:
        summary = f"the balance drop ({money(used_by_balance, unit)}) matches the ledger"
    elif used_by_balance < 0:
        summary = (f"the balance went up by {money(-used_by_balance, unit)} (a top-up or the monthly reset); "
                   "take a fresh snapshot to start a new span")
    elif unexplained > 0:
        summary = f"{money(unexplained, unit)} spent outside Slate (a chat-lane generation or another tool)"
    else:
        summary = (f"the ledger counts {money(-unexplained, unit)} more than the balance dropped (estimates above "
                   "actuals, a refund, or a top-up)")
    if pending:
        summary += f"; {len(pending)} call(s) still in flight aren't counted"
    return {"provider": provider, "from": a["at"], "to": b["at"], "balance_drop": used_by_balance,
            "ledger": recorded, "unexplained": unexplained, "unit": unit, "session": session, "inflight": pending,
            "status": "PASS" if ok else "REVIEW", "summary": summary}
