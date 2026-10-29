"""The spotter reads neutral temporary films; its renderer is a short known signal, with no library or vendor IO."""

import copy

import numpy as np
import pytest

from slate import jsonio
from slate.errors import Refused, UsageError
from slate.sound.effects import palette, spotting
from slate.sound.film import Film
from slate.state import lock


@pytest.fixture
def picture(tmp_path):
    shots = {"fps_num": 24, "fps_den": 1, "width": 640, "height": 360, "total_frames": 144,
             "acts": [{"index": 1, "shots": ["S01"]}, {"index": 2, "shots": ["S02"]}],
             "shots": [{"id": "S01", "act": 1, "frame_in": 0, "frame_out": 72},
                       {"id": "S02", "act": 2, "frame_in": 72, "frame_out": 144}]}
    brand = {"sound": {"key": "A minor"}, "motion": {"eases": {"arrive": [0.12, 0.9, 0.2, 1.0]}}}
    film = Film(tmp_path, {"id": "neutral"}, shots, 24, 1, 8000, 6.0, {}, brand,
                palette.from_brand(brand), None)
    jsonio.write_atomic(film.path("timing/shots.json"), shots)
    jsonio.write_atomic(film.path("film.json"), film.data)
    jsonio.write_atomic(film.path("timing/visual_events/act1.json"),
                        [{"shot": "S01", "id": "press", "kind": "tap", "t": 1.0}])
    jsonio.write_atomic(film.path("timing/visual_events/act2.json"), {"silent": True, "reason": "A held frame."})
    return film


def render(cue):
    wave = np.zeros((80, 2))
    wave[11:17] = 0.5
    return wave, 3


def events(film, rows):
    jsonio.write_atomic(film.path("timing/visual_events/act1.json"), rows)


def test_spot_uses_measured_anchor_clock_and_preserves_silent_act(picture):
    sheet = spotting.build(picture, seed=7, render_cue=render)
    cue, = sheet["cues"]
    assert cue["t"] + (11 + 3) / picture.sr == cue["beat"] == 1.0
    assert cue["params"]["fps_num"] == 24
    assert cue["params"]["fps_den"] == 1
    assert cue["params"]["sample_rate"] == 8000
    assert cue["family"] == "accents" and cue["sound"] == "wood block"
    assert cue["dur"] == 80 / picture.sr
    assert sheet["input_report"][1]["status"] == "silent"
    assert sheet["input_report"][1]["reason"] == "A held frame."
    assert sheet["provenance"]["shots_sha256"] == jsonio.sha256(picture.path("timing/shots.json"))
    assert sheet["provenance"]["film_sound_sha256"] == jsonio.sha256_obj(picture.data.get("sound"))
    assert not picture.path("sound/sound_cues.json").exists()
    assert not jsonio.validate(sheet, "sound_cues")


def test_seeded_variation_survives_rebuild_and_inserting_an_earlier_event(picture):
    original = [{"shot": "S01", "id": f"press-{i}", "kind": "tap", "t": 1 + i * 0.1} for i in range(5)]
    events(picture, original)
    first = spotting.build(picture, seed=19, render_cue=render)
    assert spotting.build(picture, seed=19, render_cue=render) == first
    assert len({c["params"]["seed"] for c in first["cues"]}) == 5
    assert len({c["params"]["cents"] for c in first["cues"]}) == 5
    events(picture, [{"shot": "S01", "id": "earlier", "kind": "tap", "t": 0.5}, *original])
    second = spotting.build(picture, seed=19, render_cue=render)
    assert second["cues"][1:] == first["cues"]
    assert spotting.build(picture, seed=20, render_cue=render)["cues"][1]["params"] != first["cues"][0]["params"]


@pytest.mark.parametrize("bad,status", [(None, "missing"), ("{", "unreadable"),
                                        ("[]", "malformed"), ('{"silent":true,"reason":" "}', "malformed"),
                                        ('[{"shot":"S01","id":"x","kind":"tap","t":NaN}]', "malformed")])
