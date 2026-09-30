"""Generated shots end to end in replay: two takes, the judge gate, the pick, the conform, resumes and refusals."""

from __future__ import annotations

import json

import pytest

from gencap_helpers import SHOTS, run_json, write_timing
from slate import clock, config, gates, jsonio, ledger
from slate.capture import media
from slate.gen import bodies, deps, run, store
from slate.vendors import higgsfield


def _generate(film, shot="S02", extra=()):
    code, out, err = run_json(["gen", "run", "--film", str(film), "--shot", shot, *extra])
    assert code == 0, err
    return out


def test_two_takes_by_default_recorded_before_waiting_and_pinned(film):
    out = _generate(film)
    assert out["takes_wanted"] == 2 and out["completed"] == 2 and out["lane"] == "cloud-api"
    rec = store.load_record(film, "S02")
    assert deps.validate_part(rec, "generation") == []
    ids = [t["request_id"] for t in rec["takes"]]
    assert len(set(ids)) == 2 and all(t["status"] == "completed" and t["files"] for t in rec["takes"])
    assert "<upload:refs/start.png>" in json.dumps(rec["body"])  # no URLs kept, only what was uploaded
    rows = ledger.rows(provider="higgsfield")
    assert sorted(r["request_id"] for r in rows) == sorted(ids) and all(r["replay"] for r in rows)
    fj = jsonio.read(film / "film.json")
    assert fj["pins"]["plate_video"] == rec["model"] == config.registry()["roles"]["plate_video"]["default"]


def test_the_pin_holds_when_the_registry_moves(film, monkeypatch):
    _generate(film)
    moved = json.loads(json.dumps(config.registry()))
    moved["roles"]["plate_video"]["default"] = "kling-video/v3.0/pro/image-to-video"
    monkeypatch.setattr(config, "registry", lambda refresh=False: moved)
    role, model, source = bodies.resolve_model(film, deps.load_film(film), SHOTS[1]["generate"])
    assert (role, source) == ("plate_video", "pin") and model == "bytedance/seedance-2.5/image-to-video"


def test_judge_needs_two_finished_takes_and_records_a_review_gate(film):
    _generate(film)
    rec = store.load_record(film, "S02")
    rec["takes"][1]["status"] = "failed"
    store.save_record(film, "S02", rec)
    code, out, _ = run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    assert code == 1 and "at least 2" in out["message"]
    rec["takes"][1]["status"] = "completed"
    store.save_record(film, "S02", rec)
    code, out, err = run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    assert code == 0, err
    assert (film / out["sheet"]).is_file() and out["gate"]["status"] == "REVIEW"
    assert {m["take"] for m in out["takes"]} == {"t1", "t2"} and all("black_frames" in m for m in out["takes"])
    report = gates.load_report(film)
    assert report["gen_takes:S02"].status == "REVIEW" and not report["gen_takes:S02"].passed


def test_conform_only_from_a_picked_take_with_exact_frames_and_provenance(film):
    _generate(film)
    code, out, _ = run_json(["plates", "conform", "--film", str(film), "--shot", "S02"])
    assert code == 1 and "no approved take" in out["message"]
    code, out, _ = run_json(["gen", "pick", "--film", str(film), "--shot", "S02", "--take", "t2", "--by", "Quinn"])
    assert code == 1 and "judge" in out["hint"]  # a take is picked from the sheet, not blind
    run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    code, out, err = run_json(["gen", "pick", "--film", str(film), "--shot", "S02", "--take", "t2", "--reject", "t1",
                               "--why", "the horizon warps: reads as AI", "--by", "Quinn"])
    assert code == 0, err
    assert out["gate"]["status"] == "PASS" and out["approved"]["take"] == "t2"
    code, out, err = run_json(["plates", "conform", "--film", str(film), "--shot", "S02"])
    assert code == 0, err
    fj = deps.load_film(film)
    clk = clock.from_film(fj)
    take_file = store.take_file(film, store.approved_take(store.load_record(film, "S02")))
    expected = clk.frame_of(media.probe(take_file)["duration"])
    assert out["frames"]["count"] == expected == media.count_frames(film / out["frames"]["dir"])
    assert media.sequence_size(film / out["frames"]["dir"]) == (fj["width"], fj["height"])
    plate = jsonio.read(film / out["plate"])
    assert deps.validate_part(plate, "plate") == []
    prov = plate["provenance"]
    for key in ("request_id", "model", "estimated_usd", "actual_usd", "approved_by", "lane"):
        assert prov[key] not in (None, ""), key
    assert prov["approved_by"] == "Quinn" and prov["lane"] == "cloud-api"
    assert plate["source"]["width"] and plate["source"]["fps"] and plate["source"]["duration"]
    assert plate["frames"]["fps_num"] == fj["fps_num"] and plate["frames"]["fps_den"] == fj["fps_den"]
    assert plate["ambience"]["rate"] == 48000 and plate["ambience"]["bits"] == 24
    assert deps.is_locked(film, deps.rel(film, take_file))  # the approved take is locked by its hash


