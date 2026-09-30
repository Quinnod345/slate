"""`slate budget …` and `slate ledger …`: what's been spent, what's allowed, and who said yes.

    slate budget show [--month 2026-09]                    this month per mode and provider, plus each film's budget
    slate budget set --mode autonomous --provider elevenlabs --cap 25000     a person, in Terminal, only
    slate budget film --film F --provider elevenlabs --raise 2000 --approved-by Quinn --note "yes in chat"
    slate ledger list | inflight [--recover] | reconcile | snapshot --provider elevenlabs [--balance N]

Slate never buys credits, upgrades a plan or starts a licence. When a budget or a balance runs out, the work stops and
asks a person.
"""

from __future__ import annotations

import argparse
import math

from slate import cli as slate_cli
from slate import config, gates, jsonio, ledger, log, paths
from slate.cli import add_command, add_group, flag
from slate.errors import MissingPrerequisite, UsageError
from slate.jobs import resume
from slate.replay import fake_vendors

EPILOG = "Exit codes: 0 ok, 1 refused (or a reconcile needs review), 2 usage, 3 missing prerequisite, 4 spend refused."


def _fmt(amount: float | None, unit: str) -> str:
    if amount is None:
        return "no cap"
    if unit == "usd":
        return f"${amount:,.2f}"
    return f"{amount:,.0f} credits"


def _films_on_disk() -> list[dict]:
    root = paths.films()
    out = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        fj = jsonio.read(child / "film.json", default=None) if child.is_dir() else None
        if isinstance(fj, dict):
            fj.setdefault("id", child.name)
            out.append(fj)
    return out


# ---------------------------------------------------------------------------------------------------------------
# slate budget …


def budget_report(month: str | None = None, film_ref=None) -> dict:
    month = month or ledger.month_key()
    cfg = ledger.budget_config()
    all_rows = ledger.rows()
    modes = []
    for mode in ledger.MODES:
        for provider in ledger.PROVIDERS:
            cap = (cfg["monthly"].get(mode) or {}).get(provider)
            used = ledger.spent(provider, month=month, mode=mode, _rows=all_rows)
            modes.append({
                "mode": mode, "provider": provider, "unit": ledger.UNITS[provider], "cap": cap, "spent": used,
                "left": None if cap is None else max(0.0, float(cap) - used),
                "approval_required": provider in (cfg.get("approval_required", {}).get(mode) or []),
            })
    if film_ref is not None:
        film_dir = paths.film_dir(film_ref)
        films = [jsonio.read(film_dir / "film.json")]
        films[0].setdefault("id", film_dir.name)
    else:
        films = _films_on_disk()
        known = {f.get("id") for f in films}
        for fid in sorted({r.get("film") for r in all_rows if r.get("film")} - known):
            films.append({"id": fid, "note": "not in the films folder"})
    film_rows = []
    for fj in films:
        limits = ledger.film_limits(fj)
        entry = {"film": fj.get("id"), "mode": limits["mode"], "format": fj.get("format"), "providers": {}}
        for provider, info in limits["providers"].items():
            budget = info.get("budget")
            entry["providers"][provider] = {
                **info,
                "left": None if budget is None else max(0.0, budget - info["spent"]),
                "rework_limit": None if budget is None else budget * ledger.rework_factor(fj, cfg),
            }
        if fj.get("note"):
            entry["note"] = fj["note"]
        film_rows.append(entry)
    notes = []
    for item in (config.registry().get("price_notes") or []):
        expires = str(item.get("expires") or "")
        notes.append({**item, "expired": bool(expires) and expires[:10] < ledger._now()[:10]})
    return {"month": month, "replay": fake_vendors(), "monthly": modes, "films": film_rows,
            "rework_factor": cfg.get("rework_factor", 1.5), "price_notes": notes,
            "plan_defaults": {k: {kk: vv for kk, vv in v.items()} for k, v in ledger.PLAN_FORMAT_BUDGETS.items()}}


