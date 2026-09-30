"""The Higgsfield Cloud API: the one lane Slate generates through.

Generalized from the films' `pipeline/tools/hf_api.py`, with every lesson it learned kept:

- **One lane.** Slate generates only on the Cloud API (`api.higgsfield.ai`), paid from the Cloud API balance Quinn
  keeps funded. Higgsfield's CLI and app credits are a separate billing pool that isn't funded, so Slate never
  generates through them (decision 6). The hosted connector's `sync_agents`, TikTok publishing and website publishing
  tools are never called either; `DENIED_CONNECTOR_TOOLS` names them so agent configs can deny them.
- **Auth** is `Authorization: Key <id:secret>` from `slate.secrets` (env `HF_KEY`, then the Keychain item
  `higgsfield-cloud-api`). The key only ever travels in that header: never in a URL, a log, a record or an error.
- **A plain User-Agent** (slate.http sets it): Cloudflare in front of the API bans urllib's default with error 1010.
- **Estimate first.** `/estimate/<model>` is free, and `submit` always has a quote for the exact body before it
  posts. In the answer, `usd` is the price (after any discount) and `discount.usd` is the saving, not the price (the
  films' notes read it the other way round). The price is what the ledger books and what a finished job costs.
- **Submits are idempotent.** Every generation submit carries an `Idempotency-Key` derived from its ledger row's
  stable request key: the same key and body return the original request and charge once. So a submit that times out,
  loses its connection or meets a 5xx is sent again with the same key and body (a few times, with backoff), and a
  rerun after a crash sends the same key too. Anything sent without a key is never retried: a blind retry can create,
  and bill, a duplicate job.
- **Every submit is ledger-wrapped.** The budget check and an in-flight ledger row come before the POST, and the
  request id is written to that row before anything waits on it, so a restart polls the request instead of paying
  for a second one. A restarted submit finds its own abandoned row by the request key (`slate.jobs.resume`) and
  takes it over: it waits on the request the row already has, or sends the same key again. `wait` and `cancel` close
  the row.
- **Wait with backoff** (2 s growing to 10 s, with jitter). Stopping the wait doesn't stop the job: a timed-out wait
  leaves it running on purpose, so its result can still be fetched; `cancel` stops it (only while it's queued).
- **Uploads** go through a presigned URL: POST for the URL, PUT the file with exactly the returned headers and no API
  key, then use `public_url`. The signed URL is never logged or stored.
- **Probe** costs nothing: a status request for a request id that can't exist answers 404 when the key is accepted
  and 401 when it isn't.
- **Replay.** With `SLATE_FAKE_VENDORS=1` every call is answered from `slate.replay`: a recorded fixture, or a faker
  that quotes from registry.json and returns a flat test frame (image models) or a tiny test clip (video models).
  Nothing reaches the network and nothing is spent; everything a fake touches is marked `replay`.

Docs: https://docs.higgsfield.ai/docs/llms.txt (lifecycle, polling, uploads, billing and retention).
"""

from __future__ import annotations

import datetime as _dt
import json
import mimetypes
import os
import random
import re
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from slate import config, jsonio, ledger, log, replay, secrets
from slate.errors import Busy, MissingPrerequisite, SlateError, UsageError

PROVIDER = "higgsfield"
LANE = "cloud-api"
BASE = "https://api.higgsfield.ai"
TERMINAL = frozenset({"completed", "failed", "nsfw", "canceled", "cancelled"})  # the SDK spells it "Cancelled"
UNBILLED = frozenset({"failed", "nsfw", "canceled", "cancelled"})  # only successful generations are billed
PROBE_REQUEST_ID = "00000000-0000-4000-8000-000000000000"  # a well-formed id that can never exist
IDEMPOTENCY_HEADER = "Idempotency-Key"
SUBMIT_TRIES = 3  # with the same Idempotency-Key: a repeat returns the original request and charges once
AMBIGUOUS_STATUSES = frozenset({408, 500, 502, 503, 504})  # the submit may have landed: resend with its key
REFUSED_FOR_NOW = frozenset({425, 429})  # not taken this time (nothing created): resend with its key, then wait
RETRY_STATUSES = AMBIGUOUS_STATUSES | REFUSED_FOR_NOW
UPLOAD_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "audio/wav", "audio/x-wav", "audio/vnd.wave", "video/mp4",
}
# The hosted connector's tools Slate must never call, for agent configs that deny them (autonomous mode).
DENIED_CONNECTOR_TOOLS = ("sync_agents",  # denied: it uploads a user's skills and a personality profile
                          "tiktok_connect", "tiktok_reconnect", "tiktok_prepare_publish", "tiktok_publish_status",
                          "create_website", "deploy_website", "publish_website")  # denied: never called

