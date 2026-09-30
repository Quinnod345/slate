"""iOS Simulator captures: a pinned build, a seeded demo account, Apple's status bar, a scripted recording.

Generalized from video-verifier's record_ios_sim.sh and the lessons of the hand-built reels:

- **A pinned build.** The spec names the app bundle and the SHA-256 it must hash to. Any other build is refused
  before anything boots, so a capture in week three shows the same app as week one.
- **An isolated Simulator.** By default Slate makes its own device (`slate-<film>-<capture>`, the named device's type
  and runtime, default iPhone 17 Pro) and erases it before each capture, so no one's personal data can appear and
  other work on this Mac's simulators is left alone. It's shut down afterwards; nothing is left running.
- **A seeded demo account.** Launch arguments, environment, an .xcappdata container and media from the spec's
  `account` block. Never a personal account.
- **Apple's status bar**: 9:41, full battery, full Wi-Fi and cellular bars (overridable per spec).
- **Scripted steps** at fixed times from the moment simctl reports "Recording started": launch, open URLs, taps and
  typing through a tap driver (idb or AXe when installed; `scripted` when the app plays its own demo from launch
  arguments), with each step's actual time recorded for the beat map.
- **Conformed** to the film's frame rate as a JPEG sequence. simctl records a variable frame rate and only writes a
  frame when the screen changes, so each film frame shows what was on screen at that moment and the last frame is
  held to the spec's duration.

Nothing here opens a browser. Tests never boot a simulator: they hand `capture` a fake `Simctl`.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import signal
import time
from pathlib import Path
from typing import Callable

from slate import jsonio, log, procs, replay
from slate.capture import beatmap, conform, media, spec as spec_mod, store
from slate.errors import MissingPrerequisite, Refused, SlateError, UsageError
from slate.gen import deps

SETTLE_S = 0.4  # recorded after the last scheduled moment, then trimmed by the conform


# ---------------------------------------------------------------------------------------------------------------
# The pinned build


def bundle_sha256(path: str | os.PathLike) -> str:
    """A stable SHA-256 of an app: of the file itself (.ipa, .zip), or of every file in an .app bundle (relative
    path and content, in sorted order; symlinks by their target), so the same build always hashes the same."""
    root = Path(path)
    if root.is_file():
        return jsonio.sha256(root)
    if not root.is_dir():
        raise UsageError(f"No app at {root}.")
    digest = hashlib.sha256()
    for item in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = item.relative_to(root).as_posix()
        if item.is_symlink():
            digest.update(f"L {rel} -> {os.readlink(item)}\n".encode())
        elif item.is_file():
            digest.update(f"F {rel} {jsonio.sha256(item)}\n".encode())
    return digest.hexdigest()


def read_info(app: Path) -> dict:
    """The bundle's identity from its Info.plist (bundle id, version, build)."""
    plist = app / "Info.plist"
    if not plist.is_file():
        return {}
    with plist.open("rb") as fh:
        info = plistlib.load(fh)
    return {"bundle_id": info.get("CFBundleIdentifier"), "version": info.get("CFBundleShortVersionString"),
            "build": info.get("CFBundleVersion"), "name": info.get("CFBundleName")}


def check_build(film_dir: Path, spec: dict) -> dict:
    """Refuse unless the app on disk is exactly the pinned build."""
    app = spec_mod.resolve(film_dir, spec["app"]["path"])
    if not app.exists():
        raise MissingPrerequisite(f"The pinned build isn't at {app}.",
                                  hint="Build the pinned version (or copy it there), then capture again.")
    actual = bundle_sha256(app)
    expected = spec["app"]["build_sha256"]
    info = read_info(app) if app.is_dir() else {}
    if actual != expected:
        raise Refused(f"The app at {app} is a different build from the one this capture pins "
                      f"(hash {actual[:12]}…, pinned {expected[:12]}…).",
                      hint="Capture the pinned build, or pin this one on purpose: a new pin changes what the film "
                           "shows, so it needs the person's yes.",
                      details={"actual": actual, "expected": expected})
    wanted = spec["app"].get("bundle_id")
    if wanted and info.get("bundle_id") and wanted != info["bundle_id"]:
        raise Refused(f"The pinned build is {info['bundle_id']}, but the spec says {wanted}.")
    return {"path": str(app), "sha256": actual, "expected": expected, "match": True,
            "bundle_id": wanted or info.get("bundle_id"), "version": info.get("version"), "build": info.get("build")}