def cmd_budget_show(args: argparse.Namespace) -> int:
    film_ref = flag(args, "film")
    report = budget_report(args.month, film_ref)
    lines = [f"This month ({report['month']}, UTC), per mode:"]
    for row in report["monthly"]:
        if row["cap"] is None and not row["spent"] and not row["approval_required"]:
            continue
        spent = _fmt(row["spent"], row["unit"])
        cap = "no monthly cap" if row["cap"] is None else f"cap {_fmt(row['cap'], row['unit'])}"
        extra = "; every call needs a person's yes" if row["approval_required"] else ""
        lines.append(f"  {row['mode']:<11} {row['provider']:<11} {spent} spent, {cap}{extra}")
    if report["films"]:
        lines.append("Films:")
    for f in report["films"]:
        parts = []
        for provider, info in f["providers"].items():
            if info.get("budget") is None and not info.get("spent"):
                continue
            unit = info["unit"]
            budget = "no budget" if info.get("budget") is None else _fmt(info["budget"], unit)
            raised = f" (incl. {_fmt(info['raised'], unit)} raised)" if info.get("raised") else ""
            src = f" [{info['source']}]" if info.get("source") else ""
            parts.append(f"{provider} {_fmt(info['spent'], unit)} of {budget}{raised}{src}")
        lines.append(f"  {f['film']} ({f['mode']}{', ' + f['format'] if f.get('format') else ''}): "
                     + ("; ".join(parts) or "no budget and no spend yet"))
    lines.append(f"A film may reach {report['rework_factor']:g}× its budget only for rework; past that, a person's "
                 "yes (`slate budget film --raise`).")
    for note in report["price_notes"]:
        lines.append(f"Price note: {note.get('note')}" + (" (expired)" if note.get("expired") else ""))
    if report["replay"]:
        lines.append("Replay mode is on: nothing here spends, and replayed calls never count.")
    log.result(report, "\n".join(lines))
    return 0


def _cap_value(text: str) -> float | None:
    if text.lower() in ("none", "off", "unlimited", "no-cap"):
        return None
    try:
        value = float(text)
    except ValueError:
        raise UsageError(f"--cap takes an amount or 'none' (got {text!r}).") from None
    if math.isnan(value) or math.isinf(value):
        raise UsageError("--cap must be a finite amount.")
    return value


def cmd_budget_set(args: argparse.Namespace) -> int:
    approval = None if args.approval_required is None else args.approval_required == "yes"
    change = ledger.set_cap(args.mode, args.provider, _cap_value(args.cap), approval_required=approval)
    unit = ledger.UNITS.get(change["provider"], "credits")
    log.result(change, f"{change['mode']} {change['provider']}: monthly cap {_fmt(change['from'], unit)} → "
                       f"{_fmt(change['to'], unit)} (recorded in {paths.budget_file()})")
    return 0


def cmd_budget_film(args: argparse.Namespace) -> int:
    film_dir = slate_cli.film(args)
    fj = jsonio.read(film_dir / "film.json")
    fj.setdefault("id", film_dir.name)
    provider = args.provider
    if not provider:
        limits = ledger.film_limits(fj)["providers"]
        budgeted = [p for p, info in limits.items() if info.get("budget") is not None]
        if len(budgeted) == 1:
            provider = budgeted[0]
        else:
            raise UsageError("Which budget? Pass --provider elevenlabs or --provider higgsfield.")
    event = ledger.raise_film_budget(fj, provider, args.raise_by, approved_by=args.approved_by, note=args.note)
    after = ledger.film_limits(fj)["providers"][event["provider"]]
    out = {"raise": event, "budget": after}
    unit = after["unit"]
    log.result(out, f"Recorded {event['approved_by']}'s yes: film {event['film']}'s {event['provider']} budget is now "
                    f"{_fmt(after['budget'], unit)} ({_fmt(after['spent'], unit)} spent).")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# slate ledger …


