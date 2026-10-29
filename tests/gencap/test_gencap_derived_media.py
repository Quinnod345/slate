"""A variant reads its parent's media by reference and writes every revision into its own folder."""

import pytest
from PIL import Image

from gencap_helpers import run_json, write_timing
from slate import gates, jsonio, ledger
from slate.capture import beatmap, conform, ios, media, spec as capture_spec, store as captures
from slate.errors import Refused
from slate.gen import judge, plates, run, store
from slate.state import derive, lock


def _approved_still(film):
    run.run(film, "S04")
    judge.judge(film, "S04")
    judge.pick(film, "S04", take_id="t1", by="Tester")


def _parent_media(film):
    return {p.relative_to(film).as_posix(): jsonio.sha256(p)
            for folder in ("plates", "captures") for p in (film / folder).rglob("*") if p.is_file()}


def test_variant_conforms_approved_plate_at_its_size_without_changing_parent(film):
    _approved_still(film)
    before = _parent_media(film)
    child = derive.create(film, "vertical", aspect="9:16", git=False)
    rec = store.load_record(child, "S04")
    assert rec["picked"] == "t1"
    assert not store.record_path(child, "S04").exists()
    assert store.take_file(child, store.approved_take(rec)).is_relative_to(film)

    out = plates.conform(child, "S04", frames=2)

    assert out["provenance"]["approved_by"] == "Tester"
    assert media.sequence_size(child / out["frames"]["dir"]) == (180, 320)
    assert out["frames"]["count"] == 2
    assert _parent_media(film) == before


def test_variant_can_rejudge_locally_and_parent_change_stales_its_gate(film):
    _approved_still(film)
    before = _parent_media(film)
    child = derive.create(film, "vertical", aspect="9:16", git=False)
    judge.judge(child, "S04")
    judge.pick(child, "S04", take_id="t2", by="Child reviewer")

    assert store.record_path(child, "S04").is_file()
    assert store.load_record(child, "S04")["picked"] == "t2"
    assert store.load_record(film, "S04")["picked"] == "t1"
    assert _parent_media(film) == before
    assert gates.load_report(child)["gen_takes:S04"].status == "PASS"
    src = store.take_file(child, store.approved_take(store.load_record(child, "S04")))
    src.write_bytes(src.read_bytes() + b"changed")
    assert gates.load_report(child)["gen_takes:S04"].status == "STALE"
    with pytest.raises(Refused, match="changed on disk"):
        plates.conform(child, "S04", frames=1)


@pytest.mark.parametrize("local_metadata", [False, True])
def test_variant_never_operates_on_parent_vendor_requests(film, local_metadata):
    _approved_still(film)
    child = derive.create(film, "vertical", aspect="9:16", git=False)
    if local_metadata:
        judge.judge(child, "S04")
    before = _parent_media(film)
    rows = ledger.rows()
    for operation in (lambda: run.run(child, "S04", force=True),
                      lambda: run.takes(child, "S04", refresh=True),
                      lambda: run.cancel(child, shot_id="S04")):
        with pytest.raises(Refused, match="parent film"):
            operation()
    assert ledger.rows() == rows
    assert _parent_media(film) == before


def test_variant_inherits_legacy_generation_record_missing_from_lock(film):
    _approved_still(film)
    data = lock.load(film)
    data["files"].pop("plates/S04/generation.json")
    jsonio.write_atomic(film / "film.lock.json", data)
    child = derive.create(film, "vertical", aspect="9:16", git=False)

    assert store.load_record(child, "S04")["picked"] == "t1"
    assert plates.conform(child, "S04", frames=1)["frames"]["count"] == 1


def _capture_stills(film):
    spec = capture_spec.normalize({"kind": "web", "name": "screen", "duration": 0.12, "root": "site",
                                   "viewport": {"width": 64, "height": 64},
                                   "place": {"shot": "S01"}, "steps": []})
    shots_dir = captures.capture_dir(film, "screen") / "shots"
    shots_dir.mkdir(parents=True)
    shots = []
    for index in range(3):
        path = shots_dir / media.frame_name(index)
        Image.new("RGB", (64, 64), (50 + index * 20, 60, 70)).save(path)
        shots.append(path)
    jsonio.write_atomic(captures.spec_path(film, "screen"), spec)
    raw = conform.from_frames(shots, film, "screen", spec=spec)
    ios.finish(film, spec, source=None, raw=raw, command="slate capture web")


def test_variant_beatmaps_parent_raw_frames_and_reconforms_parent_screenshots(film):
    _capture_stills(film)
    before = _parent_media(film)
    child = derive.create(film, "vertical", aspect="9:16", git=False)
    write_timing(child)  # the child's new placement; no browser or recording is needed
    assert captures.load(child, "screen")["name"] == "screen"
    assert captures.read_dir(child, "screen", "raw") == captures.capture_dir(film, "screen") / "raw"

    out = beatmap.run(child, "screen")
    assert out["gate"]["status"] == "PASS"
    assert out["frames"]["count"] == 3
    assert captures.load(child, "screen")["film"]["id"] == "vertical"
    code, result, err = run_json(["capture", "conform", "--film", str(child), "--name", "screen"])
    assert code == 0, err
    assert result["raw"]["count"] == 3
    assert (captures.capture_dir(child, "screen") / "raw").is_dir()
    assert _parent_media(film) == before
