"""iOS captures: spec validation, the pinned build (refused on any other hash), the isolated Simulator, Apple's
status bar, the seeded demo account, and a full capture against a fake simctl. Nothing here boots a simulator;
the one real call is `xcrun simctl list`, which the brief allows."""

from __future__ import annotations

import os
import plistlib
import types

import pytest

from gencap_helpers import vfr_clip
from slate import gates, procs
from slate.capture import ios, media, spec as spec_mod
from slate.errors import MissingPrerequisite, Refused, UsageError


def make_app(folder, name="Demo", bundle_id="com.example.demo"):
    app = folder / f"{name}.app"
    app.mkdir(parents=True, exist_ok=True)
    with (app / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": "1.0", "CFBundleVersion": "7",
                       "CFBundleName": name}, fh)
    (app / name).write_bytes(b"\xcf\xfa\xed\xfe demo binary")
    (app / "Frameworks").mkdir(exist_ok=True)
    return app


def ios_spec(app_path, sha, **extra):
    spec = {"kind": "ios", "name": "save-entry", "duration": 3,
            "app": {"path": str(app_path), "bundle_id": "com.example.demo", "build_sha256": sha},
            "account": {"kind": "demo", "launch_args": ["-ui-testing", "-seed-demo-account"],
                        "env": {"DEMO_USER": "demo@example.com"}},
            "tap_driver": "scripted", "place": {"shot": "S01"},
            "steps": [{"at": 0, "do": "launch"},
                      {"at": 1.2, "do": "mark", "id": "tap1", "word": {"shot": "S01", "text": "tap"}}]}
    spec.update(extra)
    return spec


# ---------------------------------------------------------------------------------------------------------------
# Specs


def test_spec_validation_names_every_problem(tmp_path):
    good = ios_spec("Demo.app", "a" * 64)
    assert spec_mod.validate(good) == []
    cases = [
        ({**good, "account": None}, "account"),
        ({k: v for k, v in good.items() if k != "account"}, "account"),
        ({k: v for k, v in good.items() if k != "app"}, "app"),
        ({**good, "app": {**good["app"], "build_sha256": "abc"}}, "build_sha256"),
        ({**good, "steps": [{"at": 0, "do": "launch"}, {"at": 9, "do": "mark", "id": "late"}]}, "after the capture"),
        ({**good, "steps": [{"at": 0, "do": "launch"}, {"at": 1, "do": "mark", "word": {"shot": "S01", "text": "x"}}]},
         "needs an id"),
        ({**good, "steps": [{"at": 1, "do": "mark", "id": "m"}]}, "launches its app"),
        ({**good, "viewport": {"width": 100, "height": 100}}, "only web"),
        ({**good, "name": "Bad Name"}, "name"),
        ({**good, "account": {"kind": "personal", "launch_args": []}}, "account"),
        ({**good, "steps": [{"at": 0, "do": "tap", "id": "t"}]}, "x"),
    ]
    for spec, needle in cases:
        problems = spec_mod.validate(spec)
        assert problems and any(needle in p for p in problems), (needle, problems)
    web = {"kind": "web", "name": "w", "duration": 1, "root": "site", "viewport": {"width": 320, "height": 240},
           "app": {"path": "x", "build_sha256": "a" * 64}}
    assert any("only iOS" in p for p in spec_mod.validate(web))
    with pytest.raises(UsageError):
        spec_mod.normalize({"kind": "file", "name": "f", "duration": 1})  # a file capture needs its source


def test_defaults_are_apples_status_bar_and_the_iphone_17_pro():
    spec = spec_mod.normalize(ios_spec("Demo.app", "a" * 64))
    assert spec["device"] == "iPhone 17 Pro" and spec["isolate"] is True
    assert spec["status_bar"] == {"time": "9:41", "battery": 100, "battery_state": "charged", "wifi_bars": 3,
                                  "cell_bars": 4, "data_network": "wifi", "operator": ""}
    args = ios.status_bar_args(spec["status_bar"])
    for flag, value in (("--time", "9:41"), ("--batteryLevel", "100"), ("--wifiBars", "3"), ("--cellularBars", "4")):
        assert args[args.index(flag) + 1] == value


# ---------------------------------------------------------------------------------------------------------------
# The pinned build


