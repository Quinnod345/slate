"""API keys: read from the environment first, then the macOS login Keychain. Never printed, logged or returned in output.

Rules this module enforces:

- A missing key is a `MissingSecret` (exit 3) whose message says exactly which `slate connect …` to run in Terminal.
- Only `slate connect` ever asks for a key, and only in an interactive Terminal: the person types it into macOS's own
  prompt (`security add-generic-password … -w`), so the key never passes through an agent, a chat or argv.
- `present()` answers "is it there?" without reading the value, which is all `slate doctor` needs.
- Keys never go into URLs, only into request headers.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from dataclasses import dataclass

from slate.errors import MissingPrerequisite, Refused

SECURITY = "/usr/bin/security"


class MissingSecret(MissingPrerequisite):
    """A key Slate needs isn't in the environment or the Keychain."""

    kind = "missing_secret"


@dataclass(frozen=True)
class Secret:
    label: str
    keychain_service: str
    env: tuple[str, ...]
    connect: str
    # Higgsfield's official SDK also accepts a key id and secret in two variables, joined as "id:secret".
    env_pair: tuple[str, str] | None = None


SECRETS: dict[str, Secret] = {
    "elevenlabs": Secret(
        label="ElevenLabs API key",
        keychain_service="elevenlabs-api-key",
        env=("ELEVENLABS_API_KEY",),
        connect="slate connect elevenlabs",
    ),
    "elevenlabs_oneiro": Secret(
        # Oneiro's own key, created with a credit quota and only TTS, music and alignment scopes (autonomous mode).
        label="ElevenLabs key for Oneiro (capped)",
        keychain_service="slate-elevenlabs-oneiro",
        env=("SLATE_ONEIRO_ELEVENLABS_API_KEY",),
        connect="slate connect elevenlabs --oneiro",
    ),
    "higgsfield": Secret(
        label="Higgsfield Cloud API key",
        keychain_service="higgsfield-cloud-api",
        env=("HF_KEY",),
        env_pair=("HF_API_KEY", "HF_API_SECRET"),
        connect="slate connect higgsfield --api-key",
    ),
}

_seen_values: set[str] = set()


def _spec(name: str) -> Secret:
    if name not in SECRETS:
        raise MissingPrerequisite(f"Slate doesn't know a secret called {name!r}. Known: {', '.join(SECRETS)}.")
    return SECRETS[name]


def _from_env(spec: Secret) -> str | None:
    for var in spec.env:
        if os.environ.get(var):
            return os.environ[var]
    if spec.env_pair and all(os.environ.get(v) for v in spec.env_pair):
        return f"{os.environ[spec.env_pair[0]]}:{os.environ[spec.env_pair[1]]}"
    return None


def _keychain_read(service: str) -> str | None:
    if not os.path.exists(SECURITY):
        return None
    proc = subprocess.run(
        [SECURITY, "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.rstrip("\n")
    return value or None


def _keychain_has(service: str) -> bool:
    if not os.path.exists(SECURITY):
        return False
    proc = subprocess.run(
        [SECURITY, "find-generic-password", "-s", service],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15,
    )
    return proc.returncode == 0


def get(name: str) -> str:
    """The key's value, for a request header. Raises MissingSecret with the connect command when it's absent."""
    spec = _spec(name)
    value = _from_env(spec) or _keychain_read(spec.keychain_service)
    if not value:
        env_names = " or ".join(spec.env)
        raise MissingSecret(
            f"No {spec.label}: set {env_names} or add the Keychain item {spec.keychain_service!r}.",
            hint=f"Run `{spec.connect}` in your own Terminal (it asks for the key there, never in chat).",
        )
    _seen_values.add(value)
    return value


def present(name: str) -> str | None:
    """Where the key is ('env' or 'keychain'), or None. Never reads the Keychain value."""
    spec = _spec(name)
    if _from_env(spec):
        return "env"
    if _keychain_has(spec.keychain_service):
        return "keychain"
    return None


def redact(text: str) -> str:
    """Scrub any key this process has read out of a message before it is logged or shown."""
    for value in _seen_values:
        if value and value in text:
            text = text.replace(value, "•••")
    return text


def store_interactive(name: str) -> int:
    """Ask for a key in macOS's own Terminal prompt and store it in the Keychain. Refuses when not a real Terminal."""
    from slate.procs import agent_context  # noqa: PLC0415 (avoid an import cycle)

    spec = _spec(name)
    ctx = agent_context()
    if ctx or not sys.stdin.isatty():
        raise Refused(
            f"Keys are only ever typed by a person, in their own Terminal (this looks like {ctx or 'a non-interactive shell'}).",
            hint=f"Open Terminal and run: {spec.connect}",
        )
    print(f"Paste your {spec.label} at the prompt below. It goes straight into your login Keychain as "
          f"{spec.keychain_service!r}; Slate never sees it on screen.", file=sys.stderr)
    # -w as the last option makes `security` prompt for the value itself, so it never appears in argv or history.
    proc = subprocess.run([SECURITY, "add-generic-password", "-U", "-a", getpass.getuser(),
                           "-s", spec.keychain_service, "-l", f"Slate: {spec.label}", "-w"])
    return proc.returncode