def test_a_changed_approved_file_is_refused(film):
    _generate(film)
    run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    run_json(["gen", "pick", "--film", str(film), "--shot", "S02", "--take", "t1", "--by", "Quinn"])
    take_file = store.take_file(film, store.approved_take(store.load_record(film, "S02")))
    take_file.write_bytes(take_file.read_bytes() + b"tampered")
    code, out, _ = run_json(["plates", "conform", "--film", str(film), "--shot", "S02"])
    assert code == 1 and "changed on disk" in out["message"]


def test_ugly_takes_never_ship_and_rejected_takes_dont_count(film):
    _generate(film)
    run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    code, out, _ = run_json(["gen", "pick", "--film", str(film), "--shot", "S02", "--reject", "t1", "--reject", "t2",
                             "--why", "both read as AI", "--by", "Quinn"])
    assert code == 1 and out["gate"]["status"] == "FAIL"
    code, out, _ = run_json(["plates", "conform", "--film", str(film), "--shot", "S02"])
    assert code == 1 and "rejected" in out["message"]
    again = _generate(film)
    assert again["new_takes"] == 2  # rejected takes don't count toward the two


def test_an_approved_shot_is_never_regenerated_without_force(film):
    _generate(film)
    run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    run_json(["gen", "pick", "--film", str(film), "--shot", "S02", "--take", "t1", "--by", "Quinn"])
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S02"])
    assert code == 1 and "--force" in out["message"]
    assert _generate(film, extra=["--force"])["new_takes"] == 2


def test_a_rerun_waits_on_running_takes_instead_of_paying_again(film):
    _generate(film)
    rec = store.load_record(film, "S02")
    running = rec["takes"][1]
    running.update(status="submitted", files=[], probe=None)
    store.save_record(film, "S02", rec)
    before = len(ledger.rows(provider="higgsfield"))
    out = _generate(film)
    assert out["resuming"] == ["t2"] and out["new_takes"] == 0
    assert len(ledger.rows(provider="higgsfield")) == before  # nothing new was submitted
    assert store.find_take(store.load_record(film, "S02"), "t2")["status"] == "completed"


def test_cancel_and_refresh(film):
    _generate(film)
    rec = store.load_record(film, "S02")
    rec["takes"][0].update(status="queued")
    store.save_record(film, "S02", rec)
    code, out, err = run_json(["gen", "cancel", "--film", str(film), "--shot", "S02"])
    assert code == 0, err
    assert out["results"][0]["canceled"] is True
    assert store.find_take(store.load_record(film, "S02"), "t1")["status"] == "canceled"
    rec = store.load_record(film, "S02")
    rec["takes"][1].update(status="in_progress", files=[])
    store.save_record(film, "S02", rec)
    code, out, _ = run_json(["gen", "takes", "--film", str(film), "--shot", "S02", "--refresh"])
    assert code == 0 and out["takes"][1]["status"] == "completed" and out["takes"][1]["files"]


def test_brand_material_is_refused_before_anything_is_spent(make_film):
    film = make_film(uploads=False)
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S02"])
    assert code == 1 and "allows uploads" in out["message"]
    assert ledger.rows() == [] and store.load_record(film, "S02") is None


def test_policy_refusals_exit_one_before_any_spend(make_film):
    film = make_film()
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S03"])  # adds-something on "rarely"
    assert code == 1 and "where-it-helps" in out["message"]
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S01"])  # no generate block
    assert code == 1 and "code builds it" in out["message"]
    draft = make_film("f2", mode="draft")
    code, out, _ = run_json(["gen", "run", "--film", str(draft), "--shot", "S02"])
    assert code == 1 and "Draft" in out["message"]
    none = make_film("f3", generation="none")
    code, out, _ = run_json(["gen", "run", "--film", str(none), "--shot", "S02"])
    assert code == 1 and "none" in out["message"]
    assert ledger.rows() == []


def test_one_take_is_a_usage_error(film):
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S02", "--takes", "1"])
    assert code == 2 and "at least 2" in out["message"]


