"""Replay mode: vendor calls answered from recorded fixtures, so tests, evals and `slate demo` spend nothing.

- `SLATE_FAKE_VENDORS=1` turns replay on. `--live` (which sets `SLATE_LIVE=1`) turns it off for one command.
- Fixtures live in `fixtures/vendors/<provider>/<key>.json`, with any binary payloads beside them
  (`<key>.<name>`). `SLATE_FIXTURES` points somewhere else (tests use a temp folder).
- A request's key is a stable hash of what was asked (provider, operation, body), or a readable name the caller gives.
- With no fixture, a registered faker makes a deterministic stand-in (a tone for speech, a pad for music, a flat
  frame for a plate), so the whole pipeline can run offline. Fakes are marked `"replay": true` everywhere they land,
  and the delivery manifest refuses to call a film final when any replayed asset is in it.
- `SLATE_RECORD=1` in live mode writes each real response as a new fixture.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from slate import jsonio, paths
from slate.errors import MissingPrerequisite

Faker = Callable[[str, dict], dict]
_FAKERS: dict[tuple[str, str], Faker] = {}


class ReplayMissing(MissingPrerequisite):
    kind = "replay_missing"


def fake_vendors() -> bool:
    """True when vendor calls must not reach a vendor (replay mode)."""
    if os.environ.get("SLATE_LIVE") == "1":
        return False
    return os.environ.get("SLATE_FAKE_VENDORS") == "1"


def recording() -> bool:
    return os.environ.get("SLATE_RECORD") == "1" and not fake_vendors()


def fixtures_root() -> Path:
    env = os.environ.get("SLATE_FIXTURES")
    return Path(env).expanduser() if env else paths.FIXTURES_DIR / "vendors"


def request_key(provider: str, op: str, body: Any = None) -> str:
    return f"{op}-{jsonio.sha256_obj({'provider': provider, 'op': op, 'body': body})[:16]}"


def fixture_path(provider: str, key: str) -> Path:
    return fixtures_root() / provider / f"{key}.json"


def payload_path(provider: str, key: str, name: str) -> Path:
    return fixtures_root() / provider / f"{key}.{name}"


def load(provider: str, key: str) -> dict | None:
    path = fixture_path(provider, key)
    return jsonio.read(path) if path.exists() else None


def load_payload(provider: str, key: str, name: str) -> bytes | None:
    path = payload_path(provider, key, name)
    return path.read_bytes() if path.exists() else None


def record(provider: str, key: str, response: dict, payloads: dict[str, bytes] | None = None) -> Path:
    path = fixture_path(provider, key)
    jsonio.write_atomic(path, {"replay_fixture": True, "provider": provider, "key": key, "response": response})
    for name, blob in (payloads or {}).items():
        payload_path(provider, key, name).write_bytes(blob)
    return path


def register_faker(provider: str, op: str, faker: Faker) -> None:
    """Register a deterministic stand-in for (provider, op), used when no fixture matches."""
    _FAKERS[(provider, op)] = faker


def respond(provider: str, op: str, request: dict, key: str | None = None) -> dict:
    """The replayed response for a request: a recorded fixture, else the faker's stand-in, else ReplayMissing."""
    key = key or request_key(provider, op, request)
    fixture = load(provider, key)
    if fixture is not None:
        response = dict(fixture.get("response", fixture))
        response.setdefault("replay", True)
        response.setdefault("replay_key", key)
        return response
    faker = _FAKERS.get((provider, op))
    if faker is not None:
        response = dict(faker(key, request))
        response["replay"] = True
        response["replay_key"] = key
        return response
    raise ReplayMissing(
        f"Replay mode has no recorded answer for {provider} {op} (key {key}).",
        hint="Record one with SLATE_RECORD=1 and --live, or run this command with --live to spend for real.",
    )
