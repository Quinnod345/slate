"""Fixtures for the film-state tests.

The formats and brand modules are other builders' work, so these tests use small fakes of exactly the functions
docs/ARCHITECTURE.md §12 names (`slate.formats.load/names/safe_zones`, `slate.brand.pack.find/load/...`), backed by the
neutral fixture files in tests/state/fixtures. One test (test_deps_fallback) removes the fakes to prove film state
falls back to reading the data files directly.
"""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FORMATS = FIXTURES / "formats"
BRANDS = FIXTURES / "brands"
REFS = ("truth", "look_motion", "voice", "sound")


def _fake_formats() -> types.ModuleType:
    mod = types.ModuleType("slate.formats")

    def load(name: str) -> dict:
        path = FORMATS / f"{name}.json"
        if not path.is_file():
            from slate.errors import UsageError

            raise UsageError(f"No format called {name!r} (fake)")
        return json.loads(path.read_text())

    def names() -> list[str]:
        return sorted(p.stem for p in FORMATS.glob("*.json"))

    def safe_zones(fmt: dict, platform: str) -> dict:
        return (fmt.get("safe_zones") or {}).get(platform) or {}

    mod.load, mod.names, mod.safe_zones = load, names, safe_zones
    mod.FAKE = True
    return mod


def _fake_brand_pack(store: Path) -> types.ModuleType:
    mod = types.ModuleType("slate.brand.pack")

    def find(name: str) -> Path:
        path = store / name
        if not (path / "brand.json").is_file():
            from slate.errors import UsageError

            raise UsageError(f"No brand pack called {name!r} (fake)")
        return path

    def load(name: str) -> dict:
        return json.loads((find(name) / "brand.json").read_text())

    def missing_references(pack: dict) -> list[str]:
        refs = pack.get("references") or {}
        return [k for k in REFS if not (isinstance(refs.get(k), dict) and refs[k].get("approved_by"))]

    def references_approved(pack: dict) -> bool:
        return not missing_references(pack)

    def approved_claims(pack: dict) -> list[dict]:
        return list((pack.get("truth") or {}).get("approved_claims") or [])

    mod.find, mod.load, mod.missing_references = find, load, missing_references
    mod.references_approved, mod.approved_claims = references_approved, approved_claims
    mod.FAKE = True
    return mod


@pytest.fixture
def state_env(slate_home, monkeypatch):
    """Temp Slate folders, the fixture brand packs in the brand store, and fake formats/brand modules."""
    for pack in BRANDS.iterdir():
        shutil.copytree(pack, slate_home["brands"] / pack.name)
    monkeypatch.setitem(sys.modules, "slate.formats", _fake_formats())
    monkeypatch.setitem(sys.modules, "slate.brand.pack", _fake_brand_pack(slate_home["brands"]))
    from slate.state import deps, status

    deps._WARNED.clear()
    status.reset_command_cache()
    yield {**slate_home, "formats": FORMATS}
    status.reset_command_cache()


@pytest.fixture
def new_film(state_env):
    """Make a film quickly (no git unless asked): new_film('f1', mode='express', brand='onboarded-brand', ...)."""
    from slate.state import film

    def make(film_id: str = "f1", *, brand: str = "open-brand", format: str = "fixture-explainer",
             git: bool = False, **kw) -> Path:
        return film.create(film_id, brand=brand, format=format, git=git, **kw)

    return make


def write_script(film_dir: Path, shots: int = 3, acts: int = 1) -> Path:
    """A small script.json in the shape the films used (acts → shots with id, vo, on_screen, picture, source)."""
    data = {"title": "t", "version": "draft-1", "language": "en", "acts": []}
    n = 0
    for a in range(1, acts + 1):
        act = {"name": f"Act {a}", "job": "test", "shots": []}
        for _ in range(shots):
            n += 1
            act["shots"].append({"id": f"S{n:02d}", "vo": f"Line {n}.", "on_screen": f"Card {n}",
                                 "picture": "a card", "source": "fixture"})
        data["acts"].append(act)
    path = film_dir / "script" / "script.json"
    path.write_text(json.dumps(data, indent=1))
    return path


@pytest.fixture
def script_writer():
    """write_script as a fixture (test files never import conftest by name: other folders have one too)."""
    return write_script


# ---------------------------------------------------------------------------------------------------------------
# Fake stage commands for `slate make` (they write outputs and record them, like real modules must)

RUNS: list[str] = []


