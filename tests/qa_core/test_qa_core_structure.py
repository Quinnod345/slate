"""schema, frames and events_complete on synthetic films."""

import json

import numpy as np
import pytest

from slate import jsonio, paths
from slate.qa.checks import structure, sync

from qa_core_helpers import moving_square, needs_ffmpeg, write_json


def _sub(result, name):
    return next(c for c in result.details["checks"] if c["check"] == name)


# ---------------------------------------------------------------------------------------------- events_complete
def test_events_complete_passes_when_every_act_has_beats(film_builder):
    fb = film_builder()
    fb.all_events()
    r = structure.events_complete(fb.path)
    assert r.status == "PASS", r.summary
    assert r.engine["name"] and r.engine["version"]
    assert set(r.inputs) >= {"timing/shots.json", "timing/visual_events/act1.json", "timing/visual_events/act2.json"}


def test_events_complete_fails_on_a_missing_act(film_builder):
    fb = film_builder(shots=[(1, 20), (2, 20), (3, 20)])
    fb.events(3, [fb.beat("S03", "land", 45)])
    r = structure.events_complete(fb.path)
    assert r.status == "FAIL"
    assert r.details["missing"] == [1, 2]
    assert "acts 1, 2" in r.summary
    # the expected-but-missing files are inputs (None), so the result goes STALE when they appear
    assert r.inputs["timing/visual_events/act1.json"] is None


def test_events_complete_holds_draws_and_counts_to_their_spans(film_builder):
    """t is always the beat: a draw or a count ends on it (start + dur, to half a frame), and gives its span; a whoosh
    or a swell peaks inside its own. slate.timing.beats.problems is the one check every engine and reader shares."""
    fb = film_builder(shots=[(1, 30)])
    t = fb.secs(20)
    fb.events(1, [fb.beat("S01", "line", 20, kind="draw")])  # a draw with no span: when did it start?
    r = structure.events_complete(fb.path)
    assert r.status == "FAIL" and any("line" in p for row in r.details["acts"] for p in row.get("problems", []))
    fb.events(1, [fb.beat("S01", "line", 20, kind="draw", start=round(t - 0.4, 6), dur=0.4)])  # ends on its beat
    assert structure.events_complete(fb.path).status == "PASS"
    fb.events(1, [fb.beat("S01", "line", 20, kind="draw", start=round(t - 0.6, 6), dur=0.4)])  # ends 0.2 s early
    assert structure.events_complete(fb.path).status == "FAIL"
    fb.events(1, [fb.beat("S01", "air", 20, kind="whoosh", start=round(t - 0.1, 6), dur=0.3)])  # peaks inside
    assert structure.events_complete(fb.path).status == "PASS"


def test_events_complete_accepts_silent_with_a_reason_only(film_builder):
    fb = film_builder()
    fb.events(1, [fb.beat("S01", "a", 3), fb.beat("S02", "b", 25)])
    fb.events(2, silent="a held title card; nothing moves")
    assert structure.events_complete(fb.path).status == "PASS"
    fb.events(2, silent="")
    r = structure.events_complete(fb.path)
    assert r.status == "FAIL" and r.details["invalid"] == [2]


@pytest.mark.parametrize("bad,why", [
    ("[]", "empty list"),
    ('[{"shot": "S03", "id": "x", "t": 1.5, "kind": "boing"}]', "unknown kind"),
    ('[{"shot": "S03", "id": "x", "t": 0.1, "kind": "land"}]', "outside the act"),
    ('[{"shot": "S01", "id": "x", "t": 1.5, "kind": "land"}]', "shot from another act"),
    ('[{"shot": "S03", "id": "x", "t": 1.5, "kind": "land"}, {"shot": "S03", "id": "x", "t": 1.6, "kind": "tap"}]',
     "duplicate id"),
    ('[{"shot": "S03", "id": "x", "kind": "land"}]', "missing t"),
    ('{"events": oops', "broken JSON"),
])
def test_events_complete_rejects_invalid_files(film_builder, bad, why):
    fb = film_builder()  # act 1: S01+S02 (frames 0–40), act 2: S03 (40–70) at 30 fps
    fb.events(1, [fb.beat("S01", "a", 3)])
    fb.events(2, raw=bad)
    r = structure.events_complete(fb.path)
    assert r.status == "FAIL", why
    assert r.details["invalid"] == [2], why


def test_events_complete_flags_orphan_files(film_builder):
    fb = film_builder()
    fb.all_events()
    fb.events(9, [fb.beat("S03", "z", 50)])
    r = structure.events_complete(fb.path)
    assert r.status == "FAIL" and r.details["orphans"] == ["timing/visual_events/act9.json"]


