"""slate deliver: refusals, the manifest written last with every file's sha256, immutable versions, drafts, and the
internal synthetic-media record."""

from __future__ import annotations

import stat
from pathlib import Path


import filmkit
from slate import jsonio
from slate.errors import Refused
from slate.finish import deliver, synthetic
from slate.finish.film import Film


def mastered(make_film, film_id="dl", **kw) -> Path:
    """A film mastered with ffmpeg whose other film gates are recorded as passing."""
    kw.setdefault("mode", "autonomous")
    if kw["mode"] == "autonomous":
        kw.setdefault("format", "finish-reel")
    d = make_film(film_id, **kw)
    code, out, err = filmkit.cli(d, "master")
    assert code == 0, err
    filmkit.pass_gates(d)
    return d


# ---------------------------------------------------------------------------------------------------------------
# Refusals


def test_refuses_when_a_gate_failed(make_film):
    d = mastered(make_film)
    filmkit.pass_gates(d, [], extra={"loudness": "FAIL"})
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "loudness FAIL" in err and "slate qa loudness" in err
    assert not (d / "deliver/v1").exists()


def test_refuses_when_a_gate_never_ran(make_film):
    d = make_film("unchecked", mode="autonomous", format="finish-reel")
    filmkit.cli(d, "master")
    code, out, err = filmkit.cli(d, "deliver")  # no slate qa yet: the film gates are UNCHECKED
    assert code == 1 and "UNCHECKED" in err


def test_refuses_a_stale_gate(make_film):
    from slate import gates

    d = mastered(make_film)
    stale = gates.GateResult.pass_("captions", "fixture", inputs={"voice/captions/dl.srt": "0" * 64})
    gates.write_report(d, [stale])
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "captions STALE" in err


def test_draft_is_never_final(make_film):
    d = mastered(make_film, "drafty", mode="draft", brand="open-brand", format="finish-tiny")
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "draft films are never delivered as final" in err
    assert not (d / "deliver/v1").exists()
    code, out = filmkit.cli_json(d, "deliver", "--draft")
    assert code == 0 and out["final"] is False and out["folder"] == "deliver/drafts/v1.1"
    m = jsonio.read(d / "deliver/drafts/v1.1/manifest.json")
    assert m["final"] is False and m["why_not_final"]
    # a second draft never overwrites the first
    code, out2 = filmkit.cli_json(d, "deliver", "--draft")
    assert out2["folder"] == "deliver/drafts/v1.2"


def test_replayed_asset_blocks_a_final_delivery(make_film):
    d = mastered(make_film, "replayed", replay_voice=True)
    film = Film(d)
    assert [r["what"] for r in synthetic.replayed(film)] == ["voice take for S01", "voice take for S03"]
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "replayed stand-in" in err and "--live" in err
    code, out = filmkit.cli_json(d, "deliver", "--draft")
    assert code == 0 and out["final"] is False
    m = jsonio.read(d / "deliver/drafts/v1.1/manifest.json")
    assert len(m["replayed"]) == 2


def test_replayed_plate_and_capture_are_found(make_film):
    d = make_film("plates", mode="draft", brand="open-brand", format="finish-tiny", renders=False, mix=False)
    filmkit.write_json(d / "plates/S02/plate/plate.json", {"shot": "S02", "covers": ["S02"], "provenance": {
        "lane": "cloud-api", "provider": "higgsfield", "request_id": "r", "model": "fixture-video",
        "role": "plate_scene",
        "estimated_usd": 0, "actual_usd": 0, "approved_by": "Test Person", "replay": True}})
    filmkit.write_json(d / "captures/app/capture.json", {"name": "app", "kind": "ios", "replay": True})
    film = Film(d)
    whats = [r["what"] for r in synthetic.replayed(film)]
    assert "plate for S02" in whats and "capture app" in whats
    rec = synthetic.record(film)
    assert rec["summary"]["generated_shots"] == ["S02"]
    assert rec["captures"][0]["synthetic"] is False


def test_supervised_needs_the_ae_finish(make_film):
    d = make_film("supint", mode="supervised")
    filmkit.cli(d, "master", "--ffmpeg")
    filmkit.pass_gates(d)
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "master.finish FAIL" in err


