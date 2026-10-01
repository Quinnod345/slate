"""The Python bridge to the Node engine: finding the engine, calling it, and mapping its errors onto Slate's."""

from __future__ import annotations

import json
import os
import shutil
import textwrap
from pathlib import Path

import pytest

from slate import config, paths
from slate.engine import bridge
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, UsageError

ENGINE = paths.ENGINE_SRC
FIXTURE = ENGINE / "tests" / "core" / "fixtures" / "film"


def fake_engine(tmp_path: Path, body: str) -> Path:
    """An engine folder whose cli.mjs is `body` (it sees the command as process.argv[2] and args as argv[4])."""
    folder = tmp_path / "fake-engine"
    folder.mkdir()
    (folder / "cli.mjs").write_text(textwrap.dedent(body), encoding="utf-8")
    return folder


REPLY = """
const cmd = process.argv[2];
const args = process.argv[3] === '--args-file'
  ? JSON.parse((await import('node:fs')).readFileSync(process.argv[4], 'utf8'))
  : JSON.parse(process.argv[4] ?? '{}');
const replies = {
  usage: {ok: false, kind: 'usage', error: 'bad call', hint: 'call it right'},
  refused: {ok: false, kind: 'refused', error: 'gate says no'},
  missing: {ok: false, kind: 'missing_prerequisite', error: 'no browser', hint: 'node cli.mjs browser-ensure'},
  busy: {ok: false, kind: 'busy', error: 'a render holds the slot'},
  crash: {ok: false, kind: 'error', error: 'TypeError: x is undefined', details: {stack: ['at y']}},
  old: {ok: true, protocol: 0},
  stopped: {ok: false, kind: 'stopped', error: `took longer than ${process.env.SLATE_ENGINE_DEADLINE_S} s and was stopped`, hint: 'smaller ranges'},
  signalled: {ok: false, kind: 'stopped', error: 'stopped by SIGTERM'},
};
if (cmd === 'silent') { process.stderr.write('boom\\n'); process.exit(7); }
if (cmd === 'slow') { await new Promise((r) => setTimeout(r, 5000)); }
const reply = replies[cmd] ?? {ok: true, protocol: 1, echo: args, argv: process.argv.slice(3, 4), browser: process.env.BROWSER, data: process.env.SLATE_DATA, deadline: process.env.SLATE_ENGINE_DEADLINE_S ?? null};
process.stderr.write('progress line\\n');
process.stdout.write(JSON.stringify(reply) + '\\n');
process.exitCode = reply.ok ? 0 : ({usage: 2, missing_prerequisite: 3, busy: 5, stopped: 130}[reply.kind] ?? 1);
"""


@pytest.fixture
def fake(tmp_path, monkeypatch, slate_home):
    folder = fake_engine(tmp_path, REPLY)
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(folder))
    return folder


# --- finding the engine --------------------------------------------------------------------------------------------

def test_engine_dir_falls_back_to_the_repo_in_development(slate_home, monkeypatch):
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    assert bridge.engine_dir() == ENGINE
    assert bridge.engine_source() == "repo"


def test_engine_dir_prefers_the_installed_engine_for_the_pinned_version(slate_home, monkeypatch):
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    installed = paths.engine_home(config.engine_version())
    installed.mkdir(parents=True)
    (installed / "cli.mjs").write_text("// installed\n")
    # packages missing: not complete, so the repo still runs
    assert bridge.engine_dir() == ENGINE
    (installed / "node_modules").symlink_to(ENGINE / "node_modules")
    assert bridge.engine_dir() == installed
    assert bridge.engine_source() == "installed"


def test_engine_dir_env_override_must_be_an_engine(slate_home, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(tmp_path))
    with pytest.raises(MissingPrerequisite, match="has no cli.mjs"):
        bridge.engine_dir()


def test_engine_dir_says_how_to_install_when_there_is_none(slate_home, monkeypatch):
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    monkeypatch.setattr(paths, "ENGINE_SRC", slate_home["tmp"] / "no-engine")
    with pytest.raises(MissingPrerequisite) as info:
        bridge.engine_dir()
    assert "slate engine install --npm-ci" in info.value.hint