def test_missing_or_malformed_act_never_becomes_silence(picture, bad, status):
    path = picture.path("timing/visual_events/act1.json")
    if bad is None:
        path.unlink()
    else:
        path.write_text(bad)
    with pytest.raises(Refused) as err:
        spotting.build(picture, render_cue=render)
    assert err.value.details["input_report"][0]["status"] == status
    assert err.value.details["input_report"][1]["status"] == "silent"


@pytest.mark.parametrize("event", [
    {"shot": "S02", "id": "other-act", "kind": "tap", "t": 1},
    {"shot": "S01", "id": "late", "kind": "tap", "t": 4},
    {"shot": "S01", "id": "clock", "kind": "tap", "t": 1, "frame": 12},
    {"shot": "S01", "id": "draw", "kind": "draw", "t": 2, "start": 0.5, "dur": 1},
])
def test_cross_act_and_invalid_clock_are_reported(picture, event):
    events(picture, [event])
    with pytest.raises(Refused) as err:
        spotting.build(picture, render_cue=render)
    assert err.value.details["input_report"][0]["status"] == "malformed"


def test_palette_overrides_ease_and_pan_use_film_data(picture):
    events(picture, [{"shot": "S01", "id": "line", "kind": "draw", "t": 2,
                      "start": 1, "dur": 1, "ease": "arrive", "x": 640}])
    picture.palette["kinds"]["draw"].update(sound="paper air", gain=-33,
                                           params={"f0": 800, "f1": 2600, "ease": "sine"})
    sheet = spotting.build(picture, render_cue=render)
    cue, = sheet["cues"]
    assert cue["sound"] == "paper air" and cue["pan"] == 0.6
    assert cue["params"]["dur"] == 1
    assert cue["params"]["ease"] == {"kind": "bezier", "bezier": [0.12, 0.9, 0.2, 1.0]}
    assert -34.2 <= cue["gain"] <= -31.8


def test_silences_and_beds_survive_respotting(picture):
    old = {"cues": [], "silences": [{"from": 0, "to": 0.2, "why": "An opening breath.", "stems": ["fx"]}],
           "beds": [{"from": 4, "to": 5, "why": "A planned held sound."}]}
    jsonio.write_atomic(picture.path("sound/sound_cues.json"), old)
    before = picture.path("sound/sound_cues.json").read_bytes()
    sheet = spotting.build(picture, render_cue=render)
    assert sheet["silences"] == old["silences"] and sheet["beds"] == old["beds"]
    assert picture.path("sound/sound_cues.json").read_bytes() == before


def test_inherited_events_are_read_without_materializing_or_changing_parent(picture, tmp_path):
    child_dir = tmp_path / "derived"
    child_dir.mkdir()
    child = copy.deepcopy(picture)
    child.dir = child_dir
    rels = ["timing/shots.json", "timing/visual_events/act1.json", "timing/visual_events/act2.json"]
    before = {r: picture.path(r).read_bytes() for r in rels}
    rec = lock.empty()
    for rel in rels:
        rec["files"][rel] = {"ref": {"film": "neutral", "dir": str(picture.dir), "path": rel,
                                    "sha256": jsonio.sha256(picture.path(rel))}}
    jsonio.write_atomic(child.path("film.lock.json"), rec)
    sheet = spotting.build(child, render_cue=render)
    assert len(sheet["cues"]) == 1
    assert all(not child.path(r).exists() for r in rels)
    assert before == {r: picture.path(r).read_bytes() for r in rels}


def test_character_requires_a_motif_and_tonal_voices_require_explicit_music(picture):
    events(picture, [{"shot": "S01", "id": "gesture", "kind": "character", "t": 1}])
    with pytest.raises(UsageError, match="motif"):
        spotting.build(picture, render_cue=render)
    picture.brand["sound"]["motif"] = {"notes": ["A4", "C5", "E5"]}
    assert spotting.build(picture, render_cue=render)["cues"][0]["params"]["pitch"] in picture.brand["sound"]["motif"]["notes"]
    events(picture, [{"shot": "S01", "id": "value", "kind": "data", "t": 1}])
    picture.brand["sound"].pop("key")
    with pytest.raises(UsageError, match="musical key"):
        spotting.build(picture, render_cue=render)
    picture.palette["kinds"]["data"]["params"]["pitch"] = "C5"
    assert spotting.build(picture, render_cue=render)["cues"][0]["params"]["pitch"] == "C5"


