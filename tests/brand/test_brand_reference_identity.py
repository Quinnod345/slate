"""Retain measured reference provenance across basename collisions, versions and deliberate reanalysis.

Every analysis and video is a neutral filesystem fixture. Automatic analysis and media probing are scripted; no
child process, media decoder, model, vendor or network operation is part of these tests.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from slate import jsonio
from slate.brand import onboard

A = [0.2, 0.8, 0.3, 1.0]
B = [0.45, 0.1, 0.7, 0.9]


def analysis(path, digest, *, curve=A, duration=0.5, name="move-01", full=True):
    video = {"name": "preview.mp4", "codec": "neutral fixture"}
    if full:
        video.update(path=str(path), sha256=digest)
    return {"video": video, "eases": {"moves": [{
        "name": name, "bezier": curve, "rms": 0.006 if curve == A else 0.024,
        "duration_s": duration, "note": "retained source note",
        "lineage": {"video": str(path) if full else "preview.mp4", "window_s": [2.0, 2.0 + duration],
                    "tool": "neutral fitted fixture", "sampling": {"count": 9, "axis": "x"}},
    }]}}


def write_analysis(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def stored(folder):
    return jsonio.read(folder / onboard.LOOK_INPUTS)["measured"]


def by_video(measured, path, digest):
    matches = [(key, value) for key, value in measured.items()
               if value.get("source", {}).get("measurement_identity", {}).get("reference") ==
               {"kind": "video", "path": str(path), "sha256": digest}]
    assert len(matches) == 1
    return matches[0]


def assert_source(value, data, analysis_file):
    source = value["source"]
    move = data["eases"]["moves"][0]
    assert source["video"] == data["video"]
    assert source["measurement"] == move["lineage"]
    assert source["analysis"] == {"file": str(Path(analysis_file).resolve()), "data_sha256": jsonio.sha256_obj(data)}
    assert source["kind"] == "measured"
    assert value["bezier"] == move["bezier"] and value["duration_s"] == move["duration_s"]
    assert value["fit_rms"] == move["rms"] and move["note"] in value["lineage"]


def reference_item(result):
    return next(item for item in result["items"] if item["id"] == "reference_films")


def test_explicit_same_basename_keeps_both_values_and_full_provenance(fresh, tmp_path):
    first_path = tmp_path / "shared folder" / "first reference" / "preview.mp4"
    second_path = tmp_path / "shared folder" / "second reference" / "preview.mp4"
    one = analysis(first_path, "01" * 32)
    two = analysis(second_path, "02" * 32, curve=B, duration=1.5)
    files = [write_analysis(tmp_path / "one.analysis.json", one),
             write_analysis(tmp_path / "two.analysis.json", two)]
    original_bytes = [Path(file).read_bytes() for file in files]
    result = onboard.look("fresh", measured=files)
    measured = stored(fresh)
    assert len(measured) == 2
    first_key, first = by_video(measured, first_path, "01" * 32)
    second_key, second = by_video(measured, second_path, "02" * 32)
    assert first_key == "preview-move-01" and second_key != first_key
    assert_source(first, one, files[0])
    assert_source(second, two, files[1])
    assert reference_item(result)["done"]
    assert reference_item(result)["evidence"] == "2 move(s) from 2 film(s)"
    assert [Path(file).read_bytes() for file in files] == original_bytes
    assert not jsonio.validate(jsonio.read(fresh / "brand.json"), "brand")


def test_same_source_version_reanalysis_updates_existing_key(fresh, tmp_path):
    path = tmp_path / "reference" / "preview.mp4"
    one = analysis(path, "03" * 32)
    two = analysis(path, "03" * 32, curve=B, duration=0.9)
    first_file = write_analysis(tmp_path / "first.json", one)
    second_file = write_analysis(tmp_path / "reanalysis.json", two)
    onboard.look("fresh", measured=[first_file])
    initial_key, _ = by_video(stored(fresh), path, "03" * 32)
    onboard.look("fresh", measured=[second_file])
    measured = stored(fresh)
    key, value = by_video(measured, path, "03" * 32)
    assert len(measured) == 1 and key == initial_key
    assert_source(value, two, second_file)


def test_distinct_versions_survive_rerun_without_counting_one_path_as_two_films(fresh, tmp_path):
    path = tmp_path / "reference" / "preview.mp4"
    files = [write_analysis(tmp_path / "first-version.json", analysis(path, "04" * 32)),
             write_analysis(tmp_path / "second-version.json", analysis(path, "05" * 32, curve=B, duration=1.5))]
    result = onboard.look("fresh", measured=files)
    before = stored(fresh)
    assert len(before) == 2
    assert not reference_item(result)["done"]
    assert reference_item(result)["evidence"] == "2 move(s) from 1 film(s)"
    onboard.look("fresh", measured=files)
    assert stored(fresh) == before
    assert by_video(before, path, "04" * 32)[1]["duration_s"] == 0.5
    assert by_video(before, path, "05" * 32)[1]["duration_s"] == 1.5


def test_natural_suffix_key_is_retained_with_its_original_contents(fresh, tmp_path):
    path = tmp_path / "reference" / "preview.mp4"
    literal = "preview-move-01-2"
    legacy = {"bezier": B, "duration_s": 2.1, "lineage": "original annotation stays intact",
              "source": {"kind": "measured", "film": "recorded-reference", "where": "original recorded source"}}
    jsonio.write_atomic(fresh / onboard.LOOK_INPUTS, {"sources": {}, "measured": {literal: legacy}, "pending": []})
    files = [write_analysis(tmp_path / "first.json", analysis(path, "06" * 32)),
             write_analysis(tmp_path / "second.json", analysis(tmp_path / "other" / "preview.mp4", "07" * 32, curve=B))]
    onboard.look("fresh", measured=files)
    measured = stored(fresh)
    assert len(measured) == 3 and measured[literal] == legacy
    assert by_video(measured, path, "06" * 32)[0] == "preview-move-01"
    assert by_video(measured, tmp_path / "other" / "preview.mp4", "07" * 32)[0] not in {literal, "preview-move-01"}


@pytest.mark.parametrize("legacy_source", [
    {"kind": "measured", "film": "preview.mp4", "where": "original source"},
    {"kind": "measured", "film": "/recorded/reference/preview.mp4", "where": "original source"},
    {"kind": "measured", "film": "/recorded/reference/preview.mp4", "where": "original source",
     "reference": {"path": "/recorded/reference/preview.mp4", "sha256": "08" * 32}},
])
def test_legacy_metadata_is_never_replaced_by_guessed_identity(legacy_source, fresh, tmp_path):
    requested = "preview-move-01"
    legacy = {"bezier": B, "duration_s": 2.3, "lineage": "keep these exact original words",
              "source": copy.deepcopy(legacy_source), "annotation": {"owner": "fixture", "keep": True}}
    jsonio.write_atomic(fresh / onboard.LOOK_INPUTS, {"sources": {}, "measured": {requested: legacy}, "pending": []})
    file = write_analysis(tmp_path / "analysis.json", analysis(Path("/recorded/reference/preview.mp4"), "08" * 32))
    onboard.look("fresh", measured=[file])
    measured = stored(fresh)
    assert len(measured) == 2 and measured[requested] == legacy
    new_key, _ = by_video(measured, "/recorded/reference/preview.mp4", "08" * 32)
    assert new_key != requested
    onboard.look("fresh", measured=[file])
    assert stored(fresh) == measured


def test_duplicate_names_in_one_analysis_retain_occurrences_and_stable_rerun(fresh, tmp_path):
    path = tmp_path / "reference" / "preview.mp4"
    data = analysis(path, "09" * 32)
    data["eases"]["moves"].append(analysis(path, "09" * 32, curve=B, duration=1.2)["eases"]["moves"][0])
    file = write_analysis(tmp_path / "analysis.json", data)
    onboard.look("fresh", measured=[file])
    before = stored(fresh)
    assert len(before) == 2
    assert {value["duration_s"] for value in before.values()} == {0.5, 1.2}
    assert {value["source"]["measurement_identity"]["occurrence"] for value in before.values()} == {1, 2}
    assert {tuple(value["bezier"]) for value in before.values()} == {tuple(A), tuple(B)}
    onboard.look("fresh", measured=[file])
    assert stored(fresh) == before


def test_duplicate_move_does_not_claim_a_later_literal_suffix_name(fresh, tmp_path):
    path = tmp_path / "reference" / "preview.mp4"
    data = analysis(path, "0a" * 32, name="foo")
    data["eases"]["moves"] += [
        analysis(path, "0a" * 32, curve=B, duration=1.2, name="foo")["eases"]["moves"][0],
        analysis(path, "0a" * 32, curve=A, duration=2.4, name="foo-2")["eases"]["moves"][0],
    ]
    file = write_analysis(tmp_path / "analysis.json", data)
    onboard.look("fresh", measured=[file])
    measured = stored(fresh)
    assert len(measured) == 3
    literal = measured["preview-foo-2"]
    assert literal["duration_s"] == 2.4 and literal["bezier"] == A
    assert literal["source"]["measurement_identity"]["move"] == "foo-2"
    duplicate = next(value for value in measured.values()
                     if value["source"]["measurement_identity"]["move"] == "foo"
                     and value["source"]["measurement_identity"]["occurrence"] == 2)
    assert duplicate["duration_s"] == 1.2 and duplicate["bezier"] == B
    assert_source(measured["preview-foo"], data, file)
    onboard.look("fresh", measured=[file])
    assert stored(fresh) == measured


def fake_automatic(tmp_path, monkeypatch, *, full):
    import slate.analyze as analyze
    from slate.brand import media

    written = {}

    def fake_run(video, **kwargs):
        video = Path(video)
        assert kwargs["steps"] == ["cuts", "eases"]
        digest = jsonio.sha256(video)
        curve = A if video.read_bytes() == b"first reference" else B
        duration = 0.5 if curve == A else 1.5
        data = analysis(video.resolve(), digest, curve=curve, duration=duration, full=full)
        out = tmp_path / "analyses" / digest
        file = write_analysis(out / "analysis.json", data)
        written[digest] = (file, data)
        return {"out": str(out)}

    monkeypatch.setattr(analyze, "run", fake_run)
    monkeypatch.setattr(media, "probe", lambda path: {"duration_s": 15.0})
    return written


@pytest.mark.parametrize("full", [True, False])
def test_automatic_references_name_actual_retained_keys_and_keep_both_sources(full, fresh, tmp_path, monkeypatch):
    written = fake_automatic(tmp_path, monkeypatch, full=full)
    videos = [tmp_path / "shared folder" / "first reference" / "preview.mp4",
              tmp_path / "shared folder" / "second reference" / "preview.mp4"]
    for video, content in zip(videos, [b"first reference", b"second reference"]):
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(content)
    result = onboard.look("fresh", reference_films=[str(video) for video in videos], analyze=True)
    measured = stored(fresh)
    references = jsonio.read(fresh / "brand.json")["look"]["references"]
    assert len(measured) == len(references) == 2 and reference_item(result)["done"]
    for video in videos:
        digest = jsonio.sha256(video)
        key, value = by_video(measured, video.resolve(), digest)
        record = next(record for record in references if record["file"] == str(video.resolve()))
        assert record["sha256"] == digest and record["analysis"]["moves"] == [key]
        assert record["analysis"]["file"] == written[digest][0]
        assert value["source"]["reference_record"]["file"] == str(video.resolve())
        assert value["source"]["reference_record"]["sha256"] == digest
        assert_source(value, written[digest][1], written[digest][0])
    before_keys = set(measured)
    onboard.look("fresh", reference_films=[str(video) for video in videos], analyze=True)
    assert set(stored(fresh)) == before_keys
    assert len(jsonio.read(fresh / "brand.json")["look"]["references"]) == 2


def test_automatic_versions_keep_prior_records_and_same_version_annotations(fresh, tmp_path, monkeypatch):
    written = fake_automatic(tmp_path, monkeypatch, full=True)
    video = tmp_path / "reference" / "preview.mp4"
    video.parent.mkdir()
    video.write_bytes(b"first reference")
    first_digest = jsonio.sha256(video)
    onboard.look("fresh", reference_films=[str(video)], analyze=True)
    first_key, _ = by_video(stored(fresh), video.resolve(), first_digest)
    video.write_bytes(b"second reference")
    second_digest = jsonio.sha256(video)
    onboard.look("fresh", reference_films=[str(video)], analyze=True)
    measured = stored(fresh)
    second_key, _ = by_video(measured, video.resolve(), second_digest)
    raw = jsonio.read(fresh / "brand.json")
    records = raw["look"]["references"]
    assert len(records) == len(measured) == 2 and first_key != second_key
    assert {record["sha256"] for record in records} == {first_digest, second_digest}
    for record in records:
        key = record["analysis"]["moves"][0]
        assert measured[key]["source"]["measurement_identity"]["reference"]["sha256"] == record["sha256"]
        assert_source(measured[key], written[record["sha256"]][1], record["analysis"]["file"])
    next(record for record in records if record["sha256"] == second_digest)["note"] = "retain this source annotation"
    jsonio.write_atomic(fresh / "brand.json", raw)
    onboard.look("fresh", reference_films=[str(video)], analyze=True)
    after = jsonio.read(fresh / "brand.json")["look"]["references"]
    assert len(stored(fresh)) == len(after) == 2
    assert next(record for record in after if record["sha256"] == second_digest)["note"] == "retain this source annotation"
    assert set(stored(fresh)) == set(measured)


def test_unique_keys_and_legacy_input_shapes_stay_supported(fresh, tmp_path):
    file = write_analysis(tmp_path / "fits.json", {"moves": {
        "lift": {"bezier": A, "rms": 0.006, "duration_s": 0.5, "source": "first.mov 0.2s"},
        "push": {"bezier": B, "rms": 0.024, "duration_s": 1.5, "source": "second.mov 0.8s"},
        "broken": {"error": "blurred"},
    }})
    result = onboard.look("fresh", measured=[file])
    measured = stored(fresh)
    assert set(measured) == {"lift", "push"}
    assert measured["lift"]["bezier"] == A and measured["push"]["bezier"] == B
    assert any("broken" in note and "blurred" in note for note in result["notes"])


def test_anonymous_analysis_reruns_are_stable_without_guessing_equal_basenames(fresh, tmp_path):
    data = analysis(Path("preview.mp4"), None, full=False)
    files = [write_analysis(tmp_path / "one.json", data), write_analysis(tmp_path / "two.json", data)]
    onboard.look("fresh", measured=files)
    before = stored(fresh)
    assert len(before) == 2
    assert {value["source"]["analysis"]["file"] for value in before.values()} == {str(Path(file).resolve()) for file in files}
    assert {value["source"]["measurement_identity"]["reference"]["kind"] for value in before.values()} == {"analysis"}
    Path(files[0]).write_text(json.dumps(data, indent=2), encoding="utf-8")
    onboard.look("fresh", measured=files)
    assert stored(fresh) == before