# --- calling it ----------------------------------------------------------------------------------------------------

def test_version_from_the_real_engine(slate_home, monkeypatch):
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    v = bridge.call("version", {}, timeout=120)
    assert v["ok"] is True and v["protocol"] == bridge.PROTOCOL
    assert v["installed"]["remotion"] == config.engine_version()
    assert set(v["commands"]) >= {"version", "browser-ensure", "entry", "bundle", "render", "stills", "emit-events",
                                  "check", "web-render"}


def test_call_passes_args_and_runs_the_child_without_a_browser(fake, monkeypatch):
    monkeypatch.setenv("BROWSER", "open")
    res = bridge.call("anything", {"act": 2, "skip": None})
    assert res["echo"] == {"act": 2}  # None values are dropped
    assert res["browser"] == "none"
    assert res["data"] == str(paths.data())


@pytest.mark.parametrize("command, error, code", [
    ("usage", UsageError, 2),
    ("refused", Refused, 1),
    ("missing", MissingPrerequisite, 3),
    ("busy", Busy, 5),
    ("crash", bridge.EngineError, 1),
])
def test_engine_errors_map_onto_slate_errors(fake, command, error, code):
    with pytest.raises(error) as info:
        bridge.call(command, {})
    assert info.value.exit_code == code
    assert info.value.details["engine_command"] == command


def test_engine_error_keeps_the_hint(fake):
    with pytest.raises(MissingPrerequisite) as info:
        bridge.call("missing", {})
    assert info.value.message == "no browser"
    assert info.value.hint == "node cli.mjs browser-ensure"


def test_no_answer_is_an_engine_error_with_the_output_tail(fake):
    with pytest.raises(bridge.EngineError) as info:
        bridge.call("silent", {})
    assert "gave no answer (exit 7)" in info.value.message
    assert "boom" in info.value.details["output_tail"]


def test_an_engine_on_another_protocol_is_refused(fake):
    with pytest.raises(MissingPrerequisite, match="speaks protocol 0"):
        bridge.call("old", {})


def test_a_slow_engine_is_stopped_at_the_timeout(fake):
    with pytest.raises(SlateError, match="took longer than"):
        bridge.call("slow", {}, timeout=1)


def test_the_engine_gets_a_deadline_inside_the_timeout(fake):
    assert bridge.call("anything", {}, timeout=100)["deadline"] == "90"
    assert bridge.call("anything", {})["deadline"] is None  # no timeout, no deadline
    assert bridge.deadline_for(900) == 890.0  # at most 10 s of grace
    assert bridge.deadline_for(20) == 18.0  # a tenth of the timeout
    assert bridge.deadline_for(1) == 0.75  # at least a quarter second
    assert bridge.deadline_for(0) is None and bridge.deadline_for(None) is None


def test_an_engine_that_stopped_itself_says_so_in_the_callers_terms(fake):
    with pytest.raises(bridge.Stopped) as info:
        bridge.call("stopped", {}, timeout=100)
    assert info.value.exit_code == 1 and info.value.kind == "stopped"
    assert info.value.message == ("The engine's stopped took longer than its 100 s timeout and stopped itself; "
                                  "nothing half-made was left behind.")
    assert info.value.hint == "smaller ranges"
    with pytest.raises(bridge.Stopped, match="was stopped by SIGTERM; nothing half-made was left behind"):
        bridge.call("signalled", {})


def test_large_args_travel_in_a_file(fake):
    big = {"notes": "x" * (bridge.ARGS_FILE_OVER + 10)}
    res = bridge.call("anything", big)
    assert res["argv"] == ["--args-file"]
    assert res["echo"] == big
    assert not list((paths.cache_dir() / "engine-args").glob("*.json"))  # cleaned up


# --- preparing a film ----------------------------------------------------------------------------------------------

def _film(slate_home, name="film", vo=None) -> Path:
    dest = slate_home["films"] / name
    shutil.copytree(FIXTURE, dest)
    if vo:
        shots = json.loads((dest / "timing" / "shots.json").read_text())
        shots["shots"][0]["vo"] = vo
        (dest / "timing" / "shots.json").write_text(json.dumps(shots))
    return dest