def test_dry_run_spends_nothing_and_writes_nothing(film):
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S02", "--dry-run"])
    assert code == 0 and out["dry_run"] and out["new_takes"] == 2
    assert ledger.rows() == [] and store.load_record(film, "S02") is None
    assert not (jsonio.read(film / "film.json").get("pins") or {}).get("plate_video")


def test_estimate_is_free_and_shaped(film):
    code, out, _ = run_json(["gen", "estimate", "--film", str(film), "--shot", "S02"])
    assert code == 0
    assert out["takes"] == 2 and out["estimate_usd_per_take"] == pytest.approx(0.924)
    assert out["estimate_usd_total"] == pytest.approx(1.848) and out["replay"] is True
    assert ledger.rows() == []


def test_a_still_generation_becomes_a_one_frame_plate(film):
    _generate(film, "S04")
    run_json(["gen", "judge", "--film", str(film), "--shot", "S04"])
    run_json(["gen", "pick", "--film", str(film), "--shot", "S04", "--take", "t1", "--by", "Quinn"])
    code, out, err = run_json(["plates", "conform", "--film", str(film), "--shot", "S04", "--frames", "3"])
    assert code == 0, err
    assert out["frames"]["count"] == 3 and out["warnings"]  # shorter than the shot: it says so


def test_the_plan_says_what_may_generate_and_what_it_costs(film):
    code, out, _ = run_json(["plates", "plan", "--film", str(film)])
    assert code == 0
    rows = {r["shot"]: r for r in out["shots"]}
    assert set(rows) == {"S02", "S03", "S04", "S05"}
    assert rows["S02"]["allowed"] and rows["S02"]["takes"] == 2
    assert not rows["S03"]["allowed"] and not rows["S05"]["allowed"]  # need where-it-helps
    assert out["totals"]["allowed"] == 2 and out["totals"]["estimate_usd"] > 0
    plan = jsonio.read(film / "plates" / "plan.json")
    assert deps.validate_part(plan, "plate", "plan") == []


def test_a_take_reference_needs_an_approved_take(make_film):
    film = make_film(generation="where-it-helps")
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S05"])  # image: take:S04
    assert code == 1 and "approved take of S04" in out["message"]
    _generate(film, "S04")
    run_json(["gen", "judge", "--film", str(film), "--shot", "S04"])
    run_json(["gen", "pick", "--film", str(film), "--shot", "S04", "--take", "t2", "--by", "Quinn"])
    out = _generate(film, "S05")
    rec = store.load_record(film, "S05")
    assert out["completed"] == 2 and rec["uploads"][0]["generator_output"] is True


def test_shots_without_timing_are_a_missing_prerequisite(film):
    (film / "timing" / "shots.json").unlink()
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S02"])
    assert code == 3 and "slate timing" in out["hint"]
    write_timing(film)


def test_a_request_id_that_only_reached_the_ledger_is_adopted_not_paid_again(film):
    """The process died after the submit (the id is on the ledger row) but before generation.json was saved."""
    _generate(film)
    rec = store.load_record(film, "S02")
    lost = rec["takes"][1]
    real_id = lost["request_id"]
    lost.update(status="submitted", request_id=None, ledger_row=None, files=[], probe=None)
    first = {k: v for k, v in rec["takes"][0].items() if k not in ("request_key", "idempotency_key")}
    rec["takes"].append({**first, "id": "t3", "status": "submitted", "request_id": None,
                         "ledger_row": None, "files": [], "probe": None})  # never got an id anywhere
    store.save_record(film, "S02", rec)
    before = len(ledger.rows(provider="higgsfield"))
    out = _generate(film)
    rec = store.load_record(film, "S02")
    assert store.find_take(rec, "t2")["request_id"] == real_id and store.find_take(rec, "t2")["status"] == "completed"
    assert store.find_take(rec, "t3")["status"] == "error"
    assert out["new_takes"] == 0 and len(ledger.rows(provider="higgsfield")) == before