# ---------------------------------------------------------------------------------------------------------------
# simctl


class Recorder:
    """A running `simctl io recordVideo` (started detached, stopped with SIGINT so the file is finalized)."""

    def __init__(self, pid: int, path: Path, log_path: Path):
        self.pid, self.path, self.log_path = pid, path, log_path
        self.stopped = False

    def wait_started(self, timeout: float = 30.0, sleep: Callable[[float], None] = time.sleep) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.log_path.read_text(errors="replace") if self.log_path.exists() else ""
            if "Recording started" in text:
                return
            if not procs.pid_alive(self.pid) or _reaped(self.pid):
                raise SlateError(f"simctl stopped before recording started: {text.strip()[-400:]}")
            sleep(0.05)
        self.stop()
        raise SlateError("simctl didn't start recording within 30 s.", hint="Is the Simulator booted and awake?")

    def stop(self, timeout: float = 30.0) -> None:
        if self.stopped:
            return
        self.stopped = True
        for sig, grace in ((signal.SIGINT, timeout), (signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                _reaped(self.pid)
                return
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if _reaped(self.pid):
                    return
                time.sleep(0.1)


def _reaped(pid: int) -> bool:
    """True once the child has exited (and reap it, so it doesn't linger as a zombie)."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == pid
    except ChildProcessError:
        return True


class Simctl:
    """`xcrun simctl`, through slate.procs (no shell, BROWSER=none, a timeout on everything)."""

    def run(self, *args: str, timeout: float = 120, env: dict | None = None, check: bool = True):
        return procs.run(["xcrun", "simctl", *args], timeout=timeout, env=env, check=check)

    def json(self, *args: str) -> dict:
        return json.loads(self.run("list", *args, "-j", timeout=60).stdout or "{}")

    def devices(self) -> list[dict]:
        out = []
        for runtime, devs in (self.json("devices", "available").get("devices") or {}).items():
            for dev in devs:
                out.append({**dev, "runtime": runtime})
        return out

    def device_types(self) -> list[dict]:
        return self.json("devicetypes").get("devicetypes") or []

    def runtimes(self) -> list[dict]:
        return self.json("runtimes").get("runtimes") or []

    def create(self, name: str, device_type: str, runtime: str) -> str:
        return (self.run("create", name, device_type, runtime).stdout or "").strip()

    def boot(self, udid: str) -> None:
        proc = self.run("boot", udid, check=False)
        if proc.returncode != 0 and "Booted" not in (proc.stderr or ""):
            raise SlateError(f"simctl couldn't boot {udid}: {(proc.stderr or '').strip()[-300:]}")
        self.run("bootstatus", udid, "-b", timeout=300)

    def shutdown(self, udid: str) -> None:
        self.run("shutdown", udid, check=False, timeout=120)

    def erase(self, udid: str) -> None:
        self.run("erase", udid, timeout=300)

    def install(self, udid: str, app: Path) -> None:
        self.run("install", udid, str(app), timeout=600)

    def install_app_data(self, udid: str, data: Path) -> None:
        self.run("install_app_data", udid, str(data), timeout=600)

    def addmedia(self, udid: str, files: list[Path]) -> None:
        self.run("addmedia", udid, *[str(f) for f in files], timeout=300)

    def status_bar(self, udid: str, bar: dict) -> list[str]:
        args = status_bar_args(bar)
        self.run("status_bar", udid, "override", *args)
        return args

    def clear_status_bar(self, udid: str) -> None:
        self.run("status_bar", udid, "clear", check=False)

    def launch(self, udid: str, bundle_id: str, args: list[str], env: dict[str, str]) -> None:
        child_env = {f"SIMCTL_CHILD_{k}": v for k, v in env.items()}
        self.run("launch", "--terminate-running-process", udid, bundle_id, *args, env=child_env)

    def terminate(self, udid: str, bundle_id: str) -> None:
        self.run("terminate", udid, bundle_id, check=False)

    def openurl(self, udid: str, url: str) -> None:
        self.run("openurl", udid, url)

    def start_recording(self, udid: str, path: Path, log_path: Path) -> Recorder:
        xcrun = procs.require("xcrun", "iOS captures need Xcode's command-line tools (xcode-select --install).")
        path.parent.mkdir(parents=True, exist_ok=True)
        log_path.unlink(missing_ok=True)
        pid = procs.spawn_detached([xcrun, "simctl", "io", udid, "recordVideo", "--codec=h264", "--force",
                                    str(path)], log_path=log_path)
        return Recorder(pid, path, log_path)


def status_bar_args(bar: dict) -> list[str]:
    """`simctl status_bar override` flags for Apple's clean status bar (9:41, full battery, full bars)."""
    args = ["--time", str(bar["time"]), "--batteryState", bar["battery_state"],
            "--batteryLevel", str(bar["battery"]), "--wifiMode", "active", "--wifiBars", str(bar["wifi_bars"]),
            "--cellularMode", "active", "--cellularBars", str(bar["cell_bars"]), "--dataNetwork", bar["data_network"],
            "--operatorName", bar.get("operator", "")]
    return args


def resolve_device(sim: Simctl, spec: dict, film_id: str | None) -> dict:
    """The device to capture on. Isolated (the default): Slate's own device of the named type, made once."""
    devices = sim.devices()
    wanted = spec["device"]
    named = [d for d in devices if d.get("name") == wanted]
    if not spec.get("isolate", True):
        if not named:
            raise MissingPrerequisite(f"There's no Simulator called {wanted!r}.",
                                      hint="`xcrun simctl list devices` lists them; or leave isolate on and Slate "
                                           "makes its own.")
        return {**named[0], "isolated": False, "created": False}
    own = f"slate-{film_id or 'film'}-{spec['name']}"
    mine = [d for d in devices if d.get("name") == own]
    if mine:
        return {**mine[0], "isolated": True, "created": False}
    if named:
        device_type, runtime = named[0].get("deviceTypeIdentifier"), named[0].get("runtime")
    else:
        types = [t for t in sim.device_types() if t.get("name") == wanted]
        if not types:
            raise MissingPrerequisite(f"Xcode has no Simulator device type called {wanted!r}.",
                                      hint="`xcrun simctl list devicetypes` lists them.")
        device_type = types[0]["identifier"]
        ios = [r for r in sim.runtimes() if r.get("platform") == "iOS" and r.get("isAvailable")]
        if not ios:
            raise MissingPrerequisite("No iOS Simulator runtime is installed.",
                                      hint="Install one from Xcode's Settings → Components.")
        newest = sorted(ios, key=lambda r: [int(x) for x in str(r.get("version", "0")).split(".") if x.isdigit()])
        runtime = newest[-1]["identifier"]
    if not device_type or not runtime:
        raise MissingPrerequisite(f"Couldn't tell the device type and runtime of {wanted!r}.")
    udid = sim.create(own, device_type, runtime)
    return {"name": own, "udid": udid, "runtime": runtime, "deviceTypeIdentifier": device_type, "state": "Shutdown",
            "isolated": True, "created": True}


# ---------------------------------------------------------------------------------------------------------------
# Tap drivers


class TapDriver:
    name = "none"

    def tap(self, udid: str, x: float, y: float) -> None:
        raise NotImplementedError

    def type(self, udid: str, text: str) -> None:
        raise NotImplementedError


class IdbDriver(TapDriver):
    name = "idb"

    def tap(self, udid: str, x: float, y: float) -> None:
        procs.run(["idb", "ui", "tap", "--udid", udid, str(round(x)), str(round(y))], check=True, timeout=30)

    def type(self, udid: str, text: str) -> None:
        procs.run(["idb", "ui", "text", "--udid", udid, text], check=True, timeout=60)


class AxeDriver(TapDriver):
    name = "axe"

    def tap(self, udid: str, x: float, y: float) -> None:
        procs.run(["axe", "tap", "-x", str(round(x)), "-y", str(round(y)), "--udid", udid], check=True, timeout=30)

    def type(self, udid: str, text: str) -> None:
        procs.run(["axe", "type", text, "--udid", udid], check=True, timeout=60)


class ScriptedDriver(TapDriver):
    """The app performs its own taps (a demo mode from launch arguments); steps only mark when they happen."""

    name = "scripted"

    def tap(self, udid: str, x: float, y: float) -> None:
        return None

    def type(self, udid: str, text: str) -> None:
        return None


def resolve_tap_driver(spec: dict) -> TapDriver | None:
    """The driver for tap and type steps, or None when the spec has none. Refuses when taps have no way to happen."""
    wants = [s for s in spec.get("steps") or [] if s["do"] in ("tap", "type")]
    choice = spec.get("tap_driver", "auto")
    if choice == "scripted":
        return ScriptedDriver()
    if not wants and choice == "auto":
        return None
    candidates = {"idb": IdbDriver, "axe": AxeDriver}
    order = [choice] if choice in candidates else ["idb", "axe"]
    for name in order:
        if procs.which(name):
            return candidates[name]()
    raise MissingPrerequisite(
        "This capture taps or types, and no Simulator tap driver is installed (Slate looked for "
        + " and ".join(order) + ").",
        hint="Install idb (fb-idb) or AXe yourself, or set \"tap_driver\": \"scripted\" and let the app play its "
             "own demo from launch arguments; the spec's taps then mark when they happen.")


# ---------------------------------------------------------------------------------------------------------------
# The capture


def plan(film_dir: Path, spec: dict) -> dict:
    """What a capture would do, checked without booting anything (--dry-run)."""
    build = check_build(film_dir, spec)
    driver = resolve_tap_driver(spec)
    return {"capture": spec["name"], "build": build, "device": spec["device"], "isolate": spec.get("isolate", True),
            "status_bar": status_bar_args(spec["status_bar"]), "tap_driver": driver.name if driver else None,
            "steps": [{"at": s["at"], "do": s["do"], "id": s.get("id")} for s in spec.get("steps") or []],
            "duration": spec["duration"]}


def capture(film_dir: Path, spec: dict, *, sim: Simctl | None = None, driver: TapDriver | None = None,
            monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
            keep_booted: bool = False) -> Path:
    """Record an iOS capture into captures/<name>/ and return its capture.json path (docs/ARCHITECTURE.md §12)."""
    film_dir = Path(film_dir)
    film = deps.load_film(film_dir)
    spec = spec_mod.normalize(spec, film)
    if spec["kind"] != "ios":
        raise UsageError(f"{spec['name']} is a {spec['kind']} capture, not an iOS one.")
    deps.film_clock(film)
    build = check_build(film_dir, spec)  # refuse a different build before anything is written or booted
    tap_driver = driver if driver is not None else resolve_tap_driver(spec)
    folder = store.capture_dir(film_dir, spec["name"])
    folder.mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(folder / "spec.json", spec)
    sim = sim or Simctl()
    device = resolve_device(sim, spec, film.get("id"))
    udid = device["udid"]
    movie, rec_log = folder / "raw.mov", folder / "logs" / "simctl-record.log"
    steps_done: list[dict] = []
    booted_here = device.get("state") != "Booted"
    recorder: Recorder | None = None
    try:
        if device.get("isolated") and device.get("state") == "Booted":
            sim.shutdown(udid)
            booted_here = True
        if device.get("isolated"):
            sim.erase(udid)  # a clean device every time: only the seeded demo account is on it
        log.info(f"booting {device['name']} ({udid[:8]}…)")
        sim.boot(udid)
        sim.status_bar(udid, spec["status_bar"])
        sim.install(udid, Path(build["path"]))
        account = spec["account"]
        if account.get("app_data"):
            sim.install_app_data(udid, spec_mod.resolve(film_dir, account["app_data"]))
        if account.get("media"):
            sim.addmedia(udid, [spec_mod.resolve(film_dir, m) for m in account["media"]])
        recorder = sim.start_recording(udid, movie, rec_log)
        recorder.wait_started(sleep=sleep)
        t0 = monotonic()
        for index, step in enumerate(spec["steps"]):
            wait = step["at"] - (monotonic() - t0)
            if wait > 0:
                sleep(wait)
            actual = monotonic() - t0
            ok, note = _perform(sim, tap_driver, udid, build, account, step)
            steps_done.append({"index": index, "id": step.get("id"), "do": step["do"], "at": step["at"],
                               "actual": round(actual, 4), "ok": ok, "note": note})
            if not ok:
                raise SlateError(f"Step {index} ({step['do']}) failed: {note}")
        remaining = spec["duration"] + SETTLE_S - (monotonic() - t0)
        if remaining > 0:
            sleep(remaining)
        recorder.stop()
    finally:
        if recorder is not None:
            recorder.stop()
        try:
            sim.clear_status_bar(udid)
        finally:
            if booted_here and not keep_booted:
                sim.shutdown(udid)
    if not movie.is_file() or movie.stat().st_size == 0:
        raise SlateError("simctl finished without writing the recording.", hint=f"See {rec_log}.")
    return finish(film_dir, spec, source=movie, command="slate capture ios",
                  extra={"ios": {"device": {k: device.get(k) for k in ("name", "udid", "runtime",
                                                                       "deviceTypeIdentifier", "isolated", "created")},
                                 "build": build, "status_bar": spec["status_bar"],
                                 "account": {k: v for k, v in spec["account"].items() if k != "env"} |
                                            {"env_keys": sorted((spec["account"].get("env") or {}).keys())},
                                 "tap_driver": tap_driver.name if tap_driver else None},
                         "steps": steps_done})


def _perform(sim: Simctl, driver: TapDriver | None, udid: str, build: dict, account: dict,
             step: dict) -> tuple[bool, str]:
    kind = step["do"]
    try:
        if kind == "launch":
            sim.launch(udid, build["bundle_id"], list(account.get("launch_args") or []) + list(step.get("args") or []),
                       dict(account.get("env") or {}))
        elif kind == "openurl":
            sim.openurl(udid, step["url"])
        elif kind == "terminate":
            sim.terminate(udid, build["bundle_id"])
        elif kind == "tap":
            if driver is None:
                return False, "no tap driver"
            driver.tap(udid, step["x"], step["y"])
        elif kind == "type":
            if driver is None:
                return False, "no tap driver"
            driver.type(udid, step["text"])
        return True, driver.name if (driver and kind in ("tap", "type")) else "ok"
    except SlateError as exc:
        return False, exc.message


def finish(film_dir: Path, spec: dict, *, source: Path | None, command: str, extra: dict | None = None,
           raw: Path | None = None) -> Path:
    """Conform, beat-map (when the spec anchors steps to words or places the capture) and write capture.json."""
    film = deps.load_film(film_dir)
    name = spec["name"]
    if raw is None:
        if source is None:
            raise UsageError("Nothing to conform: no recording and no frames.")
        raw = conform.to_sequence(source, film_dir, name, duration=spec["duration"], spec=spec)
    rec = {"schema": 1, "name": name, "kind": spec["kind"], "spec": public_spec(spec),
           "film": store.film_block(film),
           "source": _source_block(film_dir, source) if source else None,
           "ios": None, "web": None, "raw": conform.raw_block(film_dir, raw, film, spec),
           "frames": {"count": 0, "reason": "not placed yet"}, "steps": [], "taps": [], "retime": [], "beats": None,
           "misses": [], "replay": replay.fake_vendors(), "made_at": deps.now_iso(), "notes": []}
    rec.update(extra or {})
    store.save(film_dir, name, rec, command)
    if spec_mod.anchored_steps(spec) or spec.get("place"):
        try:
            beatmap.run(film_dir, name)
            return store.record_path(film_dir, name)
        except MissingPrerequisite as exc:
            rec["notes"].append(f"beat map waits for word timings: {exc.message} Run `slate capture beatmap "
                                f"--name {name}` once they exist.")
    rec["frames"] = conform.place(film_dir, name, fit=spec.get("fit", "native"))
    store.save(film_dir, name, rec, command)
    return store.record_path(film_dir, name)


def public_spec(spec: dict) -> dict:
    """The spec as capture.json keeps it: the demo account's environment values are left out (keys only)."""
    account = spec.get("account")
    if not isinstance(account, dict) or not account.get("env"):
        return spec
    return {**spec, "account": {**account, "env": {k: "(set)" for k in account["env"]}}}


def _source_block(film_dir: Path, source: Path) -> dict:
    info = media.probe(source)
    return {"file": deps.rel(film_dir, source), "sha256": jsonio.sha256(source), "width": info["width"],
            "height": info["height"], "codec": info.get("codec"), "duration": info.get("duration"),
            "frames": info.get("frames"), "vfr": bool(info.get("vfr"))}