REPLAY_SCHEME = "replay://"
_FAKE_ASPECTS = {"16:9": (256, 144), "9:16": (144, 256), "1:1": (192, 192), "4:5": (176, 220), "4:3": (224, 168),
                 "3:4": (168, 224), "21:9": (294, 126)}
_FAKE_FPS = 24

Transport = Callable[..., tuple[int, dict, bytes]]


class HiggsfieldError(SlateError):
    """The API answered with an error, or couldn't be reached. `status` is the HTTP code when there was one."""

    kind = "higgsfield_error"

    def __init__(self, message: str, *, status: int | None = None, hint: str | None = None,
                 details: dict | None = None):
        super().__init__(message, hint=hint, details={**(details or {}), "status": status})
        self.status = status


class HiggsfieldBusy(HiggsfieldError, Busy):
    """A rate limit or the account's concurrency limit: nothing was created or billed, so trying again later is safe.
    Exit 5 (busy), which the job queue retries later instead of failing."""

    kind = "busy"


def _busy(op: str, status: int, detail: str) -> HiggsfieldBusy:
    what = "the account's limit on requests running at once" if status == 400 else f"a rate limit (HTTP {status})"
    return HiggsfieldBusy(f"Higgsfield turned the {op} away: {what}. Nothing was created or billed.", status=status,
                          hint="Wait for a running request to finish, then run it again (a queued job retries by "
                               "itself).", details={"detail": detail[:300]})


# ---------------------------------------------------------------------------------------------------------------
# Transports: how a request reaches the API. Tests inject their own to check exactly what is sent.


def http_transport(method: str, url: str, *, headers: dict, json_body: Any = None, data: bytes | None = None,
                   timeout: float = 60) -> tuple[int, dict, bytes]:
    """slate.http underneath: GETs retry on rate limits and server errors, anything that can bill never retries.

    Non-2xx answers come back as a status (the probe needs 404 and 401 as answers, not exceptions)."""
    from slate import http  # noqa: PLC0415 (keeps `import slate.vendors.higgsfield` cheap)

    try:
        return http.request(method, url, headers=headers, json_body=json_body, data=data, timeout=timeout)
    except http.HttpError as exc:
        return exc.status, {}, (exc.body or "").encode("utf-8")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _json_or_detail(body: bytes) -> dict:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {"data": parsed}
    except json.JSONDecodeError:
        return {"detail": secrets.redact(body.decode("utf-8", "replace"))[:500]}


def quote_usd(quote: dict) -> float | None:
    """The price in a /estimate answer: what the job costs, after any discount. Budgets and the ledger book this."""
    value = quote.get("usd")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def quote_saving_usd(quote: dict) -> float | None:
    """What a discount saves (`discount.usd`). It's the saving, never the price."""
    value = (quote.get("discount") or {}).get("usd")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def quote_list_usd(quote: dict) -> float | None:
    """The price before any discount: the price plus the saving."""
    usd = quote_usd(quote)
    if usd is None:
        return None
    return round(usd + (quote_saving_usd(quote) or 0.0), 6)


def idempotency_key(request_key: str) -> str:
    """The Idempotency-Key for a submit, derived from its ledger row's stable request key: 1-255 visible ASCII
    characters, no whitespace, the same for every retry of the same request and different for every other one."""
    if not request_key:
        raise UsageError("A submit needs a request key to derive its Idempotency-Key from.")
    return f"slate-{jsonio.sha256_bytes(request_key)[:40]}"


def model_kind(path: str, body: dict | None = None) -> str:
    """'video' or 'image', from the model path (and the body, for models whose path doesn't say)."""
    p = path.lower()
    if any(k in p for k in ("video", "i2v", "t2v", "extend", "first-last-frame", "motion")):
        return "video"
    if body and any(k in body for k in ("duration", "video_url")):
        return "video"
    return "image"


def outputs(result: dict) -> list[str]:
    """Every output URL in a finished request (images[], audios[], video, audio), in order, without duplicates."""
    urls: list[str] = []
    for key in ("images", "audios", "videos"):
        urls += [x["url"] for x in result.get(key) or [] if isinstance(x, dict) and x.get("url")]
    for key in ("video", "audio", "image"):
        if isinstance(result.get(key), dict) and result[key].get("url"):
            urls.append(result[key]["url"])
    return list(dict.fromkeys(urls))