def test_silent_render_cannot_be_reported_as_measured(picture):
    with pytest.raises(Refused, match="anchor cannot be measured"):
        spotting.build(picture, render_cue=lambda cue: (np.zeros((80, 2)), 0))


def test_write_on_default_lands_on_audible_start_instead_of_energy_peak(picture):
    from slate.sound.verify import anchor_offset

    events(picture, [{"shot": "S01", "id": "title", "kind": "type", "t": 1.0}])
    wave = np.zeros((picture.sr, 2))
    wave[800:4800] = np.linspace(0.001, 1.0, 4000)[:, None]
    cue, = spotting.build(picture, render_cue=lambda cue: (wave, 0))["cues"]
    audible = anchor_offset(wave, "start", picture.sr)
    assert cue["anchor"] == "start"
    assert cue["t"] + audible / picture.sr == cue["beat"]
    assert anchor_offset(wave, "peak50", picture.sr) - audible > picture.sr * 0.3


def test_event_sound_choices_persist_through_respot_and_match_shot_and_event(picture):
    events(picture, [{"shot": "S01", "id": "press", "kind": "tap", "t": 1.0},
                     {"shot": "S01", "id": "other", "kind": "tap", "t": 1.5}])
    jsonio.write_atomic(picture.path("timing/visual_events/act2.json"),
                        [{"shot": "S02", "id": "press", "kind": "tap", "t": 4.0}])
    picture.data["sound"] = {"event_overrides": [
        {"shot": "S01", "event": "press", "params": {"kind": "temple", "pitch": "A4"},
         "gain": -33, "pan": -0.4, "balance_family": "impacts"},
        {"shot": "S01", "event": "other", "params": {"kind": "knock", "pitch": "C4"}}]}
    jsonio.write_atomic(picture.path("film.json"), picture.data)
    baseline = copy.deepcopy(picture.palette)
    sheet = spotting.build(picture, seed=7, render_cue=render)
    first, second, untouched = sheet["cues"]
    assert first["params"]["kind"] == "temple" and first["params"]["pitch"] == "A4"
    assert first["family"] == "impacts" and first["pan"] == -0.4 and -34.2 <= first["gain"] <= -31.8
    assert second["params"]["kind"] == "knock" and second["params"]["pitch"] == "C4"
    assert untouched["params"]["kind"] == "block" and untouched["family"] == "accents"
    assert "event_override_sha256" in first["provenance"]
    assert "event_override_sha256" not in untouched["provenance"]
    jsonio.write_atomic(picture.path("sound/sound_cues.json"), sheet)
    # Re-read film data rather than trusting an in-memory edit or generated cue-sheet params.
    picture.data = jsonio.read(picture.path("film.json"))
    assert spotting.build(picture, seed=7, render_cue=render) == sheet
    assert picture.palette == baseline


@pytest.mark.parametrize("change", [
    {"t": 2}, {"approved": "reference"}, {"balance_family": "arbitrary"}, {"type": "oscillator"},
    {"stem": "voice"}, {"anchor": "none"}, {"gain": float("nan")}, {"pan": True}, {"pan": 2},
    {"params": []}, {"params": {"beat": 2}}, {"sound": ""}, {"type": "countup3"},
])
def test_event_sound_override_refuses_unsupported_choices(picture, change):
    picture.data["sound"] = {"event_overrides": [{"shot": "S01", "event": "press", **change}]}
    with pytest.raises(UsageError):
        spotting.build(picture, render_cue=lambda cue: pytest.fail("Invalid choices reached rendering."))


@pytest.mark.parametrize("overrides", [None, {}, [{"shot": "S01", "event": "missing", "gain": -30}],
                                      [{"shot": "S01", "event": "press"}],
                                      [{"shot": "S01", "event": "press", "gain": -30}] * 2])
