"""Generating a shot's takes: estimate, approval, run, resume, cancel.

The order is always the same, and every refusal happens before anything is spent:

1. policy: the film's generation setting, draft mode, the shot's reason category (slate.gen.policy);
2. uploads: brand material only when the brand pack allows it;
3. the model: the best for the role unless the film overrides it, pinned at first use;
4. the free /estimate for the exact body, and the budget check for every take at once;
5. autonomous films: a recorded yes for exactly this request (`slate gen request-approval`, then the person's yes
   through `slate gen approve`); the ledger refuses the spend without the approval id too;
6. at least two takes, each with its own request key; the exact body and its Idempotency-Key are written beside the
   take before the POST, and each request id reaches the ledger's in-flight row and generation.json before anything
   waits on it;
7. wait, download at once, probe. A rerun polls takes still running instead of paying for them again, and a take whose
   submit died before any answer is sent again with its own Idempotency-Key, which Higgsfield answers with the
   original request (charged once) when it exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from slate import jsonio, ledger, log, replay
from slate.errors import Refused, SlateError, SpendRefused, UsageError
from slate.gen import bodies, deps, policy, store
from slate.vendors import higgsfield

WAIT_TIMEOUT_S = 1800.0


@dataclass
class Prepared:
    film_dir: Path
    film: dict
    shot: dict
    block: dict
    role: str
    model: str
    model_source: str
    takes: int
    inputs: list[tuple[str, Path]]
    template: dict
    body_sha: str
    quote: dict | None
    mode: str
    needs_approval: bool
    policy_why: str
    uploads: list[dict] = field(default_factory=list)

    @property
    def shot_id(self) -> str:
        return str(self.shot["id"])

    @property
    def per_take_usd(self) -> float | None:
        return higgsfield.quote_usd(self.quote) if self.quote else None

    @property
    def total_usd(self) -> float | None:
        per = self.per_take_usd
        return round(per * self.takes, 4) if per is not None else None


def prepare(film_dir: Path, shot_id: str, *, takes: int | None = None, client: higgsfield.Client | None = None,
            estimate: bool = True) -> Prepared:
    """Everything a run needs, checked, with nothing spent (the /estimate is free)."""
    film_dir = Path(film_dir)
    store.check_shot_id(shot_id)
    film = deps.load_film(film_dir)
    shot = deps.find_shot(film_dir, shot_id)
    ok, why = policy.allowed(film, shot)
    if not ok:
        raise Refused(why, hint="Code builds this shot; `slate plates plan` shows what may generate and why.")
    block = shot["generate"]
    deps.require_part(block, "generation", "block", what=f"{shot_id}'s generate block")
    role, model, source = bodies.resolve_model(film_dir, film, block)
    count = takes if takes is not None else int(block.get("takes") or policy.DEFAULT_TAKES)
    if count < policy.MIN_TAKES:
        raise UsageError(f"Every generated shot is judged from at least {policy.MIN_TAKES} takes; {count} asked.",
                         hint=f"slate gen run --shot {shot_id} --takes {policy.MIN_TAKES}")
    inputs = bodies.file_inputs(film_dir, film, block)
    problems = policy.check_uploads(film_dir, deps.brand_pack(film), [p for _, p in inputs])
    if problems:
        raise Refused(problems[0])
    duration = bodies.covered_seconds(film_dir, shot, block)
    template = bodies.build(role, model, block, film, urls=bodies.placeholders(inputs), duration=duration)
    uploads = bodies.uploads_block(film_dir, inputs)
    body_sha = jsonio.sha256_obj({"model": model, "body": bodies.shown(film_dir, inputs, template), "takes": count,
                                  "uploads": [(u["field"], u["sha256"]) for u in uploads]})
    quote = None
    if estimate:
        quote = (client or higgsfield.Client()).estimate(model, template)
    return Prepared(film_dir=film_dir, film=film, shot=shot, block=block, role=role, model=model,
                    model_source=source, takes=count, inputs=inputs, template=template, body_sha=body_sha,
                    quote=quote, mode=policy.mode(film), needs_approval=policy.requires_approval(film),
                    policy_why=why, uploads=uploads)


def estimate(film_dir: Path, shot_id: str, *, takes: int | None = None,
             client: higgsfield.Client | None = None) -> dict:
    """The free /estimate for a shot's takes. `estimate_usd_*` is the price (after any discount); the saving and the
    list price are shown beside it, never in its place."""
    prep = prepare(film_dir, shot_id, takes=takes, client=client)
    return {"shot": shot_id, "model": prep.model, "role": prep.role, "model_source": prep.model_source,
            "takes": prep.takes, "estimate_usd_per_take": prep.per_take_usd,
            "saving_usd_per_take": higgsfield.quote_saving_usd(prep.quote or {}),
            "list_usd_per_take": higgsfield.quote_list_usd(prep.quote or {}),
            "estimate_usd_total": prep.total_usd, "pricing": (prep.quote or {}).get("pricing_description"),
            "body": bodies.shown(film_dir, prep.inputs, prep.template), "uploads": prep.uploads,
            "needs_approval": prep.needs_approval, "replay": bool((prep.quote or {}).get("replay")),
            "lane": higgsfield.LANE}


# ---------------------------------------------------------------------------------------------------------------
# Approvals (autonomous films: every generation waits for a person's yes)


def _approval_message(prep: Prepared, approval_id: str) -> str:
    film = prep.film.get("title") or prep.film.get("id") or "the film"
    total = f"about ${prep.total_usd:.2f}" if prep.total_usd is not None else "an unknown amount"
    why = prep.block.get("why") or policy.REASONS.get(prep.block.get("reason"), "")
    return (f"Slate would like to generate {prep.takes} takes of {prep.shot_id} for {film} on {prep.model}, "
            f"{total} in all ({prep.block.get('reason')}: {why}). The prompt: \"{prep.block.get('prompt', '')}\". "
            f"Reply yes to approve {approval_id}, or no to keep the shot in code.")


def request_approval(film_dir: Path, shot_id: str, *, takes: int | None = None,
                     client: higgsfield.Client | None = None) -> dict:
    """Write what the person is asked to approve, and ask them (slate.notify: iMessage and push) when allowed."""
    prep = prepare(film_dir, shot_id, takes=takes, client=client)
    approval_id = f"appr_{uuid.uuid4().hex[:12]}"
    message = _approval_message(prep, approval_id)
    request = {"schema": 1, "id": approval_id, "film": prep.film.get("id"), "shot": shot_id,
               "reason": prep.block.get("reason"), "why": prep.block.get("why") or "",
               "prompt": prep.block.get("prompt"), "role": prep.role, "model": prep.model, "takes": prep.takes,
               "estimate_usd_per_take": prep.per_take_usd, "estimate_usd_total": prep.total_usd,
               "body_sha256": prep.body_sha, "uploads": prep.uploads, "created_at": deps.now_iso(),
               "message": message, "replay": replay.fake_vendors()}
    request["notify"] = _notify(message, title=f"Slate: approve {shot_id}?")
    deps.require_part(request, "generation", "approval_request", what="the approval request")
    path = store.approval_request_path(film_dir, shot_id)
    jsonio.write_atomic(path, jsonio.mark_generated(request, "slate gen request-approval"))
    return {**request, "file": deps.rel(film_dir, path), "needs_approval": prep.needs_approval}


def _notify(message: str, *, title: str) -> dict:
    if not deps.notify_allowed():
        return {"sent": False, "why": "replay, test or SLATE_NO_NOTIFY: nothing was sent"}
    notify = deps.notify_module()
    if notify is None:
        return {"sent": False, "why": "slate.notify isn't installed yet: send the message yourself"}
    sent: dict = {"sent": False}
    # Each channel on its own: a route that isn't set up (no iMessage handle, no push route) is reported, and never
    # stops the other. What notify says it sent is what's recorded, never assumed.
    for channel, send in (("imessage", lambda: notify.imessage(message)),
                          ("push", lambda: notify.push(title, message))):
        if not hasattr(notify, channel):
            continue
        try:
            res = send()
            ok = bool(res.get("sent", True)) if isinstance(res, dict) else True
            sent[channel] = ok
            if not ok and isinstance(res, dict) and res.get("why"):
                sent[f"{channel}_why"] = str(res["why"])[:200]
        except Exception as exc:  # noqa: BLE001 - a failed notification mustn't lose the request; it's written anyway
            sent[channel] = False
            sent[f"{channel}_error"] = str(getattr(exc, "message", exc))[:300]
    sent["sent"] = bool(sent.get("imessage") or sent.get("push"))
    if not sent["sent"]:
        sent["why"] = "nothing went out: send the message yourself (the request is written either way)"
    return sent


def _find_request(film_dir: Path, approval_id: str) -> tuple[Path, dict]:
    for path in sorted(store.plates_dir(film_dir).glob("*/approval_request.json")):
        data = jsonio.read(path, default={}) or {}
        if data.get("id") == approval_id:
            return path, data
    raise UsageError(f"No approval request called {approval_id} in this film.",
                     hint="slate gen request-approval --shot <id> writes one.")


def approve(film_dir: Path, approval_id: str, *, by: str, note: str | None = None) -> dict:
    """Record a person's yes to an approval request (the SHA-256 of the request is what's approved)."""
    if not by.strip():
        raise UsageError("Say who approved it (--by).")
    path, request = _find_request(film_dir, approval_id)
    entry = deps.approvals().record(film_dir, what=f"generation:{request['shot']}", path=deps.rel(film_dir, path),
                                    approved_by=by, note=note)
    return {"approval": approval_id, "yes": entry.get("id"), "shot": request["shot"], "approved_by": by,
            "record": entry}


def check_approval(prep: Prepared, approval_id: str | None) -> tuple[dict | None, str | None]:
    """Autonomous films: refuse (exit 4) unless a person said yes to exactly this request.

    `approval_id` is the request's id (appr_…, from `slate gen request-approval`) or the recorded yes itself (ap_…,
    from `slate gen approve` or `slate approve spend <request file>`). Returns (the request, the recorded yes's id)."""
    if not prep.needs_approval:
        return None, None
    hint = f"slate gen request-approval --shot {prep.shot_id}, then wait for the person's yes"
    if not approval_id:
        raise SpendRefused(f"{prep.shot_id} is in an autonomous film: every generation waits for a person's yes, "
                           "and this run has no approval id.", hint=hint)
    path = store.approval_request_path(prep.film_dir, prep.shot_id)
    rel = deps.rel(prep.film_dir, path)
    request = jsonio.read(path, default=None)
    if not request:
        raise SpendRefused(f"{prep.shot_id} has no approval request, so {approval_id} can't cover it.", hint=hint)
    approvals = deps.approvals()
    what = f"generation:{prep.shot_id}"
    if approval_id.startswith("appr_"):
        if request.get("id") != approval_id:
            raise SpendRefused(f"{approval_id} isn't {prep.shot_id}'s current approval request.", hint=hint)
        record = approvals.latest(prep.film_dir, what, rel)
        if record is None or not approvals.is_approved(prep.film_dir, what, rel):
            raise SpendRefused(f"{approval_id} is still waiting for a person's yes.",
                               hint=f"When the yes comes in: slate gen approve --id {approval_id} --by <who>")
        yes_id = record.get("id")
    else:
        if not approvals.spend_covers(prep.film_dir, approval_id, rel):
            raise SpendRefused(f"{approval_id} isn't a person's yes to {prep.shot_id}'s approval request.", hint=hint)
        record = approvals.spend_approval(prep.film_dir, approval_id) or {}
        if not approvals.is_approved(prep.film_dir, record.get("what") or what, rel):
            raise SpendRefused(f"{prep.shot_id}'s approval request changed after {approval_id} was given.", hint=hint)
        yes_id = approval_id
    if request.get("body_sha256") != prep.body_sha:
        raise SpendRefused(f"{prep.shot_id}'s generation changed since the person was asked (prompt, model, inputs or "
                           "takes), so that yes doesn't cover it.", hint=hint)
    approved_total = request.get("estimate_usd_total")
    if approved_total is not None and prep.total_usd is not None and prep.total_usd > approved_total * 1.01 + 1e-9:
        raise SpendRefused(f"The price went up since the yes (${approved_total:.2f} then, ${prep.total_usd:.2f} now).",
                           hint=hint)
    return request, yes_id


# ---------------------------------------------------------------------------------------------------------------
# Runs


def _new_record(prep: Prepared) -> dict:
    return {"schema": 1, "film": prep.film.get("id"), "shot": prep.shot_id, "lane": higgsfield.LANE,
            "role": prep.role, "model": prep.model, "model_source": prep.model_source,
            "policy": {"allowed": True, "why": prep.policy_why, "setting": policy.setting(prep.film),
                       "reason": prep.block.get("reason"), "mode": prep.mode},
            "block": prep.block, "body": bodies.shown(prep.film_dir, prep.inputs, prep.template),
            "body_sha256": prep.body_sha, "uploads": prep.uploads, "takes_wanted": prep.takes, "approval": None,
            "takes": [], "judge": None, "picked": None}


def _check_request_identity(prep: Prepared, rec: dict | None) -> None:
    """A generation record describes one request's takes, so a rerun cannot mix in a different request.

    The approval fingerprint includes the number of takes. That number may grow on a rerun, but the model, exact
    body and uploaded bytes must stay the same: judge and conform use the record's shared provenance for each take.
    Even --force only buys more takes of that request; it never rewrites the provenance of existing media.
    """
    if not rec or not rec.get("takes"):
        return
    current = _new_record(prep)

    def identity(record: dict) -> dict:
        return {"model": record.get("model"), "body": record.get("body"),
                "uploads": [(u.get("field"), u.get("sha256")) for u in record.get("uploads") or []]}

    if identity(rec) != identity(current):
        raise Refused(
            f"{prep.shot_id}'s generation request changed since its existing takes were submitted (model, prompt, "
            "settings or input files). These takes keep their original provenance; --force cannot mix requests.",
            hint=f"Restore the request recorded in plates/{prep.shot_id}/generation.json to resume it, or give the "
                 "revised generated shot its own id. Existing takes are kept.")


def run(film_dir: Path, shot_id: str, *, takes: int | None = None, approval: str | None = None, force: bool = False,
        client: higgsfield.Client | None = None, timeout_s: float = WAIT_TIMEOUT_S, dry_run: bool = False) -> dict:
    """Generate (or finish generating) a shot's takes. Returns a summary; generation.json has the detail."""
    film_dir = Path(film_dir)
    store.require_owned_requests(film_dir, shot_id)
    client = client or higgsfield.Client()
    prep = prepare(film_dir, shot_id, takes=takes, client=client)
    rec = store.load_record(film_dir, shot_id)
    _check_request_identity(prep, rec)
    if rec and adopt_orphans(film_dir, rec) and not dry_run:  # a dry run sees the adoption, never writes it
        store.save_record(film_dir, shot_id, rec)
    if rec and store.approved_take(rec) and not force:
        picked = store.approved_take(rec)["id"]
        raise Refused(f"{shot_id} already has an approved take ({picked}); approved plates are never regenerated "
                      "without --force.", hint=f"slate plates conform --shot {shot_id}")
    request, yes_id = check_approval(prep, approval)
    approval_id = yes_id or approval
    deps.mode_check(film_dir, "generate", approval_id=yes_id or (approval if approval and approval.startswith("ap_")
                                                                  else None))
    live = store.live_takes(rec) if rec else []
    unanswered = _unanswered(rec) if rec else []
    have = len(store.usable_takes(rec)) if (rec and not force) else len(live) + len(unanswered)
    new_count = max(0, prep.takes - have)
    per_take = prep.per_take_usd or 0.0
    if new_count:
        preflight = ledger.estimate(higgsfield.PROVIDER, "generate", amount=round(per_take * new_count, 4),
                                    basis="Higgsfield /estimate × takes", model=prep.model)
        ledger.check_budget(preflight, film=film_dir, mode=prep.mode, approval_id=approval_id)
    summary = {"shot": shot_id, "model": prep.model, "role": prep.role, "takes_wanted": prep.takes,
               "resuming": [t["id"] for t in live + unanswered], "new_takes": new_count,
               "estimate_usd_per_take": prep.per_take_usd,
               "estimate_usd_new": round(per_take * new_count, 4), "lane": higgsfield.LANE,
               "replay": replay.fake_vendors(), "dry_run": dry_run}
    if dry_run:
        return summary
    if prep.model_source in ("registry", "config", "film"):
        deps.pin(film_dir, prep.role, prep.model)  # first use wins: a registry refresh never changes this film
    rec = rec or _new_record(prep)
    if request:
        rec["approval"] = {"id": request["id"], "yes": yes_id,
                           "request": deps.rel(film_dir, store.approval_request_path(film_dir, shot_id))}
    rec["takes_wanted"] = max(prep.takes, int(rec.get("takes_wanted") or 0))
    store.save_record(film_dir, shot_id, rec)
    errors: list[SlateError] = []
    submitted: list[dict] = []
    with deps.hold("higgsfield"):
        for take in unanswered:
            try:
                _resubmit(film_dir, rec, take, client, prep, approval_id)
            except SlateError as exc:
                take.update(status="error", error=exc.message, finished_at=deps.now_iso())
                errors.append(exc)
            store.save_record(film_dir, shot_id, rec)
        live = [t for t in live + unanswered if t.get("request_id") and t.get("status") in store.LIVE_STATES]
        body = _upload_body(prep, client) if new_count and not errors else None
        for _ in range(new_count if not errors else 0):
            take = {"id": store.next_take_id(rec), "status": "submitted", "verdict": "unjudged", "request_id": None,
                    "ledger_row": None, "quote": {}, "actual_usd": None, "submitted_at": deps.now_iso(),
                    "finished_at": None, "files": [], "probe": None, "replay": replay.fake_vendors(), "error": None,
                    "verdict_by": None, "verdict_at": None, "verdict_why": None, "sha256": None}
            key = request_key_for(prep.film, shot_id, take["id"], prep.model, body)
            take.update(request_key=key, idempotency_key=higgsfield.idempotency_key(key))
            if request:
                take["approval_slot"] = approval_slot(approval_id, int(request.get("takes") or prep.takes))
            rec["takes"].append(take)
            store.save_record(film_dir, shot_id, rec)
            _write_ahead(film_dir, shot_id, take, prep.model, body)  # the exact body, before the POST
            try:
                sub = client.submit(prep.model, body, film=film_dir, mode=prep.mode,
                                    approval_id=take.get("approval_slot") or approval_id,
                                    note=_take_note(rec, take["id"]), key=key)
            except SlateError as exc:
                take.update(status="error", error=exc.message, finished_at=deps.now_iso())
                store.save_record(film_dir, shot_id, rec)
                errors.append(exc)
                break
            _took(take, sub)
            store.save_record(film_dir, shot_id, rec)  # the request id is on disk before anything waits
            submitted.append(take)
            log.info(f"{shot_id} {take['id']}: submitted {sub['request_id']}")
        for take in live + submitted:
            _await(film_dir, rec, take, client, timeout_s)
            store.save_record(film_dir, shot_id, rec)
    if errors:
        raise errors[0]
    summary["takes"] = [{k: t.get(k) for k in ("id", "status", "request_id", "actual_usd", "files", "error")}
                        for t in rec["takes"]]
    summary["completed"] = len(store.completed_takes(rec))
    summary["next"] = (f"slate gen judge --shot {shot_id}" if summary["completed"] >= policy.MIN_TAKES
                       else f"slate gen run --shot {shot_id}")
    return summary


def approval_slot(yes_id: str, takes: int) -> str:
    """The ledger approval for one take: the person's yes covers the request's N takes, and the ledger lets an
    approval id pay for one call, so each take spends under its own slot of the yes (`<yes>#1` … `#N`). A slot whose
    call failed or was cancelled (nothing billed) is free again; when every slot is used, the yes is spent."""
    used = {r.get("approval_id") for r in ledger.rows(provider=higgsfield.PROVIDER)
            if str(r.get("approval_id") or "").startswith(f"{yes_id}#") and r.get("status") not in ("failed",
                                                                                                    "cancelled")}
    for k in range(1, max(1, takes) + 1):
        slot = f"{yes_id}#{k}"
        if slot not in used:
            return slot
    raise SpendRefused(f"The yes {yes_id} covered {takes} take(s), and every one of them has been paid for.",
                       hint="Ask the person again for more takes (slate gen request-approval).")


def _take_note(rec: dict, take_id: str) -> str:
    return f"{rec.get('film')} {rec['shot']} {take_id}"


def request_key_for(film: dict, shot_id: str, take_id: str, model: str, body: dict | None) -> str:
    """A take's stable request key: this film (its id and when it was made), shot, take, model and exact body. The
    ledger row carries it and the Idempotency-Key is derived from it, so every resend of this take is the same
    request to Higgsfield, and every other take is a new one."""
    return replay.request_key(higgsfield.PROVIDER, "generate",
                              {"film": film.get("id"), "film_created": film.get("created_at"), "shot": shot_id,
                               "take": take_id, "model": model, "body": body})


def _submit_record(film_dir: Path, shot_id: str, take_id: str) -> Path:
    return store.take_dir(film_dir, shot_id, take_id) / "submit.json"


def _write_ahead(film_dir: Path, shot_id: str, take: dict, model: str, body: dict | None) -> None:
    """The exact request, written before the POST (uploaded URLs included, which generation.json never keeps), so a
    take whose submit dies unanswered can be sent again byte for byte under the same Idempotency-Key."""
    jsonio.write_atomic(_submit_record(film_dir, shot_id, take["id"]),
                        jsonio.mark_generated({"take": take["id"], "model": model, "body": body,
                                               "request_key": take["request_key"],
                                               "idempotency_key": take["idempotency_key"],
                                               "written_at": deps.now_iso()}, "slate gen run"))


def _took(take: dict, sub: dict) -> None:
    quote = sub.get("quote") or {}
    take.update(request_id=sub["request_id"], ledger_row=sub["ledger_row"], replay=bool(sub.get("replay")),
                idempotency_key=sub.get("idempotency_key") or take.get("idempotency_key"),
                quote={"usd": higgsfield.quote_usd(quote), "saving_usd": higgsfield.quote_saving_usd(quote),
                       "list_usd": higgsfield.quote_list_usd(quote)})


def _unanswered(rec: dict) -> list[dict]:
    """Takes whose submit died before any answer, that can be sent again under their own Idempotency-Key."""
    return [t for t in rec.get("takes", []) if t.get("status") == "submitted" and not t.get("request_id")
            and t.get("resend")]


def adopt_orphans(film_dir: Path, rec: dict) -> bool:
    """Takes whose submit went out but whose request id never reached generation.json (the process died in between).

    - The ledger row got the id first: the take takes it over and is polled, never paid for again.
    - No id anywhere, but the exact request was written ahead of the POST: the take is marked to be sent again under
      its own Idempotency-Key (`slate gen run` does it; Higgsfield answers with the original request if it exists).
    - Neither: nothing can be running under Slate's name, and the take is an error.
    Returns True when anything changed."""
    changed = False
    for take in rec.get("takes", []):
        if take.get("status") != "submitted" or take.get("request_id") or take.get("resend"):
            continue
        key = take.get("request_key")
        rows = [r for r in ledger.rows(provider=higgsfield.PROVIDER)
                if (key and r.get("key") == key) or r.get("note") == _take_note(rec, take["id"])]
        row = next((r for r in reversed(rows) if r.get("request_id")), None)
        if row is not None:
            take.update(request_id=row["request_id"], ledger_row=row["id"])
        elif key and _submit_record(film_dir, rec["shot"], take["id"]).is_file():
            take.update(resend=True, error="the submit died before Higgsfield answered; `slate gen run` sends it "
                                           "again with the same Idempotency-Key, so it can't be billed twice")
        else:
            take.update(status="error", finished_at=deps.now_iso(),
                        error="the submit never got a request id, so nothing is running (see slate ledger)")
        changed = True
    return changed


def _resubmit(film_dir: Path, rec: dict, take: dict, client: higgsfield.Client, prep: Prepared,
              approval_id: str | None) -> None:
    """Send an unanswered take again, exactly as written ahead of its first POST, under the same key."""
    sent = jsonio.read(_submit_record(film_dir, rec["shot"], take["id"]))
    if sent.get("request_key") != take.get("request_key"):
        raise Refused(f"{rec['shot']} {take['id']}: its write-ahead record doesn't match the take, so it isn't sent "
                      "again.", hint="Check the Higgsfield console for the request, then run a new take.")
    sub = client.submit(sent["model"], sent["body"], film=film_dir, mode=prep.mode,
                        approval_id=take.get("approval_slot") or approval_id, note=_take_note(rec, take["id"]),
                        key=take["request_key"])
    take.pop("resend", None)
    take["error"] = None
    _took(take, sub)
    log.info(f"{rec['shot']} {take['id']}: sent again with its Idempotency-Key; request {sub['request_id']}")


def _upload_body(prep: Prepared, client: higgsfield.Client) -> dict:
    urls: dict[str, str | list[str]] = {}
    for key, path in prep.inputs:
        url = client.upload(path)
        if bodies.FILE_FIELDS[key][1]:
            urls.setdefault(key, [])
            urls[key].append(url)  # type: ignore[union-attr]
        else:
            urls[key] = url
    duration = bodies.covered_seconds(prep.film_dir, prep.shot, prep.block)
    return bodies.build(prep.role, prep.model, prep.block, prep.film, urls=urls, duration=duration)


def _await(film_dir: Path, rec: dict, take: dict, client: higgsfield.Client, timeout_s: float) -> None:
    """Wait for one take, then download and probe it. A timeout leaves it running (and says so)."""
    try:
        final = client.wait(take["request_id"], timeout_s=timeout_s)
    except SlateError as exc:
        take["error"] = f"{exc.message} (still on Higgsfield: rerun `slate gen run` to poll it; it's never paid twice)"
        return
    _apply_final(film_dir, rec, take, final, client)


def _apply_final(film_dir: Path, rec: dict, take: dict, final: dict, client: higgsfield.Client) -> None:
    state = "canceled" if final.get("status") == "cancelled" else final.get("status")
    take["status"] = state if state in ("completed", "failed", "nsfw", "canceled", "timeout", "queued",
                                        "in_progress") else "error"
    if state == "timeout":
        take["error"] = "still running on Higgsfield after the wait; poll with slate gen takes --refresh"
        return
    take["finished_at"] = deps.now_iso()
    row = next((r for r in ledger.rows(id=take.get("ledger_row"))), None) if take.get("ledger_row") else None
    if row is not None and row.get("status") != "inflight":
        take["actual_usd"] = row.get("actual")
    if state != "completed":
        take["error"] = final.get("error") or f"Higgsfield finished it as {state} (not billed)"
        take["actual_usd"] = 0.0
        return
    folder = store.take_dir(film_dir, rec["shot"], take["id"])
    files = []
    for i, url in enumerate(higgsfield.outputs(final)):
        try:
            files.append(client.download(url, folder, f"{take['request_id']}_{i}"))
        except SlateError as exc:
            take["error"] = f"download failed ({exc.message}); retry with slate gen takes --refresh"
    take["files"] = [deps.rel(film_dir, f) for f in files]
    ahead = _submit_record(film_dir, rec["shot"], take["id"])
    for f in files:  # made by the request written ahead of the POST (the exact body)
        deps.record_made(film_dir, deps.rel(film_dir, f), producer="slate gen run",
                         inputs={deps.rel(film_dir, ahead): None} if ahead.is_file() else {})
    if files:
        take["probe"] = _probe(files[0])


def _probe(path: Path) -> dict:
    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as im:
            return {"kind": "image", "width": im.size[0], "height": im.size[1]}
    from slate.capture import media  # noqa: PLC0415

    info = media.probe(path)
    return {"kind": "video", **{k: info.get(k) for k in ("width", "height", "fps", "fps_float", "duration", "frames",
                                                        "codec", "audio", "vfr")}}


def takes(film_dir: Path, shot_id: str, *, refresh: bool = False, client: higgsfield.Client | None = None) -> dict:
    """The shot's takes; with refresh, poll the ones still running once and fetch any missing downloads."""
    rec = store.require_record(film_dir, shot_id)
    if refresh:
        store.require_owned_requests(film_dir, shot_id)
        adopt_orphans(film_dir, rec)
        client = client or higgsfield.Client()
        for take in rec["takes"]:
            if take.get("status") in store.LIVE_STATES and take.get("request_id"):
                answer = client.status(take["request_id"])
                if answer.get("status") in higgsfield.TERMINAL:
                    client._close_ledger(take["request_id"], answer)  # noqa: SLF001 - the same close wait() does
                    _apply_final(film_dir, rec, take, answer, client)
                else:
                    if answer.get("status") in store.LIVE_STATES:
                        take["status"] = answer["status"]
            elif take.get("status") == "completed" and not take.get("files") and take.get("request_id"):
                _apply_final(film_dir, rec, take, client.status(take["request_id"]), client)
        store.save_record(film_dir, shot_id, rec)
    return rec


def cancel(film_dir: Path, *, shot_id: str | None = None, take_id: str | None = None, request_id: str | None = None,
           client: higgsfield.Client | None = None) -> dict:
    """Cancel running takes (Higgsfield only cancels queued requests). Stopping a wait never cancels anything."""
    client = client or higgsfield.Client()
    results = []
    if request_id and not shot_id:
        return {"results": [client.cancel(request_id)]}
    if not shot_id:
        raise UsageError("Say what to cancel: --shot (and --take), or --request.")
    store.require_owned_requests(film_dir, shot_id)
    rec = store.require_record(film_dir, shot_id)
    targets = [store.find_take(rec, take_id)] if take_id else store.live_takes(rec)
    if request_id:
        targets = [t for t in rec["takes"] if t.get("request_id") == request_id]
    if not targets:
        return {"shot": shot_id, "results": [], "summary": f"{shot_id} has nothing running."}
    for take in targets:
        if not take.get("request_id"):
            continue
        answer = client.cancel(take["request_id"])
        if answer.get("canceled"):
            take.update(status="canceled", finished_at=deps.now_iso(), actual_usd=0.0,
                        error="canceled before it ran (not billed)")
        results.append({"take": take["id"], **answer})
    store.save_record(film_dir, shot_id, rec)
    return {"shot": shot_id, "results": results}