def test_events_complete_unchecked_without_timing(film_builder):
    fb = film_builder()
    (fb.path / "timing" / "shots.json").unlink()
    assert structure.events_complete(fb.path).status == "UNCHECKED"


def test_regression_missing_acts_fail_even_when_the_sound_record_says_sync_passed(film_builder):
    """Film C shipped with acts 1 and 2 missing their beats while its sync check passed: the check only measured the
    cues that existed. Here the sound engine's record says every anchor is on time, and still neither gate passes."""
    fb = film_builder(shots=[(1, 30), (2, 30), (3, 30)])
    fb.events(3, [fb.beat("S03", "land", 70)])
    write_json(fb.path / "sound" / "verify.json", {"sync": {"frame_ms": 1000 / 30, "anchors": 12, "max_abs_delta_ms": 0.5,
                                                            "over_one_frame": [], "pass": True}})
    ev = structure.events_complete(fb.path)
    assert ev.status == "FAIL" and ev.details["missing"] == [1, 2]
    s = sync.sync(fb.path)
    assert s.status == "FAIL"
    cov = _sub(s, "coverage")
    assert cov["status"] == "FAIL" and cov["missing"] == [1, 2]
    assert _sub(s, "effects")["status"] == "PASS"  # the record itself was fine; coverage is what fails


# ---------------------------------------------------------------------------------------------- schema
@pytest.fixture
def schemas(tmp_path, monkeypatch):
    """A controlled schema folder: a film schema and the real gates schema (other builders' schemas aside)."""
    d = tmp_path / "schemas"
    d.mkdir()
    (d / "gates.schema.json").write_text((paths.PACKAGE / "schemas" / "gates.schema.json").read_text())
    write_json(d / "film.schema.json", {"type": "object", "required": ["id", "fps_num", "fps_den"],
                                        "properties": {"fps_num": {"type": "integer", "minimum": 1}}})
    monkeypatch.setattr(paths, "SCHEMAS_DIR", d)
    monkeypatch.setattr(jsonio, "SCHEMAS_DIR", d)
    jsonio.schema.cache_clear()
    yield d
    jsonio.schema.cache_clear()


def test_schema_passes_only_when_every_file_has_a_schema(film_builder, schemas):
    fb = film_builder()
    r = structure.schema(fb.path)
    # film.json validates; timing/shots.json has no schema in this folder, so the gate can't pass
    assert r.status == "UNCHECKED"
    assert "timing/shots.json" in r.details["no_schema"]
    assert [v["file"] for v in r.details["valid"]] == ["film.json"]
    write_json(schemas / "shots.schema.json", {"type": "object", "required": ["shots", "total_frames"]})
    jsonio.schema.cache_clear()
    r = structure.schema(fb.path)
    assert r.status == "PASS", r.summary
    assert r.engine["name"] == "jsonschema"


def test_schema_fails_an_invalid_file(film_builder, schemas):
    fb = film_builder()
    write_json(schemas / "shots.schema.json", {"type": "object"})
    film = json.loads((fb.path / "film.json").read_text())
    film["fps_num"] = 0
    write_json(fb.path / "film.json", film)
    jsonio.schema.cache_clear()
    r = structure.schema(fb.path)
    assert r.status == "FAIL"
    assert r.details["invalid"][0]["file"] == "film.json"
    assert any("fps_num" in p for p in r.details["invalid"][0]["problems"])


def test_schema_validates_the_gate_report_with_the_gates_schema(film_builder, schemas):
    fb = film_builder()
    write_json(schemas / "shots.schema.json", {"type": "object"})
    jsonio.schema.cache_clear()
    write_json(fb.path / "qa" / "gates.json", {"gates": {"x": {"gate": "x", "status": "OK"}}})
    r = structure.schema(fb.path)
    assert r.status == "FAIL"
    assert r.details["invalid"][0]["file"] == "qa/gates.json"


def test_schema_stays_fresh_when_later_commands_record_their_work(film_builder, schemas):
    """The gate validates qa/gates.json and film.lock.json, but every later command writes them (the gate's own result
    goes into qa/gates.json), so they aren't its inputs: a delivery read just after `slate qa` found it STALE."""
    from slate import gates

    fb = film_builder()
    write_json(schemas / "shots.schema.json", {"type": "object"})
    write_json(schemas / "film_lock.schema.json", {"type": "object"})
    jsonio.schema.cache_clear()
    write_json(fb.path / "film.lock.json", {"schema": 1, "files": {}})
    r = structure.schema(fb.path)
    assert r.status == "PASS", r.summary
    assert {"qa/gates.json", "film.lock.json"}.isdisjoint(r.inputs) and "timing/shots.json" in r.inputs
    gates.write_report(fb.path, [r])
    write_json(fb.path / "film.lock.json", {"schema": 1, "files": {"masters/m.mov": {"producer": "slate master"}}})
    assert gates.load_report(fb.path)["schema"].status == "PASS"