def test_event_sound_override_refuses_invalid_or_duplicate_identity(picture, overrides):
    picture.data["sound"] = {"event_overrides": overrides}
    with pytest.raises(UsageError):
        spotting.build(picture, render_cue=render)


def test_event_sound_override_cannot_replace_a_pinned_cue(picture):
    picture.data["sound"] = {"music": {"match": [
        {"clip": "neutral-proof", "from": 0.5, "to": 1.5, "pin_cues": True}]},
        "event_overrides": [{"shot": "S01", "event": "press", "params": {"kind": "knock"}}]}
    ref = reference()
    for cue in ref["cue_sheet"]["cues"]:
        cue["family"] = "accents"
    with pytest.raises(Refused, match="cannot change pinned"):
        spotting.build(picture, render_cue=lambda cue: pytest.fail("A pinned override reached rendering."),
                       load_reference=lambda clip: ref)


def test_counter_is_one_logical_cue_with_measured_notes_on_visual_changes(picture):
    from slate.sound.effects import rendering, voices

    events(picture, [{"shot": "S01", "id": "count", "kind": "count", "t": 2,
                      "start": 1, "dur": 1, "from": 0, "to": 12, "hold_frames": 2, "ease": "linear"}])
    bound = lambda cue: rendering.render_cue(cue, sample_rate=picture.sr, palette=picture.palette)
    sheet = spotting.build(picture, seed=7, render_cue=bound)
    cue, = sheet["cues"]
    assert cue["t"] == 1 and cue["beat"] == 2
    ticks = voices.count_ticks(cue, sample_rate=picture.sr)
    assert len(cue["notes"]) == len(ticks) == 12
    for note, tick in zip(cue["notes"], ticks):
        assert note["beat"] == 1 + tick["at"]
        assert note["params"] == tick["params"]
        assert note["dur"] == 0.12
        assert note["t"] + note["measurement"]["anchor_sample"] / picture.sr == note["beat"]
        assert note["beat"] <= cue["beat"]
    assert len(rendering.expand_cues(sheet["cues"])) == 12


def test_page_uses_sampled_takes_with_reproducible_rate_variation(picture):
    events(picture, [{"shot": "S01", "id": f"page-{i}", "kind": "page", "t": 1 + i * 0.1}
                      for i in range(5)])
    cues = spotting.build(picture, seed=3, render_cue=render)["cues"]
    assert all(c["type"] == "sample" for c in cues)
    assert all(c["params"]["file"].startswith("FCP:") for c in cues)
    assert all(0.96 <= c["params"]["rate"] <= 1.04 for c in cues)
    assert len({c["params"]["rate"] for c in cues}) == 5


def test_layer_peaks_are_aligned_before_composite_measurement(picture):
    events(picture, [{"shot": "S01", "id": "carry", "kind": "whoosh", "t": 2}])

    def layer_render(cue):
        wave = np.zeros((800, 2))
        at = 300 if cue["type"] == "sample" else 100
        wave[at:at + 160] = 0.5
        return wave, 0

    cue, = spotting.build(picture, render_cue=layer_render)["cues"]
    air, sample = cue["params"]["layers"]
    assert air["offset"] == 200 / picture.sr
    assert sample["offset"] == 0
    assert sample["params"]["file"].endswith("Whoosh 13.caf")


def reference():
    return {"cue_sheet": {"window": [0, 2], "cues": [
        {"id": "approved-hit", "event": "press", "shot": "S01", "p3kind": "tick", "kind": "block",
         "type": "wood", "stem": "ui", "t": 0.95, "beat": 1.0, "anchor": "onset", "gain": -29,
         "pitch": 800, "seed": 71, "reverb": 0.25},
        {"id": "approved-layer", "event": "press-layer", "visual_event": "press", "shot": "S01", "p3kind": "tick-layer", "kind": "rim",
         "type": "wood", "stem": "ui", "t": 1.05, "beat": 1.1, "anchor": "onset", "gain": -35,
         "pitch": 600, "seed": 72}], "silences": []}, "balance_used": {
             "families": {"accents": {"kinds": ["tick", "tick-layer"]}},
             "rides": {"press": {"gain": -2}}}}