def test_stale_masters_are_refused(make_film):
    d = mastered(make_film, "stalemaster")
    film = Film(d)
    filmkit.write_wav24(d / "sound/mix/final_mix.wav", filmkit.np.zeros(int(film.seconds * filmkit.SR)))
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "older than the film's current renders or mix" in err


def test_parked_film_is_refused(make_film):
    from slate.state import approvals

    d = mastered(make_film, "parked")
    approvals.ask(d, "May the film say this?", source="test", parks=True)
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "parked" in err


def test_no_master_is_a_missing_prerequisite(make_film):
    d = make_film("nomaster", mode="autonomous", format="finish-reel")
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 3 and "slate master" in err


# ---------------------------------------------------------------------------------------------------------------
# The set and its manifest


def test_manifest_is_written_last_with_every_hash(make_film):
    d = mastered(make_film, "set")
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0 and out["status"] == "delivered" and out["final"] is True, out
    folder = d / "deliver/v1"
    man = folder / "manifest.json"
    m = jsonio.read(man)
    assert jsonio.validate(m, "manifest") == []
    listed = {f["file"] for f in m["files"]}
    on_disk = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p.name != "manifest.json"}
    assert listed == on_disk
    for f in m["files"]:
        p = folder / f["file"]
        assert jsonio.sha256(p) == f["sha256"] and p.stat().st_size == f["bytes"]
        assert f["publishing"] == "not approved for publishing"
        assert man.stat().st_mtime_ns >= p.stat().st_mtime_ns  # the manifest came last
    kinds = {f["kind"] for f in m["files"]}
    assert {"master:prores_422_hq", "master:h264", "mix", "mix_record", "stem", "captions", "cut:review",
            "cut:phone", "thumbnail", "validation"} <= kinds
    assert m["publishing"]["approved"] is False and m["publishing"]["status"] == "not approved for publishing"
    assert "slate approve final_cut deliver/v1/masters/set_H264.mp4" in m["publishing"]["approve"]
    assert m["gates"]["passed"] is True and m["replayed"] == [] and m["missing"] == []
    # the delivered masters are the masters, byte for byte
    for f in m["files"]:
        if f["kind"].startswith("master:"):
            assert f["sha256"] == jsonio.sha256(d / f["source"])
    # cuts and thumbnails are the film's length and size
    phone = next(f for f in m["files"] if f["kind"] == "cut:phone")
    assert phone["media"]["video"]["nb_frames"] in ("40", 40)
    thumbs = [f for f in m["files"] if f["kind"] == "thumbnail"]
    assert thumbs and all(f["media"] is None for f in thumbs)


def test_synthetic_media_is_recorded_internally_with_no_labels(make_film):
    d = mastered(make_film, "syn")
    filmkit.cli(d, "deliver")
    m = jsonio.read(d / "deliver/v1/manifest.json")
    internal = m["internal"]
    assert "internal only" in internal["note"]
    s = internal["synthetic_media"]
    assert s["summary"]["synthetic_voice_shots"] == ["S01", "S03"]
    assert {v["source"] for v in s["voices"]} == {"designed"}
    assert internal["labels"]["ai_label"] == "none" and internal["labels"]["law"] is None
    assert set(internal["labels"]["platforms"]) == {"reels", "tiktok"}  # the reel format's platforms
    assert all(v == "the owner's call (not decided yet)" for v in internal["labels"]["platforms"].values())


def test_platform_label_calls_and_a_law_are_recorded(make_film, settings_toml):
    settings_toml('[finish.labels]\nlaw = "a fixture law"\nplatforms = { tiktok = "no label" }\n')
    d = make_film("labels", mode="draft", brand="open-brand", format="finish-reel", renders=False, mix=False)
    lab = synthetic.labels(Film(d))
    assert lab["ai_label"] == "required by a fixture law"
    assert lab["platforms"]["tiktok"] == "no label" and lab["platforms"]["reels"].startswith("the owner's call")