def _fake_cmds_module() -> types.ModuleType:
    mod = types.ModuleType("slate_state_test_cmds")

    def handler(name: str, producer: str, outputs: dict[str, str | bytes], inputs: list[str]):
        def run(args) -> int:
            from slate import cli, log
            from slate.state import lock

            film_dir = cli.film(args)
            RUNS.append(name)
            import hashlib

            seed = hashlib.sha256("".join((film_dir / i).read_text(errors="replace") if (film_dir / i).is_file()
                                          else "" for i in inputs).encode()).hexdigest()
            for rel, content in outputs.items():
                path = film_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                if rel.endswith(".json"):
                    body = json.loads(content) if isinstance(content, str) else {}
                    body = body if isinstance(body, dict) else {"items": body}
                    path.write_text(json.dumps({**body, "made_from": seed}))
                elif isinstance(content, bytes):
                    path.write_bytes(content + seed.encode())
                else:
                    path.write_text(content + seed)
                lock.record(film_dir, rel, producer=producer, inputs=[i for i in inputs if (film_dir / i).exists()])
            log.result({"ok": True, "node": name}, f"{name} ok")
            return 0

        return run

    def register(sub) -> None:
        from slate import cli

        if "script" not in sub.choices:
            grp = cli.add_group(sub, "script", "fake script")
            cli.add_command(grp, "build", handler("storyboard", "slate script build",
                                                  {"script/storyboard.json": "{}", "script/SCRIPT.md": "# s\n"},
                                                  ["script/script.json"]), "fake")
        if "voice" not in sub.choices:
            grp = cli.add_group(sub, "voice", "fake voice")
            cli.add_command(grp, "tts", handler("voice", "slate voice tts",
                                                {"voice/takes/S01.wav": b"RIFF", "voice/takes.json": "{}"},
                                                ["script/storyboard.json"]), "fake")
        cli.add_command(sub, "timing", handler("timing", "slate timing", {
            "timing/shots.json": "{}", "timing/vo_cues.json": "{}", "timing/vo_words.json": "{}",
            "timing/timeline.json": "{}"}, ["script/storyboard.json", "voice/takes.json"]), "fake")
        cli.add_command(sub, "emit-events", handler("events", "slate emit-events",
                                                    {"timing/visual_events/act1.json": "[]"},
                                                    ["timing/shots.json", "timing/timeline.json"]), "fake")
        if "render" not in sub.choices:
            cli.add_command(sub, "render", handler("render", "slate render",
                                                   {"renders/act1.mov": b"MOV", "renders/registry.json": '{"entries": []}'},
                                                   ["timing/shots.json", "timing/timeline.json",
                                                    "timing/visual_events/act1.json"]), "fake")
        if not getattr(mod, "NO_MIX", False):
            cli.add_command(sub, "mix", handler("mix", "slate mix", {"sound/mix/final_mix.wav": b"WAV"},
                                                ["timing/vo_cues.json", "timing/visual_events/act1.json",
                                                 "sound/score.wav"]), "fake")
        cli.add_command(sub, "master", handler("master", "slate master", {"masters/film.mov": b"MASTER"},
                                               ["renders/registry.json", "sound/mix/final_mix.wav"]), "fake")

        def qa_run(args) -> int:
            from slate import cli, gates, log

            film_dir = cli.film(args)
            RUNS.append("qa")
            r = gates.GateResult.pass_("fixture_gate", "fine",
                                       inputs=gates.inputs_for(film_dir, ["masters/film.mov"]))
            gates.write_report(film_dir, [r])
            log.result({"ok": True}, "qa ok")
            return 0

        if "qa" not in sub.choices:
            cli.add_command(sub, "qa", qa_run, "fake")
        cli.add_command(sub, "deliver", handler("deliver", "slate deliver", {"deliver/MANIFEST.json": "{}"},
                                                ["masters/film.mov", "qa/gates.json"]), "fake")

    mod.register = register
    return mod


@pytest.fixture
def fake_cli(state_env, monkeypatch):
    """The CLI with only film state and the fake stage commands registered (isolated from other modules)."""
    from slate import cli

    mod = _fake_cmds_module()
    monkeypatch.setitem(sys.modules, "slate_state_test_cmds", mod)
    monkeypatch.setattr(cli, "MODULES", [("slate.state.cli", "state"), ("slate_state_test_cmds", "fake stages")])
    RUNS.clear()
    from slate.state import status

    status.reset_command_cache()
    yield {"runs": RUNS, "module": mod}
    RUNS.clear()
    status.reset_command_cache()
