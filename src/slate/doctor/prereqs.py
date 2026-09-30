"""`slate doctor --install`: set up the free prerequisites that are missing, asking before each one.

Only free things are ever offered: Homebrew formulas (ffmpeg, the arm64 Rubber Band, uv, Node, Whisper, gh, git,
librsvg, potrace), Whisper's turbo model, Slate's Node engine and Remotion's headless Chrome, the higgsfield CLI, the AE
bridge at its pin, Slate's OCR extra, and the job queue's login item. Paid things (After Effects, plans, credits), keys
and permissions are the person's, and doctor only says how.

It asks only a person, in their own Terminal: one y/N per item, and nothing runs without a yes. From an agent (or
anything without a Terminal) it prints the commands and exits 3, because nothing ever prompts an agent.
"""

from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path
from typing import Callable

from slate import log, procs
from slate.doctor.common import command_text
from slate.doctor.env import Env
from slate.errors import MissingPrerequisite, SlateError

# The order installs run in: tools first, then what depends on them (Node before the engine, the engine before Chrome,
# Whisper before its model, uv before the OCR extra).
ORDER = ("brew:ffmpeg", "brew:rubberband", "brew:uv", "brew:node", "brew:git", "brew:gh", "brew:openai-whisper",
         "whisper-model", "engine", "chrome", "brew:librsvg", "brew:potrace", "npm:@higgsfield/cli", "ae-bridge", "ocr",
         "jobs-agent")
INSTALL_TIMEOUT_S = 3600


def plan(rows: list[dict]) -> list[dict]:
    """The install items behind the rows that aren't green, merged (one per id) and in install order."""
    items: dict[str, dict] = {}
    for r in rows:
        spec = r.get("install")
        if not spec or r.get("status") == "green":
            continue
        item = items.setdefault(spec["id"], {**spec, "for": []})
        item["for"].append(r["id"])
    rank = {k: i for i, k in enumerate(ORDER)}
    return sorted(items.values(), key=lambda it: (rank.get(it["id"], len(ORDER)), it["id"]))


def public(item: dict) -> dict:
    """An item as reported: what it is, the one command it runs, and why it's offered."""
    out = {k: item.get(k) for k in ("id", "what", "size", "for", "needs", "note", "kind") if item.get(k) is not None}
    out["command"] = command_text(item)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Carrying an item out


def _silence(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def _whisper_model(env: Env) -> dict:
    """Whisper downloads its model the first time it runs one; give it one second of silence to run on."""
    whisper = env.which("whisper")
    if not whisper:
        raise MissingPrerequisite("Whisper isn't installed, so its model can't be downloaded yet.",
                                  hint="Say yes to Whisper first (brew install openai-whisper).")
    with tempfile.TemporaryDirectory(prefix="slate-whisper-") as tmp:
        clip = _silence(Path(tmp) / "silence.wav")
        procs.run([whisper, str(clip), "--model", "turbo", "--language", "en", "--output_dir", tmp,
                   "--output_format", "txt", "--fp16", "False"], timeout=INSTALL_TIMEOUT_S, check=True, capture=False)
    return {"downloaded": True}


def _engine(env: Env) -> dict:
    """The pinned engine (`npm ci` from its lockfile), then its headless Chrome, so the new copy can render at once."""
    from slate.engine import install  # noqa: PLC0415

    folder = install.ensure(npm_ci=True)
    return {"engine_dir": str(folder), **_chrome(env)}


def _chrome(env: Env) -> dict:
    from slate.engine import bridge  # noqa: PLC0415

    res = bridge.call("browser-ensure", {}, timeout=INSTALL_TIMEOUT_S)
    return {"browser": (res or {}).get("browser")}


def _ae_bridge(env: Env) -> dict:
    from slate.ae import bridge  # noqa: PLC0415

    return bridge.install(yes=True)


def _jobs_agent(env: Env) -> dict:
    from slate.jobs import agent  # noqa: PLC0415

    return agent.install(yes=True)


CALLS: dict[str, Callable[[Env], dict]] = {
    "whisper-model": _whisper_model,
    "engine": _engine,
    "chrome": _chrome,
    "ae-bridge": _ae_bridge,
    "jobs-agent": _jobs_agent,
}


def carry_out(item: dict, env: Env) -> dict:
    """Run one item (its output goes straight to the Terminal). Returns {id, ok, error?}."""
    if item.get("needs"):
        return {"id": item["id"], "ok": False, "error": item["needs"]}
    try:
        if item.get("argv"):
            proc = procs.run([str(a) for a in item["argv"]], timeout=INSTALL_TIMEOUT_S, capture=False,
                             env={"HOMEBREW_NO_ENV_HINTS": "1"})
            if proc.returncode != 0:
                return {"id": item["id"], "ok": False, "error": f"{command_text(item)} exited with {proc.returncode}"}
            return {"id": item["id"], "ok": True}
        call = CALLS.get(str(item.get("call")))
        if call is None:
            return {"id": item["id"], "ok": False, "error": f"doctor doesn't know how to install {item['id']!r}"}
        result = call(env)
        return {"id": item["id"], "ok": True, "result": result}
    except SlateError as exc:
        return {"id": item["id"], "ok": False, "error": exc.message}


def _ask_tty(prompt: str) -> str:
    """Ask on stderr and read the answer (stdout stays free for --json)."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        return input()
    except EOFError:
        return ""


def interactive(items: list[dict], env: Env, *, ask: Callable[[str], str] | None = None) -> list[dict]:
    """One y/N per item, then the yes'd ones run in order. Nothing runs without a yes."""
    ask = ask or _ask_tty
    results = []
    for item in items:
        size = f", {item['size']}" if item.get("size") else ""
        note = f"\n  note: {item['note']}" if item.get("note") else ""
        needs = f"\n  can't run yet: {item['needs']}" if item.get("needs") else ""
        prompt = (f"\nInstall {item['what']}{size}?\n  runs: {command_text(item)}{note}{needs}\n"
                  "  [y/N] ")
        answer = (ask(prompt) or "").strip().lower()
        if answer not in ("y", "yes"):
            results.append({"id": item["id"], "ok": None, "skipped": True})
            continue
        log.info(f"installing {item['id']}…")
        results.append(carry_out(item, env))
    return results