def test_approved_cues_keep_exact_sound_and_clock_without_rendering(picture):
    match = {"clip": "neutral-proof", "from": 0.5, "to": 1.5, "core": [0.6, 1.4],
             "pin_cues": True, "pin_rides": True}
    picture.data["sound"] = {"music": {"match": [match]}}
    ref = reference()
    before = copy.deepcopy(ref)
    calls = []

    def load(clip):
        calls.append(clip)
        return ref

    def never_render(cue):
        pytest.fail("An approved cue was re-rendered for automatic placement.")

    sheet = spotting.build(picture, seed=900, render_cue=never_render, load_reference=load)
    assert calls == ["neutral-proof"] and ref == before
    assert sheet["music"]["match"] == [match]
    assert len(sheet["cues"]) == 2  # approved legacy layers are not regrouped
    for cue, original in zip(sheet["cues"], ref["cue_sheet"]["cues"]):
        for key in ("t", "beat", "gain", "pitch", "seed", "event"):
            assert cue[key] == original[key]
        assert cue["params"]["kind"] == original["kind"]
        assert cue["approved"] == "neutral-proof"
        assert cue["approved_source_sha256"] == jsonio.sha256_obj(original)
    assert sheet["cues"][0]["reverb"] == 0.25


def test_approved_span_refuses_missing_loader_and_changed_picture(picture):
    match = {"clip": "neutral-proof", "from": 0.5, "to": 1.5, "pin_cues": True}
    picture.palette["balance"] = {"match_refs": [match]}
    with pytest.raises(Refused, match="reference loader"):
        spotting.build(picture, render_cue=render)
    events(picture, [{"shot": "S01", "id": "press", "kind": "tap", "t": 1.2}])
    with pytest.raises(Refused, match="no longer matches"):
        spotting.build(picture, render_cue=render, load_reference=lambda clip: reference())
    match["offset_s"] = 0.2
    with pytest.raises(Refused, match="approved clock"):
        spotting.build(picture, render_cue=render, load_reference=lambda clip: reference())


def test_deleted_approved_event_and_unmapped_layer_are_refused(picture):
    picture.data["sound"] = {"music": {"match": [
        {"clip": "neutral-proof", "from": 0.5, "to": 1.5, "pin_cues": True}]}}
    ref = reference()
    ref["cue_sheet"]["cues"][1].pop("visual_event")
    with pytest.raises(Refused, match="missing from the current picture"):
        spotting.build(picture, render_cue=render, load_reference=lambda clip: ref)
    jsonio.write_atomic(picture.path("timing/visual_events/act1.json"),
                        {"silent": True, "reason": "The interaction was removed."})
    with pytest.raises(Refused, match="missing from the current picture"):
        spotting.build(picture, render_cue=render, load_reference=lambda clip: reference())


def test_approved_families_keep_the_sources_own_processing(picture):
    picture.data["sound"]={"music":{"match":[
        {"clip":"neutral-proof","from":.5,"to":1.5,"pin_cues":True,"pin_rides":False}]}}
    picture.palette["kinds"]["tap"]["balance_family"]="impacts"
    ref=reference()
    ref["cue_sheet"]["cues"][0]["family"]="textures"
    before=copy.deepcopy(ref)
    def never_render(cue):
        pytest.fail("An approved source must not be recalibrated.")
    sheet=spotting.build(picture,render_cue=never_render,load_reference=lambda clip:ref)
    assert [c["family"] for c in sheet["cues"]]==["textures","accents"]
    assert ref==before


@pytest.mark.parametrize('mapping',[{}, {'accents':{'kinds':['tick','tick-layer']},
                                       'textures':{'kinds':['tick']}}])
def test_approved_families_refuse_missing_or_ambiguous_source_mapping(picture,mapping):
    picture.data["sound"]={"music":{"match":[
        {"clip":"neutral-proof","from":.5,"to":1.5,"pin_cues":True}]}}
    ref=reference();ref['balance_used']['families']=mapping
    with pytest.raises(Refused,match='unique approval-bound'):
        spotting.build(picture,render_cue=render,load_reference=lambda clip:ref)


