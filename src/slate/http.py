"""HTTP for vendor APIs, with the lessons from the films built in.

- urllib only (no extra dependency), with a plain User-Agent: Higgsfield's API sits behind Cloudflare, which bans
  urllib's default "Python-urllib/x.y" with error 1010.
- Keys travel only in headers. A URL that contains a key is refused before it is sent.
- Reads (GET, HEAD) retry with backoff on rate limits and server errors. Anything that can bill (POST, PUT, PATCH,
  DELETE) is never retried automatically: a retry after an ambiguous timeout can create, and bill, a duplicate job.
- A rate limit (429) or an unavailable service (503) that ends a request raises `RateLimited`: an HttpError (so every
  vendor's own handling still sees the status) that is also `slate.errors.TryLater`, exit 75, which the job queue
  answers by trying the job again later instead of failing it. It carries the vendor's Retry-After when there is one.
- Error bodies are redacted before they are shown.
"""

from __future__ import annotations

import datetime as _dt
import json
import mimetypes
import os
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping

from slate import __version__
from slate.errors import SlateError, TryLater
from slate.secrets import _seen_values, redact

USER_AGENT = f"slate/{__version__} (video pipeline; +https://github.com/Quinnod345/slate)"
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
TRY_LATER = {429, 503}  # ends a request as RateLimited (exit 75): the queue tries the job again later
BILLING_METHODS = ("POST", "PUT", "PATCH", "DELETE")
SYSTEM_CA_FILES = ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem")

_ssl_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """A verifying TLS context that works on every Python Slate may run under.

    The python.org installer's Python ships with no CA certificates configured (until someone runs its "Install
    Certificates" script), so a default context verifies nothing and every vendor call fails. When the default store is
    empty, use the system's trust store instead. Verification is never switched off.
    """
    global _ssl_context
    if _ssl_context is None:
        ctx = ssl.create_default_context()
        if not ctx.cert_store_stats().get("x509_ca"):
            for path in (os.environ.get("SSL_CERT_FILE"), *SYSTEM_CA_FILES):
                if path and os.path.exists(path):
                    ctx.load_verify_locations(cafile=path)
                    break
        _ssl_context = ctx
    return _ssl_context


class HttpError(SlateError):
    kind = "http_error"

    def __init__(self, status: int, url: str, body: str):
        clean = redact(body[:2000])
        super().__init__(f"HTTP {status} from {_host(url)}: {clean}", details={"status": status})
        self.status = status
        self.body = clean


class RateLimited(HttpError, TryLater):
    """A 429 or a 503 that ended the request. Still an HttpError (vendors map statuses their own way), and a
    TryLater (exit 75), so an error nobody handles sends a queued job back to the queue instead of failing it.

    For a call that can bill, nothing was retried here: the vendor turned it away, and sending it again is the
    queue's job, after a backoff, never this function's."""

    kind = "try_later"
    exit_code = 75

    def __init__(self, status: int, url: str, body: str, *, method: str = "GET", retry_after_s: float | None = None):
        clean = redact(body[:2000])
        what = ("a rate limit (HTTP 429)" if status == 429 else "the service is unavailable for now (HTTP 503)")
        billing = method.upper() in BILLING_METHODS
        then = ("Nothing was sent again, because this call can bill." if billing else
                "The retries ran out.")
        wait = f" It asked for {retry_after_s:g} s." if retry_after_s is not None else ""
        SlateError.__init__(
            self, f"HTTP {status} from {_host(url)}: {what}. {then}{wait}" + (f" ({clean[:300]})" if clean else ""),
            hint="Wait a minute, then run the same command again (a queued job goes back in the queue and retries by "
                 "itself).",
            details={"status": status, "method": method.upper(),
                     **({"retry_after_s": retry_after_s} if retry_after_s is not None else {})})
        self.status = status
        self.body = clean
        self.retry_after_s = retry_after_s


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc or url


def retry_after_seconds(value: str | None) -> float | None:
    """A Retry-After header in seconds (the delta form; an HTTP date is read too), or None."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return max(0.0, (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def http_error(status: int, url: str, body: str, *, method: str = "GET", headers: Any = None) -> HttpError:
    """The error for a non-2xx answer: RateLimited (exit 75) for 429 and 503, HttpError otherwise."""
    if status in TRY_LATER:
        retry_after = retry_after_seconds(headers.get("Retry-After") if headers is not None else None)
        return RateLimited(status, url, body, method=method, retry_after_s=retry_after)
    return HttpError(status, url, body)


def _check_url(url: str) -> None:
    for value in _seen_values:
        if value and value in url:
            raise SlateError("Refusing to send a request with a key in its URL (keys go in headers only).")


def request(method: str, url: str, *, headers: Mapping[str, str] | None = None, json_body: Any = None,
            data: bytes | None = None, timeout: float = 60, retries: int = 3) -> tuple[int, dict, bytes]:
    """Send a request and return (status, headers, body). Non-2xx raises HttpError."""
    _check_url(url)
    method = method.upper()
    body = data
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    attempts = retries + 1 if method in ("GET", "HEAD") else 1
    last_exc: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace") if exc.fp else ""
            if exc.code in RETRYABLE and attempt + 1 < attempts:
                _sleep(attempt, exc.headers.get("Retry-After") if exc.headers else None)
                continue
            raise http_error(exc.code, url, payload, method=method, headers=exc.headers) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                _sleep(attempt, None)
                continue
            raise SlateError(f"Couldn't reach {url.split('/')[2]}: {redact(str(exc))}",
                             hint="Check the network; a POST is never retried automatically.") from None
    raise SlateError(f"Request failed: {last_exc}")  # pragma: no cover


def _sleep(attempt: int, retry_after: str | None) -> None:
    if retry_after and retry_after.isdigit():
        time.sleep(min(int(retry_after), 60))
    else:
        time.sleep(min(2 ** attempt + random.random(), 30))


def request_json(method: str, url: str, **kw) -> Any:
    status, _, body = request(method, url, **kw)
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SlateError(f"Expected JSON from {url.split('/')[2]} (HTTP {status}), got something else.") from exc


def download(url: str, dest: str | os.PathLike, *, headers: Mapping[str, str] | None = None,
             timeout: float = 600) -> Path:
    """Stream a file to disk atomically (a partial download never looks finished)."""
    _check_url(url)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp, tmp.open("wb") as fh:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
        os.replace(tmp, dest)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise http_error(exc.code, url, exc.read().decode("utf-8", "replace") if exc.fp else "",
                         headers=exc.headers) from None
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def multipart(fields: Mapping[str, Any], files: Mapping[str, str | os.PathLike]) -> tuple[bytes, str]:
    """Encode a multipart/form-data body (ElevenLabs' isolator, remix and alignment endpoints take files)."""
    boundary = f"slate-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{text}\r\n'.encode())
    for name, path in files.items():
        p = Path(path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{p.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode()
        parts.append(head + p.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