def test_the_build_hash_is_stable_and_changes_with_the_build(tmp_path):
    app = make_app(tmp_path)
    first = ios.bundle_sha256(app)
    assert first == ios.bundle_sha256(app) and len(first) == 64
    os.symlink("Demo", app / "Frameworks" / "link")
    linked = ios.bundle_sha256(app)
    assert linked != first
    (app / "Demo").write_bytes(b"a different build")
    assert ios.bundle_sha256(app) != linked


def test_a_different_build_is_refused_before_anything_happens(film, tmp_path):
    app = make_app(film / "build")
    spec = spec_mod.normalize(ios_spec(app, "0" * 64))
    with pytest.raises(Refused) as exc:
        ios.check_build(film, spec)
    assert exc.value.exit_code == 1 and "different build" in exc.value.message
    assert exc.value.details["expected"] == "0" * 64

    class Boom(ios.Simctl):
        def run(self, *a, **k):
            raise AssertionError("nothing may run before the build is checked")

    with pytest.raises(Refused):
        ios.capture(film, ios_spec(app, "0" * 64), sim=Boom())
    assert not (film / "captures" / "save-entry").exists()  # nothing written either
    wrong_id = ios_spec(app, ios.bundle_sha256(app))
    wrong_id["app"]["bundle_id"] = "com.example.other"
    with pytest.raises(Refused):
        ios.check_build(film, spec_mod.normalize(wrong_id))


def test_a_missing_build_is_a_missing_prerequisite(film):
    with pytest.raises(MissingPrerequisite):
        ios.check_build(film, spec_mod.normalize(ios_spec(film / "nowhere.app", "a" * 64)))


# ---------------------------------------------------------------------------------------------------------------
# Devices and tap drivers


class FakeSimctl(ios.Simctl):
    """simctl without a simulator: records every call; the recording is a synthetic variable-frame-rate clip."""

    def __init__(self, devices, *, segments=None):
        self.calls: list[tuple] = []
        self._devices = devices
        self.segments = segments or [((200, 200, 200), 1.2), ((60, 110, 190), 0.8), ((50, 160, 80), 0.1)]
        self.recorder = None

    def run(self, *args, timeout=120, env=None, check=True):
        self.calls.append(("run", *args, env))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def devices(self):
        return [dict(d) for d in self._devices]

    def device_types(self):
        return [{"name": "iPhone 17 Pro", "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"}]

    def runtimes(self):
        return [{"identifier": "com.apple.CoreSimulator.SimRuntime.iOS-26-5", "platform": "iOS", "version": "26.5",
                 "isAvailable": True}]

    def create(self, name, device_type, runtime):
        self.calls.append(("create", name, device_type, runtime))
        return "NEW-UDID"

    def start_recording(self, udid, path, log_path):
        self.calls.append(("record", udid, str(path)))
        sim = self

        class Rec:
            stopped = False

            def wait_started(self, **kw):
                return None

            def stop(self):
                if not self.stopped:
                    self.stopped = True
                    vfr_clip(path, sim.segments, size=(60, 130))

        self.recorder = Rec()
        return self.recorder


USER_DEVICE = {"name": "iPhone 17 Pro", "udid": "USER-UDID", "state": "Shutdown",
               "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
               "runtime": "com.apple.CoreSimulator.SimRuntime.iOS-26-5"}


def test_an_isolated_device_is_made_from_the_named_one(film):
    spec = spec_mod.normalize(ios_spec("x.app", "a" * 64))
    sim = FakeSimctl([USER_DEVICE])
    dev = ios.resolve_device(sim, spec, "f1")
    assert dev["isolated"] and dev["created"] and dev["udid"] == "NEW-UDID" and dev["name"] == "slate-f1-save-entry"
    assert ("create", "slate-f1-save-entry", USER_DEVICE["deviceTypeIdentifier"], USER_DEVICE["runtime"]) in sim.calls
    again = FakeSimctl([USER_DEVICE, {**USER_DEVICE, "name": "slate-f1-save-entry", "udid": "MINE"}])
    assert ios.resolve_device(again, spec, "f1")["udid"] == "MINE"  # made once, reused
    alone = FakeSimctl([])
    assert ios.resolve_device(alone, spec, "f1")["created"]  # from the device type when no device has the name
    shared = spec_mod.normalize(ios_spec("x.app", "a" * 64, isolate=False))
    with pytest.raises(MissingPrerequisite):
        ios.resolve_device(FakeSimctl([]), shared, "f1")