def _row_line(row: dict) -> str:
    unit = (row.get("estimate") or {}).get("unit") or ledger.UNITS.get(str(row.get("provider")), "credits")
    est = (row.get("estimate") or {}).get("amount")
    cost = row.get("actual")
    money = (_fmt(cost, unit) if isinstance(cost, (int, float)) else f"est. {_fmt(est, unit)}") if est is not None \
        else "-"
    bits = [row["id"], str(row.get("at", ""))[:19], f"{row.get('provider')} {row.get('op')}", row.get("status", "?"),
            money]
    if row.get("film"):
        bits.append(f"film {row['film']}")
    if row.get("request_id"):
        bits.append(f"request {row['request_id']}")
    if row.get("replay"):
        bits.append("replay")
    return "  ".join(bits)


def cmd_ledger_list(args: argparse.Namespace) -> int:
    film_id = None
    if flag(args, "film"):
        fj = jsonio.read(paths.film_dir(flag(args, "film")) / "film.json", default={}) or {}
        film_id = fj.get("id") or paths.film_dir(flag(args, "film")).name
    items = ledger.rows(provider=args.provider, mode=args.mode, status=args.status, month=args.month, film=film_id)
    items.sort(key=lambda r: str(r.get("at", "")), reverse=True)
    if args.limit:
        items = items[: args.limit]
    log.result({"rows": items, "count": len(items)}, "\n".join(_row_line(r) for r in items) or "The ledger is empty.")
    return 0


def cmd_ledger_inflight(args: argparse.Namespace) -> int:
    items = ledger.inflight()
    for row in items:
        row["abandoned"] = resume.abandoned(row)
    out: dict = {"rows": items, "count": len(items)}
    lines = [(_row_line(r) + ("  ABANDONED (its process is gone)" if r["abandoned"] else "")) for r in items]
    if args.recover:
        outcomes = resume.recover()
        out["recovered"] = outcomes
        lines += [f"{o['row']}: {o['action']}" for o in outcomes] or ["nothing abandoned to recover"]
    log.result(out, "\n".join(lines) or "No paid calls in flight.")
    return 0


def cmd_ledger_reconcile(args: argparse.Namespace) -> int:
    if args.provider:
        providers = [args.provider]
    else:
        providers = sorted({s.get("provider") for s in ledger.snapshots(replay=fake_vendors())} or
                           set(ledger.PROVIDERS))
    results = [ledger.reconcile(p, session=args.session) for p in providers]
    gate_results = [gates.GateResult(f"ledger.reconcile.{r['provider']}", r["status"], r["summary"],
                                     evidence=[f"{r.get('from', '?')} → {r.get('to', '?')}"] if r.get("from") else [],
                                     engine=gates.engine("slate ledger"), details=r) for r in results]
    status = gates.overall(gate_results)
    log.result({"status": status, "results": results}, gates.format_table(gate_results))
    return 0 if status == "PASS" else 1


def cmd_ledger_snapshot(args: argparse.Namespace) -> int:
    if not args.provider and not args.all:
        raise UsageError("Pass --provider NAME (or --all for every provider Slate can read by itself).")
    if args.balance is not None and not args.provider:
        raise UsageError("--balance goes with one --provider.")
    taken, skipped = [], []
    providers = [args.provider] if args.provider else list(ledger.PROVIDERS)
    for provider in providers:
        if args.balance is not None:
            taken.append(ledger.snapshot(provider, args.balance, session=args.session, source="person",
                                         note=args.note))
            continue
        try:
            balance = resume.balance_of(provider)
        except MissingPrerequisite as exc:
            if args.provider:
                raise
            skipped.append({"provider": provider, "why": exc.message})
            continue
        taken.append(ledger.snapshot(provider, balance, session=args.session,
                                     source="replay" if fake_vendors() else "api", note=args.note))
    if not taken:
        raise MissingPrerequisite("Slate couldn't read any balance by itself.",
                                  hint="Pass one you read from the dashboard: slate ledger snapshot --provider NAME "
                                       "--balance N")
    lines = [f"{s['provider']}: {_fmt(s['balance'], s['unit'])} at {s['at']}" + (" (replay)" if s["replay"] else "")
             for s in taken] + [f"{s['provider']}: skipped ({s['why']})" for s in skipped]
    log.result({"snapshots": taken, "skipped": skipped}, "\n".join(lines))
    return 0