def test_every_generic_kind_has_distinct_data_and_one_logical_cue(picture):
    picture.brand["sound"]["motif"] = {"notes": ["A4", "C5", "E5"]}
    rows = []
    for i, kind in enumerate(palette.STARTING_KINDS):
        row = {"shot": "S01", "id": kind, "kind": kind, "t": 0.5 + i * 0.15}
        if kind in ("count", "draw"):
            row.update(start=row["t"] - 0.1, dur=0.1)
        if kind == "count":
            row.update({"from": 0, "to": 2})
        rows.append(row)
    events(picture, rows)
    sheet = spotting.build(picture, render_cue=render)
    assert len(sheet["cues"]) == len(rows) == 13
    assert len({c["sound"] for c in sheet["cues"]}) == 13
    assert {c["family"] for c in sheet["cues"]} == {"accents", "textures", "swells", "impacts"}
    assert all(c["measurement"] if "notes" not in c else c["notes"] for c in sheet["cues"])


def test_actual_wood_repeats_change_waveform_beyond_level(picture):
    from slate.sound import checks
    from slate.sound.effects import rendering

    events(picture, [{"shot": "S01", "id": f"tap-{i}", "kind": "tap", "t": 1 + i * 0.1}
                      for i in range(5)])
    bound = lambda cue: rendering.render_cue(cue, sample_rate=picture.sr, palette=picture.palette)
    cues = spotting.build(picture, render_cue=bound)["cues"]
    waves = [bound(c)[0] for c in cues]
    assert not any(checks.same_sound(a, b) for a, b in zip(waves, waves[1:]))


def bed_signal(cue):
    if cue.get("kind") != "bed":
        return render(cue)
    count = int(cue["params"]["dur"] * 8000)
    progress = np.arange(count) / count
    envelope = 1 - progress if cue["id"] == "first" else progress
    return np.stack([envelope, envelope], axis=1), 0


def test_authored_sampled_beds_have_measured_short_handoff(picture):
    picture.data["sound"] = {"music": {"kind": "bed"}, "beds": [
        {"id": "first", "from": 0.25, "to": 2.25, "why": "The first held chord.",
         "params": {"notes": ["A3", "E4"], "attack": 0.1, "release": 0.1}},
        {"id": "second", "from": 1.25, "to": 3.25, "why": "The next held chord.",
         "params": {"notes": ["A3", "E4"]}}]}
    sheet = spotting.build(picture, render_cue=bed_signal)
    first, second = [c for c in sheet["cues"] if c["kind"] == "bed"]
    assert first["type"] == second["type"] == "spad"
    assert first["params"]["notes"] == ["A3", "E4"]
    assert first["params"]["dur"] == 2
    handoff, = sheet["bed_handoffs"]
    assert abs(handoff["at"] - 1.75) <= 2 / picture.sr
    assert handoff["length_s"] == 0.3
    assert first["clip_gain"]["before"] == second["clip_gain"]["after"] == 1
    assert first["clip_gain"]["after"] == second["clip_gain"]["before"] == 0
    for cue in (first, second):
        assert all(a < b for a, b in zip(cue["clip_gain"]["t"], cue["clip_gain"]["t"][1:]))
        assert 0 <= min(cue["clip_gain"]["g"]) <= max(cue["clip_gain"]["g"]) <= 2 ** 0.5
    assert spotting.build(picture, render_cue=bed_signal) == sheet


def test_bed_needs_explicit_notes_and_score_mode_keeps_bed_as_metadata(picture):
    picture.data["sound"] = {"music": {"kind": "bed"}, "beds": [
        {"from": 1, "to": 2, "why": "A permitted bed."}]}
    with pytest.raises(UsageError, match="explicit sampled chord notes"):
        spotting.build(picture, render_cue=render)
    picture.data["sound"]["music"]["kind"] = "score"
    sheet = spotting.build(picture, render_cue=render)
    assert len(sheet["cues"]) == 1 and sheet["cues"][0]["kind"] == "tap"
    assert sheet["music"]["kind"] == "score"
    assert sheet["beds"] == picture.data["sound"]["beds"]