def test_schema_files_can_be_claimed_by_x_slate_files(film_builder, schemas):
    fb = film_builder()
    write_json(schemas / "timing_shots_v1.schema.json", {"type": "object", "x-slate-files": ["timing/shots.json"],
                                                         "required": ["shots"]})
    jsonio.schema.cache_clear()
    r = structure.schema(fb.path)
    assert {"file": "timing/shots.json", "schema": "timing_shots_v1", "problems": []} in r.details["valid"]


# ---------------------------------------------------------------------------------------------- frames
@needs_ffmpeg
def test_frames_pass_with_exact_renders_and_master(film_builder):
    fb = film_builder()
    film = fb.render_all()
    fb.master(film)
    r = structure.frames(fb.path)
    assert r.status == "PASS", r.summary
    assert {c["check"] for c in r.details["checks"]} == {"shots", "renders", "masters"}
    assert r.engine["name"].startswith("ffprobe")


@needs_ffmpeg
def test_frames_fail_on_a_short_render(film_builder):
    fb = film_builder()
    fb.render(1, moving_square(39, fb.size))  # act 1 is 40 frames
    fb.render(2, moving_square(30, fb.size))
    r = structure.frames(fb.path)
    assert r.status == "FAIL"
    assert "39 frames, should have 40" in _sub(r, "renders")["summary"]


@needs_ffmpeg
def test_frames_fail_on_wrong_size_and_changed_file(film_builder):
    fb = film_builder()
    fb.render(1, moving_square(40, (64, 36)))
    p = fb.render(2, moving_square(30, fb.size))
    reg = json.loads((fb.path / "renders" / "registry.json").read_text())
    reg["entries"][1]["sha256"] = "0" * 64
    write_json(fb.path / "renders" / "registry.json", reg)
    r = structure.frames(fb.path)
    ev = " ".join(r.evidence)
    assert r.status == "FAIL" and "64×36" in ev and "sha256 differs" in ev


def test_frames_unchecked_without_renders_but_checks_shots(film_builder):
    fb = film_builder()
    r = structure.frames(fb.path)
    assert r.status == "UNCHECKED"
    assert _sub(r, "shots")["status"] == "PASS"


def test_frames_fail_on_a_gap_in_shots(film_builder):
    fb = film_builder()
    doc = json.loads((fb.path / "timing" / "shots.json").read_text())
    doc["shots"][1]["frame_in"] += 2
    write_json(fb.path / "timing" / "shots.json", doc)
    r = structure.frames(fb.path)
    assert r.status == "FAIL" and "gap" in _sub(r, "shots")["summary"]


def test_frames_fail_when_shots_and_film_disagree_on_the_clock(film_builder):
    fb = film_builder()
    doc = json.loads((fb.path / "timing" / "shots.json").read_text())
    doc["fps_num"] = 25
    write_json(fb.path / "timing" / "shots.json", doc)
    r = structure.frames(fb.path)
    assert r.status == "FAIL" and "one clock" in _sub(r, "clock")["summary"]


@needs_ffmpeg
def test_frames_fail_on_a_short_master_and_a_short_mix(film_builder):
    fb = film_builder()
    film = fb.render_all()
    fb.master(film[:-3])
    fb.wav(fb.MIX, np.zeros((48000 * 1, 2)))  # the picture is 70 frames = 2.33 s
    r = structure.frames(fb.path)
    assert r.status == "FAIL"
    assert _sub(r, "masters")["status"] == "FAIL"
    assert _sub(r, "mix_length")["status"] == "FAIL"


@needs_ffmpeg
def test_frames_fail_a_master_without_audio_when_the_film_has_a_mix(film_builder):
    from qa_core_helpers import tone

    fb = film_builder(shots=[(1, 12)])
    film = fb.render_all()
    fb.master(film)
    assert structure.frames(fb.path).status == "PASS"  # no mix yet: nothing to carry
    fb.mix({"dialogue": tone(fb.secs(fb.total))})
    r = structure.frames(fb.path)
    assert r.status == "FAIL" and "carries no audio" in _sub(r, "masters")["summary"]