class Client:
    """The Cloud API. Replay-aware: with SLATE_FAKE_VENDORS=1 (and no --live) nothing leaves this Mac.

    `transport` replaces the HTTP layer (tests use it to see exactly what is sent, in replay mode too).
    `key` is for tests only; normally the key comes from slate.secrets at the moment a header needs it.
    """

    def __init__(self, *, transport: Transport | None = None, key: str | None = None, base: str = BASE,
                 fake: bool | None = None, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        self._transport = transport
        self._key = key
        self.base = base.rstrip("/")
        self.fake = replay.fake_vendors() if fake is None else bool(fake)
        self._sleep = sleep
        self._monotonic = monotonic
        self._quotes: dict[str, dict] = {}
        self._submits = 0

    # -- plumbing ---------------------------------------------------------------------------------------------

    @property
    def lane(self) -> str:
        return LANE

    def _api_key(self) -> str:
        return self._key or secrets.get(PROVIDER)

    def _auth(self) -> dict:
        return {"Authorization": f"Key {self._api_key()}"}

    def _url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def _send(self, op: str, method: str, path: str, *, body: Any = None, auth: bool = True,
              replay_request: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        """One request. In replay mode (and no injected transport) the answer comes from slate.replay."""
        if self.fake and self._transport is None:
            request = replay_request if replay_request is not None else {"path": path, "body": body}
            if op == "submit" and replay.load(PROVIDER, replay.request_key(PROVIDER, op, request)) is None:
                idem = (headers or {}).get(IDEMPOTENCY_HEADER)
                if idem:
                    # Like the API: the same key and body answer with the same request.
                    request = {**request, "idempotency_key": idem}
                else:
                    # Two real submits of one body are two jobs with two ids; so are two replayed ones.
                    self._submits += 1
                    request = {**request, "submit": self._submits}
            return 200, replay.respond(PROVIDER, op, request)
        transport = self._transport or http_transport
        sent_headers = {**(self._auth() if auth else {}), **(headers or {})}
        status, _, raw = transport(method, self._url(path), headers=sent_headers, json_body=body)
        answer = _json_or_detail(raw)
        if replay.recording() and op in ("estimate", "submit", "status", "cancel") and 200 <= status < 300:
            replay.record(PROVIDER, replay.request_key(PROVIDER, op, replay_request or {"path": path, "body": body}),
                          answer)
        return status, answer

    @staticmethod
    def _check(op: str, status: int, answer: dict, *, hint: str | None = None) -> dict:
        if 200 <= status < 300:
            return answer
        detail = secrets.redact(json.dumps(answer, ensure_ascii=False)[:600])
        if status == 401:
            raise HiggsfieldError(f"Higgsfield rejected the key on {op} (HTTP 401).", status=status,
                                  hint="Check the key with `slate gen probe`; `slate connect higgsfield --api-key` "
                                       "replaces it (run it in your own Terminal).")
        if (status == 400 and "concurren" in detail.lower()) or status in REFUSED_FOR_NOW:
            raise _busy(op, status, detail)
        raise HiggsfieldError(f"Higgsfield answered HTTP {status} on {op}: {detail}", status=status, hint=hint)

    # -- the interface (docs/ARCHITECTURE.md §12) ---------------------------------------------------------------

    def probe(self) -> dict:
        """Zero-cost check of the key: a status request for an id that can't exist. 404 = key accepted, 401 = not."""
        if self.fake and self._transport is None:
            answer = replay.respond(PROVIDER, "probe", {"probe": True}, key="probe")
            http_status = int(answer.get("http", 404))
            return self._probe_verdict(http_status, replayed=True)
        try:
            self._api_key()
        except secrets.MissingSecret as exc:
            return {"ok": False, "status": "no_key", "http": None, "lane": LANE,
                    "summary": "No Higgsfield Cloud API key on this Mac.", "hint": exc.hint}
        try:
            status, _ = self._send("probe", "GET", f"requests/{PROBE_REQUEST_ID}/status")
        except SlateError as exc:
            return {"ok": False, "status": "unreachable", "http": None, "lane": LANE,
                    "summary": f"Couldn't reach {urllib.parse.urlparse(self.base).netloc}: {exc.message}"}
        return self._probe_verdict(status, replayed=False)

    @staticmethod
    def _probe_verdict(http_status: int, *, replayed: bool) -> dict:
        if http_status == 404:
            verdict = {"ok": True, "status": "ok", "summary": "key accepted (404 for a request that can't exist)"}
        elif http_status in (401, 403):
            verdict = {"ok": False, "status": "rejected", "summary": f"key rejected (HTTP {http_status})",
                       "hint": "Run `slate connect higgsfield --api-key` in your own Terminal to replace it."}
        else:
            verdict = {"ok": False, "status": "unexpected", "summary": f"unexpected HTTP {http_status} from the probe"}
        return {**verdict, "http": http_status, "lane": LANE, "replay": replayed}

    def estimate(self, path: str, body: dict) -> dict:
        """The free /estimate for exactly this body. Returns the quote ({usd, discount, pricing_description, …})."""
        _check_model_path(path)
        status, answer = self._send("estimate", "POST", f"estimate/{path}", body=body,
                                    replay_request={"path": path, "body": body})
        quote = self._check("estimate", status, answer)
        self._quotes[_body_key(path, body)] = quote
        return quote

    def submit(self, path: str, body: dict, *, film: Any = None, mode: str = "supervised",
               approval_id: str | None = None, note: str | None = None, rework: bool = False,
               key: str | None = None, idempotent: bool = True) -> dict:
        """Estimate (if this exact body has no quote yet), check the budget, write the in-flight ledger row, POST.

        `key` is the request's stable identity (a take of a shot, say: `request_key_for`); the ledger row carries it
        and the Idempotency-Key is derived from it, so a rerun of the same request can never be billed twice. Without
        one, this call gets a fresh key: its own retries are safe, but a rerun is a new request. `idempotent=False`
        sends no key, and then nothing is ever retried.

        Returns {request_id, status, ledger_row, quote, idempotency_key, resumed}. The request id is on the ledger
        row before this returns, so whatever waits next can die without the spend being lost or repeated.
        """
        _check_model_path(path)
        request_key = key or f"generate-{os.urandom(8).hex()}"
        idem = idempotency_key(request_key) if idempotent else None
        adopted = self._abandoned_row(request_key)
        if adopted is not None and adopted.get("request_id"):
            ledger.adopt(adopted["id"])
            log.info(f"{path}: request {adopted['request_id']} was already sent and paid for; waiting on it instead "
                     "of sending it again")
            return {"request_id": str(adopted["request_id"]), "status": "resumed", "ledger_row": adopted["id"],
                    "quote": (adopted.get("estimate") or {}).get("details", {}).get("quote") or {},
                    "replay": bool(adopted.get("replay")), "lane": LANE, "idempotency_key": idem,
                    "request_key": request_key, "resumed": True}
        quote = self._quotes.get(_body_key(path, body)) or self.estimate(path, body)
        if adopted is not None:
            if not idem:
                ledger.orphan(adopted["id"], "an earlier submit died before Higgsfield answered, and this one sends "
                                             "no Idempotency-Key, so Slate can't tell whether it billed")
                adopted = None
            else:
                ledger.adopt(adopted["id"])  # the same key: Higgsfield answers with the original request if it exists
                log.info(f"{path}: an earlier submit died before Higgsfield answered; sending it again with the same "
                         "Idempotency-Key, so it can't be billed twice")
        est = ledger_estimate(path, body, quote, idempotency_key=idem)
        row = adopted["id"] if adopted is not None else ledger.begin(
            est, film=film, mode=mode, approval_id=approval_id, rework=rework, note=note or f"higgsfield {path}",
            key=request_key)
        status, answer = self._post_submit(path, body, row=row, est=est, idem=idem)
        request_id = str(answer["request_id"])
        ledger.request(row, request_id)
        return {"request_id": request_id, "status": answer.get("status", "queued"), "ledger_row": row,
                "quote": quote, "replay": bool(answer.get("replay")), "lane": LANE, "idempotency_key": idem,
                "request_key": request_key, "resumed": adopted is not None}

    def _post_submit(self, path: str, body: dict, *, row: str, est: ledger.Estimate,
                     idem: str | None) -> tuple[int, dict]:
        """POST the submit: with an Idempotency-Key, resend the same key and body when the answer was lost; without
        one, exactly once. Closes the ledger row itself on anything but a request id."""
        headers = {IDEMPOTENCY_HEADER: idem} if idem else {}
        tries = SUBMIT_TRIES if idem else 1
        delay = 2.0
        status, answer, lost, attempts = 0, {}, None, 0
        maybe_created = None  # the first answer that left it open whether a job exists (and bills)
        for attempt in range(tries):
            attempts += 1
            lost = None
            try:
                status, answer = self._send("submit", "POST", path, body=body, headers=headers,
                                            replay_request={"path": path, "body": body})
            except SlateError as exc:  # no answer at all: the job may exist, and bill
                lost = exc
                status, answer = 0, {}
            ok = 200 <= status < 300 and answer.get("request_id")
            if not ok and (lost is not None or status in AMBIGUOUS_STATUSES) and maybe_created is None:
                maybe_created = lost.message if lost is not None else f"HTTP {status}"
            if ok or (lost is None and status not in RETRY_STATUSES):
                break
            if attempt + 1 < tries:
                why = lost.message if lost is not None else f"HTTP {status}"
                log.warn(f"{path}: the submit didn't come back ({why}); sending it again with the same "
                         "Idempotency-Key, which Higgsfield answers with the original request")
                self._sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 2, 10.0)
        if 200 <= status < 300 and answer.get("request_id"):
            return status, answer
        if maybe_created is not None:
            # Ambiguous: some try may have created the job, and it would bill. Count the price so budgets err the safe
            # way, and say how to finish it without paying twice.
            why = maybe_created if lost is None and status not in AMBIGUOUS_STATUSES else (
                lost.message if lost is not None else f"HTTP {status}")
            tries = attempts
            ledger.finish(row, status="failed", actual=est.amount,
                          result={"error": why, "ambiguous": True, "lane": LANE, "idempotency_key": idem,
                                  "tries": tries, "counted": "the quoted price, in case the job was created"})
            if idem:
                hint = ("It's safe to run it again: the retry sends the same Idempotency-Key, and Higgsfield answers "
                        "with the original request instead of billing a second one.")
            else:
                hint = ("This submit had no Idempotency-Key, so Slate never retries it. Check the Higgsfield console "
                        "for a new request before trying again, so it isn't paid for twice.")
            raise HiggsfieldError(f"The submit to {path} didn't get an answer after {tries} tr"
                                  f"{'y' if tries == 1 else 'ies'} ({why}). It may or may not have created a job.",
                                  status=status or None, hint=hint)
        ledger.finish(row, status="failed", actual=0.0, result={"http": status, "lane": LANE,
                                                                "idempotency_key": idem})
        detail = secrets.redact(json.dumps(answer, ensure_ascii=False)[:600])
        if status == 402 or (status == 403 and any(w in detail.lower() for w in ("credit", "balance", "insufficient",
                                                                                  "funds"))):
            raise ledger.out_of_balance(PROVIDER, f"HTTP {status}: {detail[:200]}")
        if status == 422 and idem and "idempot" in json.dumps(answer).lower():
            raise HiggsfieldError(
                f"Higgsfield already has a request under this Idempotency-Key with a different body ({path}).",
                status=status, hint="The request changed since it was first sent. Nothing new was billed; check the "
                                    "Higgsfield console for the original, then run it as a new take.")
        self._check("submit", status, answer if not (200 <= status < 300) else {"detail": "no request_id"})
        raise HiggsfieldError(f"Higgsfield's answer to the submit had no request id ({path}).", status=status)

    @staticmethod
    def _abandoned_row(request_key: str) -> dict | None:
        """This request's in-flight ledger row whose process died (slate.jobs.resume), if there is one."""
        try:
            import importlib  # noqa: PLC0415

            resume = importlib.import_module("slate.jobs.resume")
        except ModuleNotFoundError as exc:
            if str(exc.name or "").startswith("slate.jobs"):
                return None
            raise
        rows = resume.pending(key=request_key, provider=PROVIDER)
        return rows[-1] if rows else None

    def status(self, request_id: str) -> dict:
        """One status poll (GET, safe to retry)."""
        _check_request_id(request_id)
        status, answer = self._send("status", "GET", f"requests/{request_id}/status",
                                    replay_request={"request_id": request_id})
        return self._check("status", status, answer)

    def wait(self, request_id: str, *, timeout_s: float = 1800.0, first_delay: float = 2.0, max_delay: float = 10.0,
             on_poll: Callable[[dict], None] | None = None) -> dict:
        """Poll to a terminal state (completed, failed, nsfw, canceled), backing off from 2 s to 10 s with jitter.

        On a terminal state the request's in-flight ledger row is closed (completed: done at the quoted price;
        failed or moderated: not billed). A timeout returns {"status": "timeout"} and leaves both the job and its
        ledger row open on purpose: fetch it later with another wait, or stop it with `cancel`.
        """
        _check_request_id(request_id)
        started = self._monotonic()
        delay = first_delay
        while True:
            answer = self.status(request_id)
            if on_poll:
                on_poll(answer)
            state = answer.get("status")
            if state in TERMINAL:
                self._close_ledger(request_id, answer)
                return answer
            if self._monotonic() - started > timeout_s:
                return {"status": "timeout", "request_id": request_id, "last": answer,
                        "note": "still running on Higgsfield: wait again later, or stop it with slate gen cancel"}
            self._sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 1.5, max_delay)

    def cancel(self, request_id: str) -> dict:
        """Ask Higgsfield to cancel (it only works while the request is queued). Stopping a wait never cancels."""
        _check_request_id(request_id)
        status, answer = self._send("cancel", "POST", f"requests/{request_id}/cancel",
                                    replay_request={"request_id": request_id})
        result = {"request_id": request_id, "http": status, **{k: v for k, v in answer.items() if k != "request_id"}}
        if 200 <= status < 300:
            self._close_ledger(request_id, {"status": "canceled"})
            result["canceled"] = True
        else:
            result["canceled"] = False
            result["summary"] = (f"Higgsfield answered HTTP {status}: it only cancels queued requests, so this one "
                                 "may already be running or finished.")
        return result

    def upload(self, path: str | os.PathLike) -> str:
        """Upload a local file for use as a reference; returns its public_url. The signed PUT URL is never kept."""
        file = Path(path)
        if not file.is_file():
            raise UsageError(f"Can't upload {file}: no such file.")
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if ctype not in UPLOAD_TYPES:
            raise UsageError(f"Higgsfield takes jpeg, png, webp, gif, wav and mp4 uploads; {file.name} is {ctype}.")
        digest = jsonio.sha256(file)
        status, answer = self._send("upload_url", "POST", "files/generate-upload-url", body={"content_type": ctype},
                                    replay_request={"content_type": ctype, "sha256": digest})
        slot = self._check("upload", status, answer)
        if not slot.get("public_url") or not slot.get("upload_url"):
            raise HiggsfieldError("Higgsfield's upload answer had no URLs.", status=status)
        if self.fake and self._transport is None:
            return str(slot["public_url"])
        put_headers = dict(slot.get("upload_headers") or {"Content-Type": ctype})
        transport = self._transport or http_transport
        put_status, _, _ = transport("PUT", str(slot["upload_url"]), headers=put_headers, data=file.read_bytes())
        if put_status not in (200, 201, 204):
            raise HiggsfieldError(f"The upload of {file.name} failed (HTTP {put_status}); its URL wasn't used.",
                                  status=put_status)
        return str(slot["public_url"])

    # -- helpers built on the interface -------------------------------------------------------------------------

    def outputs(self, result: dict) -> list[str]:
        return outputs(result)

    def download(self, url: str, dest_dir: str | os.PathLike, stem: str) -> Path:
        """Fetch an output at once (Higgsfield keeps them about 7 days). Replay URLs become local test media."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        if url.startswith(REPLAY_SCHEME):
            return fake_media(url, dest, stem)
        from slate import http  # noqa: PLC0415

        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".bin"
        return http.download(url, dest / f"{stem}{ext}")

    def generate(self, path: str, body: dict, *, out_dir: str | os.PathLike, stem: str, film: Any = None,
                 mode: str = "supervised", approval_id: str | None = None, note: str | None = None,
                 on_submitted: Callable[[dict], None] | None = None, timeout_s: float = 1800.0,
                 key: str | None = None) -> dict:
        """Estimate → budget check → in-flight row → submit → (record) → wait → download, as one call."""
        sub = self.submit(path, body, film=film, mode=mode, approval_id=approval_id, note=note, key=key)
        if on_submitted:
            on_submitted(sub)
        final = self.wait(sub["request_id"], timeout_s=timeout_s)
        files: list[str] = []
        if final.get("status") == "completed":
            files = [str(self.download(u, out_dir, f"{stem}_{i}")) for i, u in enumerate(outputs(final))]
        return {**sub, "final": final, "files": files}

    def _close_ledger(self, request_id: str, answer: dict) -> None:
        """Finish the in-flight ledger row(s) for this request, with the cost the request actually carries."""
        state = answer.get("status")
        for row in ledger.rows(provider=PROVIDER, status="inflight"):
            if row.get("request_id") != request_id:
                continue
            est = row.get("estimate") or {}
            if state == "completed":
                ledger.finish(row["id"], status="done", actual=float(est.get("amount") or 0.0),
                              result={"status": state, "actual_basis": "the quoted price (/estimate's usd, after "
                                                                        "any discount; the API has no balance "
                                                                        "endpoint)", "lane": LANE})
            elif state in ("canceled", "cancelled"):
                ledger.finish(row["id"], status="cancelled", actual=0.0, result={"status": state, "lane": LANE})
            elif state in UNBILLED:
                ledger.finish(row["id"], status="failed", actual=0.0, result={"status": state, "lane": LANE})


def ledger_estimate(path: str, body: dict, quote: dict, *, idempotency_key: str | None = None) -> ledger.Estimate:
    """The ledger's view of a quote: the price (usd, after any discount) books against budgets; the saving and the
    list price ride along, never in its place."""
    usd = quote_usd(quote)
    if usd is None:
        info = config.model_info(path)
        if not info.get("price"):
            raise HiggsfieldError(f"Higgsfield's /estimate for {path} had no price, and registry.json has none.",
                                  hint="Nothing was submitted. Check the model path, or add its price to "
                                       "registry.json.")
        seconds = body.get("duration")
        return ledger.estimate(PROVIDER, "generate", model=path,
                               seconds=float(seconds) if isinstance(seconds, (int, float)) else None)
    return ledger.estimate(PROVIDER, "generate", amount=usd, basis="Higgsfield /estimate (free)", model=path,
                           saving_usd=quote_saving_usd(quote), list_usd=quote_list_usd(quote), lane=LANE,
                           pricing=quote.get("pricing_description"), replay=bool(quote.get("replay")),
                           idempotency_key=idempotency_key,
                           quote={k: quote.get(k) for k in ("usd", "discount", "credits", "pricing_description")
                                  if k in quote})


def _body_key(path: str, body: dict) -> str:
    return jsonio.sha256_obj({"path": path, "body": body})


_MODEL_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9._-]+)+$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,127}$")


def _check_model_path(path: str) -> None:
    if not _MODEL_PATH.match(path or "") or path.startswith(("requests/", "files/", "estimate/")):
        raise UsageError(f"{path!r} isn't a Higgsfield model path (like 'bytedance/seedance-2.5/image-to-video').")


def _check_request_id(request_id: str) -> None:
    if not _REQUEST_ID.match(request_id or ""):
        raise UsageError(f"{request_id!r} isn't a Higgsfield request id.")


# ---------------------------------------------------------------------------------------------------------------
# Replay: deterministic stand-ins, so tests, evals and `slate demo` run the whole generation path for free.


def _fake_estimate(key: str, request: dict) -> dict:
    path, body = request.get("path", ""), request.get("body") or {}
    info = config.model_info(path)
    price = info.get("price") or {}
    seconds = body.get("duration") if isinstance(body.get("duration"), (int, float)) else 5
    usd = 0.0
    if "per_second" in price:
        usd = price["per_second"] * float(seconds)
    elif "per_generation" in price:
        usd = price["per_generation"] * int(body.get("batch_size") or 1)
    return {"usd": round(usd, 4), "pricing_description": "replay: registry.json price, nothing quoted or spent"}


def _fake_submit(key: str, request: dict) -> dict:
    path, body = request.get("path", ""), request.get("body") or {}
    kind = model_kind(path, body)
    seconds = body.get("duration") if isinstance(body.get("duration"), (int, float)) else (2 if kind == "video" else 0)
    aspect = str(body.get("aspect_ratio") or "16:9").replace(":", "x")
    audio = "a" if (body.get("generate_audio") or body.get("sound") == "on") else "s"
    digest = key.rsplit("-", 1)[-1][:12]
    request_id = f"replay-{kind}-{int(round(float(seconds) * 1000))}-{aspect}-{audio}-{digest}"
    return {"status": "queued", "request_id": request_id}


def _fake_status(key: str, request: dict) -> dict:
    request_id = str(request.get("request_id", ""))
    parts = request_id.split("-")
    kind = parts[1] if len(parts) > 2 else "image"
    url = f"{REPLAY_SCHEME}{PROVIDER}/{request_id}/output.{'mp4' if kind == 'video' else 'png'}"
    out: dict = {"status": "completed", "request_id": request_id}
    if kind == "video":
        out["video"] = {"url": url}
    else:
        out["images"] = [{"url": url}]
    return out


def _fake_cancel(key: str, request: dict) -> dict:
    return {"status": "canceled", "request_id": request.get("request_id")}


def _fake_upload(key: str, request: dict) -> dict:
    digest = str(request.get("sha256", ""))[:16]
    ext = (mimetypes.guess_extension(str(request.get("content_type") or "")) or ".bin").lstrip(".")
    return {"upload_url": f"{REPLAY_SCHEME}upload/{digest}", "public_url": f"{REPLAY_SCHEME}uploaded/{digest}.{ext}",
            "upload_headers": {"Content-Type": request.get("content_type")}}


def _fake_probe(key: str, request: dict) -> dict:
    return {"http": 404, "detail": "replay: a request that can't exist"}


# The replay stand-ins, by operation. Tests re-install these before each test, so a stub another test left in the
# shared registry can't change what a replayed call answers.
FAKERS: dict[str, Callable[[str, dict], dict]] = {
    "estimate": _fake_estimate, "submit": _fake_submit, "status": _fake_status, "cancel": _fake_cancel,
    "upload_url": _fake_upload, "probe": _fake_probe,
}
for _op, _faker in FAKERS.items():
    replay.register_faker(PROVIDER, _op, _faker)


def fake_media(url: str, dest: Path, stem: str) -> Path:
    """Make the local stand-in for a replayed output: a flat test frame, or a tiny test clip of the asked length.

    The clip is a slow neutral-grey drift (so contact sheets, conforms and mattes have motion to work with), 24 fps,
    at a tiny size in the asked aspect ratio, with a very quiet noise bed when the request asked for audio."""
    request_id = url[len(REPLAY_SCHEME):].split("/")[1] if "/" in url[len(REPLAY_SCHEME):] else "replay"
    parts = request_id.split("-")
    kind = parts[1] if len(parts) > 2 else "image"
    seconds = int(parts[2]) / 1000 if len(parts) > 2 and parts[2].isdigit() else 2.0
    aspect = parts[3].replace("x", ":") if len(parts) > 3 else "16:9"
    audio = len(parts) > 4 and parts[4] == "a"
    width, height = _FAKE_ASPECTS.get(aspect, _FAKE_ASPECTS["16:9"])
    if kind != "video":
        return _fake_still(dest / f"{stem}.png", width, height, request_id)
    return _fake_clip(dest / f"{stem}.mp4", width, height, max(seconds, 1 / _FAKE_FPS), request_id, audio)


def _seed(text: str) -> int:
    return int(jsonio.sha256_bytes(text)[:8], 16)


def _fake_still(out: Path, width: int, height: int, request_id: str) -> Path:
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    rng = np.random.default_rng(_seed(request_id))
    base = 118 + int(rng.integers(0, 20))
    ramp = np.linspace(-10, 10, width, dtype=np.float32)[None, :]
    img = np.clip(base + ramp + np.zeros((height, 1), np.float32), 0, 255).astype(np.uint8)
    Image.fromarray(np.dstack([img, img, img])).save(out)
    return out


def _fake_clip(out: Path, width: int, height: int, seconds: float, request_id: str, audio: bool) -> Path:
    import numpy as np  # noqa: PLC0415

    from slate import procs  # noqa: PLC0415

    ffmpeg = procs.which("ffmpeg")
    if not ffmpeg:
        raise MissingPrerequisite("Replay needs ffmpeg to make its tiny test clip.",
                                  hint="Run `slate doctor --install` (it installs ffmpeg with your yes).")
    frames = max(1, int(round(seconds * _FAKE_FPS)))
    rng = np.random.default_rng(_seed(request_id))
    phase = float(rng.uniform(0, 1))
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    raw = bytearray()
    for i in range(frames):
        u = i / max(1, frames - 1)
        cx, cy = width * (0.25 + 0.5 * u), height * (0.5 + 0.1 * np.sin(2 * np.pi * (u + phase)))
        disc = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * (0.18 * height) ** 2)))
        grey = 150 - 40 * disc + 12 * (xx / width - 0.5)
        g = np.clip(grey, 0, 255).astype(np.uint8)
        raw += np.dstack([g, g, g]).tobytes()
    argv = [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-r", str(_FAKE_FPS), "-i", "-"]
    tmp_wav = None
    if audio:
        tmp_wav = Path(tempfile.mkstemp(prefix="slate-replay-", suffix=".wav")[1])
        _quiet_noise(tmp_wav, frames / _FAKE_FPS, _seed(request_id))
        argv += ["-i", str(tmp_wav), "-c:a", "aac", "-b:a", "96k", "-shortest"]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "18",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", str(out)]
    try:
        procs.run(argv, input=bytes(raw), text=False, check=True, timeout=120)
    finally:
        if tmp_wav:
            tmp_wav.unlink(missing_ok=True)
    return out


def _quiet_noise(path: Path, seconds: float, seed: int, rate: int = 48000) -> None:
    import wave  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    n = max(1, int(seconds * rate))
    noise = np.cumsum(rng.standard_normal(n)).astype(np.float64)
    noise -= np.convolve(noise, np.ones(480) / 480, mode="same")  # brown noise, gently high-passed: "wind"
    noise = noise / (np.abs(noise).max() + 1e-9) * 0.001  # about -60 dBFS
    pcm = (noise * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def describe_lane() -> dict:
    """What Slate generates through, for doctor and the MCP server: one lane, the Cloud API."""
    return {"provider": PROVIDER, "lane": LANE, "base": BASE, "auth": "Authorization: Key <id:secret> (HF_KEY or "
            "Keychain higgsfield-cloud-api)", "app_credits": "off: not funded; Slate never generates through the "
            "Higgsfield CLI or app", "denied_connector_tools": list(DENIED_CONNECTOR_TOOLS)}


def log_quote(path: str, quote: dict) -> None:
    usd, saving = quote_usd(quote), quote_saving_usd(quote)
    log.info(f"{path}: ${usd if usd is not None else '?'}" + (f" (${saving} off a list price of "
                                                                f"${quote_list_usd(quote)})" if saving else "")
             + (" (replay)" if quote.get("replay") else ""))


# ---------------------------------------------------------------------------------------------------------------
# The job queue's recovery hook (slate.jobs.resume): how an abandoned row's request is settled


def resume_poll(request_id: str | None, row: dict) -> dict:
    """One status poll for an abandoned ledger row. A finished job books the quoted price (`usd`, after any discount:
    the Cloud API has no balance endpoint); failed, moderated and cancelled ones aren't billed."""
    if not request_id:
        return {"status": "unknown", "why": "no request id to poll (the submit died before Higgsfield answered)"}
    answer = Client().status(str(request_id))
    state = answer.get("status")
    if state == "completed":
        return {"status": "done", "actual": float((row.get("estimate") or {}).get("amount") or 0.0),
                "result": {"status": state, "recovered": True, "lane": LANE}}
    if state in UNBILLED:
        return {"status": "failed", "actual": 0.0, "why": f"Higgsfield finished it as {state} (not billed)"}
    return {"status": "running"}


def _register_resume_hooks() -> None:
    try:
        import importlib  # noqa: PLC0415

        resume = importlib.import_module("slate.jobs.resume")
    except ModuleNotFoundError as exc:
        if str(exc.name or "").startswith("slate.jobs"):
            return
        raise
    if hasattr(resume, "register"):
        resume.register(PROVIDER, resume_poll)


_register_resume_hooks()