def test_a_bed_with_two_handoffs_keeps_both_gain_curves(picture):
    picture.data["sound"] = {"music": {"kind": "bed"}, "beds": [
        {"id": name, "from": start, "to": start + 3, "params": {"notes": ["C4"]}}
        for name, start in (("first", 0), ("second", 1), ("third", 2))]}
    sheet = spotting.build(picture, render_cue=bed_signal)
    assert len(sheet["bed_handoffs"]) == 2
    middle = next(c for c in sheet["cues"] if c["id"] == "second")
    assert middle["clip_gain"]["before"] == 0
    assert middle["clip_gain"]["after"] == 0
    assert len(middle["clip_gain"]["t"]) > 34


def test_different_chords_do_not_get_same_chord_handoff(picture):
    picture.data["sound"] = {"music": {"kind": "bed"}, "beds": [
        {"id": "first", "from": 0, "to": 3, "params": {"notes": ["C4"]}},
        {"id": "second", "from": 1, "to": 4, "params": {"notes": ["D4"]}}]}
    sheet = spotting.build(picture, render_cue=bed_signal)
    assert not sheet["bed_handoffs"]
    assert all("clip_gain" not in c for c in sheet["cues"])


# --- a beat's size, runs and an act's colour (all opt-in through the palette) ---------------------------------------

def _midi(note):
    from slate.sound.effects import sampler

    return sampler.midi_of(note)


def test_without_the_new_tables_spotting_is_unchanged(picture):
    """sizes, walk and acts are opt-in: a film whose palette has none of them spots exactly as before."""
    events(picture, [{"shot": "S01", "id": f"row{i}", "kind": "tap", "t": 1.0 + 0.3 * i, "size": "l"} for i in range(4)])
    plain = spotting.build(picture, seed=3, render_cue=render)
    assert all("walk" not in c["provenance"] for c in plain["cues"])
    assert all(abs(c["params"]["cents"]) <= 4.0 for c in plain["cues"])


def test_a_large_beat_is_a_deeper_knock_not_a_louder_small_one(picture):
    events(picture, [{"shot": "S01", "id": "small", "kind": "land", "t": 1.0, "size": "s"},
                     {"shot": "S01", "id": "large", "kind": "land", "t": 3.0, "size": "l"}])
    base = {c["event"]: c for c in spotting.build(picture, seed=3, render_cue=render)["cues"]}
    picture.palette["sizes"] = {"s": {"gain_db": -3}, "l": {"gain_db": 2, "octave": -1}}
    sized = {c["event"]: c for c in spotting.build(picture, seed=3, render_cue=render)["cues"]}
    assert sized["large"]["params"]["cents"] == pytest.approx(base["large"]["params"]["cents"] - 1200)
    assert sized["large"]["gain"] == pytest.approx(base["large"]["gain"] + 2)
    assert sized["small"]["gain"] == pytest.approx(base["small"]["gain"] - 3)
    assert sized["small"]["params"]["cents"] == pytest.approx(base["small"]["params"]["cents"])


def test_a_run_of_beats_walks_up_the_scale(picture):
    """Four checks in a row are one gesture rising a step per item; a lone beat and another kind stay out of it."""
    rows = [{"shot": "S01", "id": f"check_{i}", "kind": "tap", "t": 1.0 + 0.4 * i} for i in range(4)]
    rows += [{"shot": "S01", "id": "lone", "kind": "tap", "t": 2.9}, {"shot": "S01", "id": "card", "kind": "land", "t": 2.1}]
    events(picture, rows)
    picture.palette["walk"] = {"gap_s": 0.5, "min": 3}
    cues = {c["event"]: c for c in spotting.build(picture, seed=3, render_cue=render)["cues"]}
    pitches = [_midi(cues[f"check_{i}"]["params"]["pitch"]) for i in range(4)]
    assert pitches == sorted(pitches) and len(set(pitches)) == 4
    assert [cues[f"check_{i}"]["provenance"]["walk"]["i"] for i in range(4)] == [0, 1, 2, 3]
    assert "walk" not in cues["lone"]["provenance"] and "walk" not in cues["card"]["provenance"]
    assert {p % 12 for p in pitches} <= {9, 0, 2, 4, 7}  # A minor's pentatonic: A C D E G