def test_recorded_voice_is_not_synthetic(make_film):
    d = make_film("rec", mode="draft", brand="open-brand", format="finish-tiny", renders=False, mix=False)
    tj = jsonio.read(d / "voice/takes/S01/t01.json")
    tj["voice_source"] = "recording"
    jsonio.write_atomic(d / "voice/takes/S01/t01.json", tj)
    rec = synthetic.record(Film(d))
    assert rec["summary"]["synthetic_voice_shots"] == ["S03"] and rec["summary"]["recorded_voice_shots"] == ["S01"]


# ---------------------------------------------------------------------------------------------------------------
# Versions are never overwritten


def test_a_delivered_version_is_never_overwritten(make_film):
    d = mastered(make_film, "imm")
    code, out = filmkit.cli_json(d, "deliver")
    folder = d / "deliver/v1"
    before = {p: (jsonio.sha256(p), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()}
    for p in before:
        assert not (p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)), p  # read-only
    code, out2 = filmkit.cli_json(d, "deliver")
    assert code == 0 and out2["status"] == "already_delivered"
    after = {p: (jsonio.sha256(p), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()}
    assert after == before


def test_changed_content_is_the_next_version(make_film):
    d = mastered(make_film, "nextv")
    filmkit.cli(d, "deliver")
    v1 = jsonio.sha256(d / "deliver/v1/manifest.json")
    film = Film(d)
    filmkit.render_act(d / "renders/act2.mov", 15, film.size, film.rate, seed=11)
    reg = jsonio.read(d / "renders/registry.json")
    reg["entries"][1]["sha256"] = jsonio.sha256(d / "renders/act2.mov")
    jsonio.write_atomic(d / "renders/registry.json", reg)
    filmkit.cli(d, "master")
    filmkit.pass_gates(d)
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0 and out["version"] == 2 and out["started_version"] is True, out
    assert jsonio.sha256(d / "deliver/v1/manifest.json") == v1
    assert jsonio.read(d / "film.json")["versions"][-1]["n"] == 2


def test_a_stray_folder_is_never_written_over(make_film):
    d = mastered(make_film, "stray")
    (d / "deliver/v1").mkdir(parents=True)
    (d / "deliver/v1/keep.txt").write_text("someone's file")
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "never writes over it" in err
    assert (d / "deliver/v1/keep.txt").read_text() == "someone's file"


def test_failed_delivery_leaves_nothing_behind(make_film, monkeypatch):
    d = mastered(make_film, "fails")

    def boom(*a, **k):
        raise Refused("fixture failure while making the cuts")

    monkeypatch.setattr("slate.finish.cuts.review_half", boom)
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1
    assert not (d / "deliver/v1").exists()
    assert not [p for p in (d / "deliver").iterdir() if p.name.startswith(".staging")]


def test_listing_and_dry_run(make_film):
    d = mastered(make_film, "lst")
    code, p = filmkit.cli_json(d, "deliver", "--dry-run")
    assert code == 0 and p["would"].startswith("deliver to deliver/v1") and not (d / "deliver/v1").exists()
    filmkit.cli(d, "deliver")
    code, out = filmkit.cli_json(d, "deliver", "--list")
    row = out["deliveries"][0]
    assert row["final"] is True and all(m["publishing"] == "not approved for publishing" for m in row["masters"])


def test_run_returns_the_manifest(make_film):
    d = mastered(make_film, "iface")
    path = deliver.run(d)
    assert path == d / "deliver/v1/manifest.json" and path.is_file()


def test_a_plate_without_an_approved_take_blocks_a_final_delivery(make_film):
    d = mastered(make_film, "plate")
    filmkit.write_json(d / "plates/S02/plate/plate.json", {"shot": "S02", "covers": ["S02"], "provenance": {
        "lane": "cloud-api", "provider": "higgsfield", "request_id": "r", "model": "fixture-video",
        "role": "plate_scene", "estimated_usd": 0, "actual_usd": 0, "approved_by": "", "replay": False}})
    code, out, err = filmkit.cli(d, "deliver")
    assert code == 1 and "plate for S02 has no approved take" in err


def test_two_caption_files_with_one_name_both_arrive(make_film):
    d = mastered(make_film, "caps")
    filmkit.write_json(d / "voice/captions/captions.json", {"cues": []})
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0, out
    files = {f["file"] for f in jsonio.read(d / "deliver/v1/manifest.json")["files"]}
    assert {"captions/captions.json", "captions/captions-captions.json"} <= files