def test_call_resolves_the_film_and_passes_its_brand_pack(fake, slate_home):
    brand = slate_home["brands"] / "engine-fixture-brand"
    brand.mkdir()
    (brand / "brand.json").write_text('{"name": "engine-fixture-brand"}')
    film = _film(slate_home)
    rel = os.path.relpath(film)
    res = bridge.call("anything", {"film": rel})
    assert res["echo"]["film"] == str(film.resolve())
    assert Path(res["echo"]["brand_path"]).resolve() == brand.resolve()


def test_prepare_writes_the_spoken_forms_of_numbers_and_symbols(slate_home):
    pytest.importorskip("slate.timing.spoken")
    film = _film(slate_home, vo="664 people & 54% of them")
    bridge.prepare(film)
    spoken = json.loads((film / ".slate" / "spoken.json").read_text())
    assert set(spoken) == {"664", "&", "54%"}
    assert ["and"] in spoken["&"]
    assert all(isinstance(form, list) for form in spoken["664"])
    before = (film / ".slate" / "spoken.json").stat().st_mtime_ns
    bridge.prepare(film)  # unchanged: not rewritten (keeps the bundle hash stable)
    assert (film / ".slate" / "spoken.json").stat().st_mtime_ns == before


def test_prepare_adds_the_heard_numbers_so_a_script_word_can_find_them(slate_home):
    """A script says "twelve", Whisper wrote "12": the kit's clock needs the heard word's spoken forms to match it."""
    pytest.importorskip("slate.timing.spoken")
    film = _film(slate_home, vo="twelve people")
    (film / "timing" / "vo_words.json").write_text(json.dumps({"shots": {"S01": {
        "line": "twelve people", "words": [{"w": "12", "start": 0.0, "end": 0.3}, {"w": "people.", "start": 0.3, "end": 0.7}]}}}))
    bridge.prepare(film)
    spoken = json.loads((film / ".slate" / "spoken.json").read_text())
    assert ["twelve"] in spoken["12"]
    assert "people" not in spoken  # plain words say themselves: nothing to add


def test_prepare_writes_the_languages_match_rules_for_the_kit(slate_home):
    pytest.importorskip("slate.timing.spoken")
    from slate.timing import spoken, words

    film = _film(slate_home)
    bridge.prepare(film)
    rules = json.loads((film / ".slate" / "lang.json").read_text())
    assert rules["lang"] == "en"
    assert rules["keep"] == spoken.table("en")["normalize"]["keep"]
    assert set(rules["keep"]) <= set(rules["strip_keep"])
    assert "-" in rules["negative_marks"]
    assert rules["max_run"] == words.MAX_RUN
    before = (film / ".slate" / "lang.json").stat().st_mtime_ns
    bridge.prepare(film)  # unchanged: not rewritten
    assert (film / ".slate" / "lang.json").stat().st_mtime_ns == before


def test_call_resolves_every_path_from_where_the_caller_stands(fake, slate_home, tmp_path, monkeypatch):
    """The engine runs from its own folder, so a relative path is made absolute here, from the caller's folder: the
    browser, and a web scene's `scene` and `frames_dir` too (engine/cli.mjs promises the same for every path arg)."""
    monkeypatch.chdir(tmp_path)
    res = bridge.call("anything", {"browser": "bin/chrome", "scene": "my-scene", "frames_dir": "frames"})
    assert res["echo"]["browser"] == str((tmp_path / "bin" / "chrome").resolve())
    assert res["echo"]["scene"] == str((tmp_path / "my-scene").resolve())
    assert res["echo"]["frames_dir"] == str((tmp_path / "frames").resolve())
    assert set(bridge.PATH_ARGS) >= {"film", "out", "out_dir", "entry", "serve_url", "brand_path", "browser", "scene",
                                     "frames_dir"}


