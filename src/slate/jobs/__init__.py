"""The machine-wide job queue: long work runs detached, in order, within this Mac's limits.

Every long job (a render, a master, a Whisper pass, a bank build, a generation batch) goes through here, so a film you
supervise and an autonomous reel can share one Mac without fighting over CPU, After Effects or API limits.

- `client`  submit, status, wait, cancel, retry, list (what agents and other modules call)
- `runner`  the scheduler behind `slate jobs serve` (kept alive by a per-user launchd agent, or started on demand)
- `shim`    the supervisor that runs one job detached: its own session, stdin closed, under `caffeinate -i`
- `locks`   counted resource slots (render, ae, elevenlabs_tts, elevenlabs_music, higgsfield, whisper)
- `disk`    disk reservations and the free-space floor
- `resume`  provider hooks so a resumed job polls a paid request it already made instead of paying twice
- `agent`   the launchd agent (plist, install, uninstall, status)

Why it exists: during the films, six builders plus 16-sample renders drove the load to 293 on 14 cores, After Effects
blocked on a second script, and one rate limit stopped eight agents at once. Jobs that outlive their agent, one lock per
resource and a queue that knows who outranks whom fix all three.

Submodules load on first use (`from slate.jobs import client`), so importing the package costs nothing.
"""

from __future__ import annotations

import importlib

_SUBMODULES = ("agent", "client", "disk", "locks", "model", "proc", "resume", "runner", "shim", "store")


def __getattr__(name: str):
    if name in _SUBMODULES:
        return importlib.import_module(f"slate.jobs.{name}")
    raise AttributeError(f"module 'slate.jobs' has no attribute {name!r}")