def register(sub) -> None:
    budget = add_group(sub, "budget", "what may be spent: monthly caps per mode, film budgets and raises",
                       description="Budgets: a hard stop per film (1.5× only for rework) and monthly caps per mode in "
                                   "Slate's own budget file. Slate never buys credits; when money runs out, the work "
                                   "stops and asks.", epilog=EPILOG)
    p = add_command(budget, "show", cmd_budget_show, "this month's spend against the caps, and each film's budget",
                    epilog=EPILOG)
    p.add_argument("--month", help="YYYY-MM (UTC); default this month")
    p = add_command(budget, "set", cmd_budget_set,
                    "change a monthly cap (only a person, in their own Terminal; agents are refused)", epilog=EPILOG)
    p.add_argument("--mode", required=True, choices=ledger.MODES)
    p.add_argument("--provider", required=True, choices=ledger.PROVIDERS)
    p.add_argument("--cap", required=True, help="an amount (credits, or USD for higgsfield), or 'none'")
    p.add_argument("--approval-required", dest="approval_required", choices=("yes", "no"),
                   help="whether every call in that mode needs a person's approval id")
    p = add_command(budget, "film", cmd_budget_film,
                    "record a person's yes to spend past a supervised film's budget (refused for autonomous films)",
                    epilog="Example: slate budget film --film my-film --provider elevenlabs --raise 2000 "
                           "--approved-by Quinn --note 'yes in chat, 14:02'\n" + EPILOG)
    p.add_argument("--raise", dest="raise_by", type=float, required=True, help="how much to add to the budget")
    p.add_argument("--approved-by", dest="approved_by", required=True, help="the person who said yes")
    p.add_argument("--provider", choices=ledger.PROVIDERS, help="which budget (needed when the film has both)")
    p.add_argument("--note", help="where and when they said it")

    led = add_group(sub, "ledger", "every paid call: list, in-flight calls, balance snapshots, reconcile",
                    description=ledger.__doc__.split("\n\n")[0], epilog=EPILOG)
    p = add_command(led, "list", cmd_ledger_list, "paid calls, newest first", epilog=EPILOG)
    p.add_argument("--provider")
    p.add_argument("--mode", choices=ledger.MODES)
    p.add_argument("--status", choices=ledger.ROW_STATUSES)
    p.add_argument("--month", help="YYYY-MM (UTC)")
    p.add_argument("--limit", type=int, default=50)
    p = add_command(led, "inflight", cmd_ledger_inflight,
                    "paid calls that started and haven't finished (abandoned ones are polled, never repaid)",
                    epilog=EPILOG)
    p.add_argument("--recover", action="store_true",
                   help="settle abandoned calls through their vendor's poll hook")
    p = add_command(led, "reconcile", cmd_ledger_reconcile,
                    "compare balance snapshots with the ledger: PASS, or REVIEW when spend happened outside Slate",
                    epilog=EPILOG)
    p.add_argument("--provider")
    p.add_argument("--session", help="compare that session's first and last snapshots")
    p = add_command(led, "snapshot", cmd_ledger_snapshot,
                    "record a provider's balance (read from the vendor when Slate can, or pass --balance)",
                    epilog="Take one at the start and the end of a session, then `slate ledger reconcile`.\n"
                           + EPILOG)
    p.add_argument("--provider")
    p.add_argument("--all", action="store_true", help="every provider Slate can read by itself")
    p.add_argument("--balance", type=float, help="the balance you read from the dashboard")
    p.add_argument("--session", help="a label tying a session's start and end snapshots together")
    p.add_argument("--note")