def test_generate_blocks_are_read_from_the_script_when_timing_doesnt_carry_them(make_film):
    """A person writes the generate block in script/script.json; shots.json only needs the frames."""
    bare = [{k: v for k, v in s.items() if k != "generate"} for s in SHOTS]
    film = make_film(shots=bare)
    script = jsonio.read(film / "script" / "script.json")
    script["acts"] = [{"name": "One", "shots": [{"id": "S01", "vo": "Open the app and tap save."},
                                                {"id": "S02", "vo": "", "generate": SHOTS[1]["generate"]}]}]
    jsonio.write_atomic(film / "script" / "script.json", script)
    shots = {s["id"]: s for s in deps.load_shots(film)}
    assert shots["S02"]["generate"]["reason"] == "real-scene" and shots["S02"]["frame_in"] == 100
    assert "generate" not in shots["S01"]
    code, out, _ = run_json(["plates", "plan", "--film", str(film)])
    assert code == 0 and [r["shot"] for r in out["shots"]] == ["S02"] and out["shots"][0]["allowed"]


def test_a_take_whose_submit_died_unanswered_is_sent_again_under_its_own_key(film, monkeypatch):
    """The process died mid-POST: the ledger row has no request id and neither does generation.json. The rerun sends
    the exact body written ahead of the POST, with the same Idempotency-Key, on the same ledger row."""
    _generate(film)
    rec = store.load_record(film, "S02")
    fj = deps.load_film(film)
    sent = jsonio.read(film / "plates" / "S02" / "takes" / "t1" / "submit.json")
    assert sent["idempotency_key"] == rec["takes"][0]["idempotency_key"] == higgsfield.idempotency_key(
        rec["takes"][0]["request_key"])
    assert "example.com" not in json.dumps(rec) and "replay://" in json.dumps(sent["body"])  # URLs only ahead
    # take t3's submit went out and the process died before any answer
    key = run.request_key_for(fj, "S02", "t3", rec["model"], sent["body"])
    take = {k: v for k, v in rec["takes"][0].items() if k not in ("request_key", "idempotency_key")}
    take.update(id="t3", status="submitted", request_id=None, ledger_row=None, files=[], probe=None,
                request_key=key, idempotency_key=higgsfield.idempotency_key(key))
    rec["takes"].append(take)
    rec["takes_wanted"] = 3
    store.save_record(film, "S02", rec)
    jsonio.write_atomic(film / "plates" / "S02" / "takes" / "t3" / "submit.json",
                        {**sent, "take": "t3", "request_key": key, "idempotency_key": take["idempotency_key"]})
    est = higgsfield.ledger_estimate(rec["model"], sent["body"], {"usd": 0.924})
    with monkeypatch.context() as m:
        m.setattr(ledger, "_identity", lambda: {"pid": 99999, "proc_start": "gone", "job": None})
        crashed = ledger.begin(est, film=film, key=key, note="crashed mid-POST")
    before = len(ledger.rows(provider="higgsfield"))
    out = _generate(film, extra=["--takes", "3"])
    rec = store.load_record(film, "S02")
    t3 = store.find_take(rec, "t3")
    assert out["resuming"] == ["t3"] and out["new_takes"] == 0
    assert t3["status"] == "completed" and t3["request_id"] and t3["ledger_row"] == crashed
    assert len(ledger.rows(provider="higgsfield")) == before  # the same row, taken over: nothing counted twice
    (row,) = ledger.rows(id=crashed)
    assert row["status"] == "done" and row["request_id"] == t3["request_id"]
    # the same key and body always name the same request (what Higgsfield does with a resend)
    again = higgsfield.Client().submit(rec["model"], sent["body"], key=key)
    assert again["request_id"] == t3["request_id"]


def test_the_estimate_shows_the_price_and_the_saving_apart(film, monkeypatch):
    real = higgsfield.Client.estimate

    def quoted(self, path, body):
        real(self, path, body)
        return {"usd": 0.308, "discount": {"usd": 0.252}, "pricing_description": "a real Kling Pro answer"}

    monkeypatch.setattr(higgsfield.Client, "estimate", quoted)
    code, out, _ = run_json(["gen", "estimate", "--film", str(film), "--shot", "S02"])
    assert code == 0
    assert out["estimate_usd_per_take"] == 0.308 and out["saving_usd_per_take"] == 0.252
    assert out["list_usd_per_take"] == pytest.approx(0.56) and out["estimate_usd_total"] == pytest.approx(0.616)


def test_gates_name_their_stage_for_slate_status(film):
    """gen_takes:<shot> has "takes" in its name, which `slate status` would read as a voice gate without its stage."""
    _generate(film)
    run_json(["gen", "judge", "--film", str(film), "--shot", "S02"])
    report = gates.load_report(film)
    assert report["gen_takes:S02"].details["stage"] == "generated"
    status = pytest.importorskip("slate.state.status")
    assert status._gate_stage(report["gen_takes:S02"]) == "generated"  # noqa: SLF001