def test_an_act_has_its_own_colour(picture):
    jsonio.write_atomic(picture.path("timing/visual_events/act2.json"),
                        [{"shot": "S02", "id": "press2", "kind": "tap", "t": 4.0}])
    picture.palette["acts"] = {"2": {"gain_db": -2, "kinds": {"tap": {"params": {"kind": "rim"}}}}}
    cues = {c["event"]: c for c in spotting.build(picture, seed=3, render_cue=render)["cues"]}
    assert cues["press"]["params"]["kind"] == "block" and cues["press2"]["params"]["kind"] == "rim"
    picture.palette.pop("acts")
    plain = {c["event"]: c for c in spotting.build(picture, seed=3, render_cue=render)["cues"]}
    assert cues["press2"]["gain"] == pytest.approx(plain["press2"]["gain"] - 2)
    assert cues["press"]["gain"] == pytest.approx(plain["press"]["gain"])


def test_an_authored_sound_keeps_its_register(picture):
    """An override with exact pitches (a sonic logo) opts out of size weight; its gain and pitch stay as written."""
    events(picture, [{"shot": "S01", "id": "logo", "kind": "land", "t": 1.0, "size": "l"}])
    picture.palette["sizes"] = {"l": {"gain_db": 2, "octave": -1}}
    jsonio.write_atomic(picture.path("film.json"), {"id": "neutral", "sound": {"event_overrides": [
        {"shot": "S01", "event": "logo", "params": {"pitch": "D5", "size_weight": False}}]}})
    picture.data["sound"] = {"event_overrides": [{"shot": "S01", "event": "logo",
                                                  "params": {"pitch": "D5", "size_weight": False}}]}
    cue, = spotting.build(picture, seed=3, render_cue=render)["cues"]
    assert cue["params"]["pitch"] == "D5" and abs(cue["params"]["cents"]) <= 4.0 and "size_weight" not in cue["params"]


def test_a_move_may_cross_its_acts_shots_but_not_the_act(tmp_path):
    """A shot is a narration line: a move that starts during one line and lands in the next is one move. Its span may
    cross the act's shots; it may not leave the act."""
    shots = {"fps_num": 24, "fps_den": 1, "width": 640, "height": 360, "total_frames": 144,
             "acts": [{"index": 1, "shots": ["S01", "S02"]}, {"index": 2, "shots": ["S03"]}],
             "shots": [{"id": "S01", "act": 1, "frame_in": 0, "frame_out": 48},
                       {"id": "S02", "act": 1, "frame_in": 48, "frame_out": 96},
                       {"id": "S03", "act": 2, "frame_in": 96, "frame_out": 144}]}
    brand = {"sound": {"key": "A minor"}}
    film = Film(tmp_path, {"id": "neutral"}, shots, 24, 1, 8000, 6.0, {}, brand, palette.from_brand(brand), None)
    jsonio.write_atomic(film.path("timing/shots.json"), shots)
    jsonio.write_atomic(film.path("film.json"), film.data)
    jsonio.write_atomic(film.path("timing/visual_events/act2.json"), {"silent": True, "reason": "A held frame."})
    crossing = {"shot": "S02", "id": "glide", "kind": "whoosh", "t": 2.4, "start": 1.6, "dur": 0.8}
    jsonio.write_atomic(film.path("timing/visual_events/act1.json"), [crossing])
    assert spotting.build(film, render_cue=render)["cues"]
    jsonio.write_atomic(film.path("timing/visual_events/act1.json"), [dict(crossing, t=3.9, start=3.6, dur=0.6)])
    with pytest.raises(Refused):
        spotting.build(film, render_cue=render)