def test_taps_need_a_driver(monkeypatch):
    monkeypatch.setattr(procs, "which", lambda tool: None)
    tapping = spec_mod.normalize(ios_spec("x.app", "a" * 64, tap_driver="auto",
                                          steps=[{"at": 0, "do": "launch"}, {"at": 1, "do": "tap", "id": "t", "x": 10,
                                                                             "y": 20}]))
    with pytest.raises(MissingPrerequisite) as exc:
        ios.resolve_tap_driver(tapping)
    assert "scripted" in exc.value.hint
    assert ios.resolve_tap_driver(spec_mod.normalize(ios_spec("x.app", "a" * 64))).name == "scripted"
    quiet = spec_mod.normalize(ios_spec("x.app", "a" * 64, tap_driver="auto", steps=[{"at": 0, "do": "launch"}]))
    assert ios.resolve_tap_driver(quiet) is None


@pytest.mark.skipif(not procs.which("xcrun"), reason="Xcode's command-line tools aren't installed")
def test_simctl_list_runs_and_parses():
    devices = ios.Simctl().devices()  # `xcrun simctl list devices available -j`: reads, never boots
    assert isinstance(devices, list)
    assert all("udid" in d and "runtime" in d for d in devices)


# ---------------------------------------------------------------------------------------------------------------
# A whole capture against the fake


def test_a_capture_end_to_end_without_a_simulator(film):
    app = make_app(film / "build")
    spec = ios_spec(app, ios.bundle_sha256(app))
    sim = FakeSimctl([USER_DEVICE])
    now = {"t": 100.0}
    path = ios.capture(film, spec, sim=sim, monotonic=lambda: now["t"],
                       sleep=lambda s: now.__setitem__("t", now["t"] + s))
    rec = __import__("json").loads(path.read_text())
    runs = [c[1:] for c in sim.calls if c[0] == "run"]
    verbs = [r[0] for r in runs]
    assert verbs[:2] == ["erase", "boot"] and "USER-UDID" not in str(runs)  # the user's own device is never touched
    override = next(r for r in runs if r[0] == "status_bar" and r[2] == "override")
    assert "--time" in override and "9:41" in override
    launch = next(r for r in runs if r[0] == "launch")
    assert "-ui-testing" in launch and "-seed-demo-account" in launch and "com.example.demo" in launch
    assert launch[-1] == {"SIMCTL_CHILD_DEMO_USER": "demo@example.com"}  # the demo account reaches the app
    assert verbs[-2:] == ["status_bar", "shutdown"]  # cleared and shut down: nothing left running
    assert sim.recorder.stopped
    assert rec["ios"]["device"]["isolated"] and rec["ios"]["build"]["match"]
    assert rec["ios"]["account"]["env_keys"] == ["DEMO_USER"] and "env" not in rec["ios"]["account"]
    assert "demo@example.com" not in path.read_text()  # the account's values stay in the spec, not the record
    assert [s["actual"] for s in rec["steps"]] == [0.0, 1.2]
    assert rec["raw"]["count"] == 75  # 3 s at 25 fps, the last screen held after simctl's last frame
    assert rec["frames"]["count"] >= rec["raw"]["count"] and rec["frames"]["film_in"] == 0
    tap = rec["taps"][0]
    assert tap["exact"] and tap["error_frames"] == 0  # the tap lands on "tap"
    assert gates.load_report(film)["capture_beats:save-entry"].status == "PASS"
    assert media.count_frames(film / rec["frames"]["dir"]) == rec["frames"]["count"]


def test_a_failed_step_still_cleans_up(film):
    app = make_app(film / "build")

    class Failing(FakeSimctl):
        def run(self, *args, timeout=120, env=None, check=True):
            if args and args[0] == "launch":
                from slate.errors import SlateError

                raise SlateError("launch failed")
            return super().run(*args, timeout=timeout, env=env, check=check)

    sim = Failing([USER_DEVICE])
    with pytest.raises(Exception):
        ios.capture(film, ios_spec(app, ios.bundle_sha256(app)), sim=sim, sleep=lambda s: None)
    verbs = [c[1] for c in sim.calls if c[0] == "run"]
    assert verbs[-1] == "shutdown" and sim.recorder.stopped
