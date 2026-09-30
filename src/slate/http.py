"""HTTP for vendor APIs, with the lessons from the films built in.

- urllib only (no extra dependency), with a plain User-Agent: Higgsfield's API sits behind Cloudflare, which bans
  urllib's default "Python-urllib/x.y" with error 1010.
- Keys travel only in headers. A URL that contains a key is refused before it is sent.
- Reads (GET, HEAD) retry with backoff on rate limits and server errors. Anything that can bill (POST, PUT, PATCH,
  DELETE) is never retried automatically: a retry after an ambiguous timeout can create, and bill, a duplicate job.
- Error bodies are redacted before they are shown.
"""

from __future__ import annotations

import json
import mimetypes
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping

from slate import __version__
from slate.errors import SlateError
from slate.secrets import _seen_values, redact

USER_AGENT = f"slate/{__version__} (video pipeline; +https://github.com/Quinnod345/slate)"
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class HttpError(SlateError):
    kind = "http_error"

    def __init__(self, status: int, url: str, body: str):
        clean = redact(body[:2000])
        super().__init__(f"HTTP {status} from {_host(url)}: {clean}", details={"status": status})
        self.status = status
        self.body = clean


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc or url


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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace") if exc.fp else ""
            if exc.code in RETRYABLE and attempt + 1 < attempts:
                _sleep(attempt, exc.headers.get("Retry-After") if exc.headers else None)
                continue
            raise HttpError(exc.code, url, payload) from None
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
        with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fh:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
        os.replace(tmp, dest)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise HttpError(exc.code, url, exc.read().decode("utf-8", "replace") if exc.fp else "") from None
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