def test_the_engine_keeps_its_temp_folders_under_slates_data_folder(tmp_path, monkeypatch, slate_home):
    """Headless Chrome's profiles live where no other tool's temp cleanup reaches them: TMPDIR for the engine is
    <Slate data>/cache/tmp (another session's $TMPDIR sweep once deleted a live Slate Chrome's profile)."""
    folder = fake_engine(tmp_path, """
        import os from 'node:os';
        process.stdout.write(JSON.stringify({ok: true, protocol: 1, tmpdir: os.tmpdir(), env: process.env.TMPDIR}) + '\\n');
    """)
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(folder))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "system-temp"))
    res = bridge.call("anything", {})
    want = paths.cache_dir() / "tmp"
    assert res["env"] == str(want) and Path(res["tmpdir"]) == want and want.is_dir()


def test_a_try_later_from_the_engine_exits_75_so_the_queue_retries(tmp_path, monkeypatch, slate_home):
    from slate import errors

    try_later = getattr(errors, "TryLater", None)
    if try_later is None:
        pytest.skip("slate.errors has no TryLater yet (the foundation's)")
    folder = fake_engine(tmp_path, """
        process.stdout.write(JSON.stringify({ok: false, kind: 'try_later', error: 'a download was rate limited'}) + '\\n');
        process.exitCode = 75;
    """)
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(folder))
    with pytest.raises(try_later) as info:
        bridge.call("anything", {})
    assert info.value.exit_code == 75


# --- derived films: what the engine reads may live in the parent, by reference ---------------------------------------

def _derived(slate_home) -> tuple[Path, Path]:
    """A parent film and a child that reuses the parent's timing and one capture by reference (nothing copied)."""
    lock = pytest.importorskip("slate.state.lock")
    from slate import jsonio

    parent = _film(slate_home, vo="664 people & 54% of them")
    cap = parent / "captures" / "demo" / "frames"
    cap.mkdir(parents=True)
    (cap / "f00001.jpg").write_bytes(b"\xff\xd8 not really a jpeg")
    child = slate_home["films"] / "child-aspect"
    child.mkdir()
    film_json = json.loads((parent / "film.json").read_text())
    (child / "film.json").write_text(json.dumps({**film_json, "id": "child-aspect", "parent": film_json["id"]}))
    refs = [{"path": rel, "parent_path": rel, "sha256": jsonio.sha256(parent / rel), "kind": "file"}
            for rel in ("timing/shots.json", "captures/demo/frames/f00001.jpg")]
    lock.add_refs(child, refs, parent_id=film_json["id"], parent_dir=parent)
    return parent, child


def test_a_derived_film_reads_its_parents_timing_and_captures_through_the_lock(slate_home):
    parent, child = _derived(slate_home)
    assert bridge.refs_map(child) == {"timing/shots.json": str((parent / "timing" / "shots.json").resolve()),
                                      "captures": str((parent / "captures").resolve())}
    bridge.prepare(child)
    assert json.loads((child / ".slate" / "refs.json").read_text())["timing/shots.json"].endswith("timing/shots.json")
    if (child / ".slate" / "spoken.json").exists():  # the parent's reads are the child's reads
        assert "664" in json.loads((child / ".slate" / "spoken.json").read_text())
    # once the child has its own copy, it stops reading the parent's
    (child / "timing").mkdir()
    shutil.copy(parent / "timing" / "shots.json", child / "timing" / "shots.json")
    shutil.copytree(parent / "captures", child / "captures")
    bridge.prepare(child)
    assert not (child / ".slate" / "refs.json").exists()


def test_a_film_with_its_own_files_has_no_references(slate_home):
    film = _film(slate_home)
    assert bridge.refs_map(film) == {}
    bridge.prepare(film)
    assert not (film / ".slate" / "refs.json").exists()


def test_the_engine_builds_a_derived_films_entry_from_its_parents_timing(slate_home, monkeypatch):
    """Through the real engine: the child film has no timing of its own, and renders from its parent's by reference."""
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    parent, child = _derived(slate_home)
    res = bridge.call("entry", {"film": str(child)}, timeout=120)
    assert [(a["act"], a["frames"]) for a in res["acts"]] == [(1, 30)]
    data_ts = (child / ".slate" / "film-data.ts").read_text()
    shots_import = os.path.relpath(parent / "timing" / "shots.json", child / ".slate")
    assert f"import shotsJson from '{shots_import}';" in data_ts
    assert not (child / "timing" / "shots.json").exists()  # nothing was copied
