"""Verification uses the render's real film clock, placement offset and approved legacy anchors."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from slate import jsonio
from slate.sound import audio, verify
from slate.sound.effects import engine, palette
from slate.sound.film import Film


@pytest.fixture
def context(tmp_path):
    film = Film(tmp_path, {"id": "neutral"}, {"shots": []}, 25, 1, 16000, 4.0,
                {}, None, copy.deepcopy(palette.STARTING_PALETTE), None)
    return SimpleNamespace(film=film, inputs=lambda *args: {})


def sheet(context, **changes):
    cue = {"id": "hit-1", "kind": "tap", "sound": "wood", "stem": "ui", "type": "wood", "t": 0.7,
           "beat": 0.6, "anchor": "onset", "align": "end", "params": {"kind": "block"}, **changes}
    value = {"cues": [cue]}
    jsonio.write_atomic(context.film.path("sound/sound_cues.json"), value)
    return value


def test_engine_renderer_binds_film_folder_and_nondefault_sample_rate(context, monkeypatch):
    from slate.sound import effects
    monkeypatch.setattr(effects, "status", lambda: {"approved": True})
    calls = []
    def render(cue, film_dir=None, sample_rate=48000):
        calls.append((cue, film_dir, sample_rate))
        return np.ones((20, 2)), 0
    monkeypatch.setattr(engine, "render_cue", render)
    bound = verify._engine_renderer(context.film)
    bound({"id": "cue"})
    assert calls == [({"id": "cue"}, context.film.dir, 16000)]
    assert verify._engine_renderer() is render
    monkeypatch.setattr(effects, "status", lambda: {"approved": False})
    assert verify._engine_renderer(context.film) is None and verify._engine_renderer() is None


def test_sync_includes_the_renderer_placement_offset(context, monkeypatch):
    sheet(context)
    y = np.ones((1600, 2)) * 0.1
    monkeypatch.setattr(verify, "_engine_renderer", lambda film=None: lambda cue: (y, -1600))
    result = verify.gate_cue_sync(context)
    assert result.status == "PASS" and result.details["max_abs_delta_ms"] == 0


def test_rendered_file_end_alignment_obeys_the_same_placement(context):
    audio.write_wav(context.film.path("sound/hit.wav"), np.ones((1600, 2)) * 0.1, context.film.sr)
    sheet(context, file="sound/hit.wav")
    result = verify.gate_cue_sync(context)
    assert result.status == "PASS" and result.details["max_abs_delta_ms"] == 0


def test_silent_audio_has_no_measurable_anchor(context, monkeypatch):
    sheet(context)
    monkeypatch.setattr(verify, "_engine_renderer", lambda film=None: lambda cue: (np.zeros((1600, 2)), 0))
    result = verify.gate_cue_sync(context)
    assert result.status == "UNCHECKED" and "no audible anchor" in result.summary


@pytest.mark.parametrize("sr", [16000, 24000, 48000])
def test_legacy_start_and_end20_use_the_approved_two_ms_envelope(sr):
    mono = np.zeros(sr // 20)
    mono[sr // 100:sr // 50] = np.hanning(sr // 100)
    y = np.stack([mono, mono], axis=1)
    width = int(0.002 * sr)
    envelope = np.sqrt(np.maximum(np.convolve(mono**2, np.ones(width)/width, mode="same"), 0))
    first = int(np.flatnonzero(envelope >= envelope.max() * 0.01)[0])
    last = int(np.flatnonzero(envelope >= envelope.max() * 0.1)[-1] + 1)
    assert verify.anchor_offset(y, "start", sr) == first
    assert verify.anchor_offset(y, "onset24", sr) == first
    assert verify.anchor_offset(y, "end20", sr) == last


def test_schema_accepts_approved_anchors_and_describes_placement(context):
    for anchor in ("start", "onset24", "end20"):
        value = sheet(context, anchor=anchor, gain=-20, reverb=-12, family="accents")
        assert jsonio.validate(value, "sound_cues") == []
    value["cues"][0]["align"] = "unknown"
    assert jsonio.validate(value, "sound_cues")


def test_legacy_peak20_uses_the_approved_steadier_fifty_ms_peak():
    sr = 16000
    mono = np.zeros(sr)
    mono[3200:3520] = 1.0  # a loud, short noise fluctuation
    mono[8000:9600] = .7   # the sustained crest chosen by the approved fifty-millisecond rule
    y = np.stack([mono, mono], axis=1)
    assert verify.anchor_offset(y, "peak20", sr) == verify.anchor_offset(y, "peak50", sr)
    assert verify.anchor_offset(y, "peak20", sr) > 8000


def mixed_report(context, *, after=.6, rows=True):
    sheet(context)
    folder = context.film.dir
    mix_path = folder / "sound/mix/final_mix.wav"
    mix_path.parent.mkdir(parents=True, exist_ok=True)
    mix_path.write_bytes(b"the bound mix")
    jsonio.write_atomic(folder / "sound/stems/stems.json", {"producer": "slate sfx", "stems": {}})
    report = {"scope": "master", "sample_rate": context.film.sr,
              "mix_sha256": jsonio.sha256(mix_path),
              "cue_sheet_sha256": jsonio.sha256(folder / "sound/sound_cues.json"),
              "rows": [{"id": "hit-1", "kind": "tap", "rule": "onset", "beat": .6,
                        "after_anchor_s": after, "delta_ms": 0, "within_frame": True}] if rows else [],
              "unmeasured": []}
    jsonio.write_atomic(folder / "sound/mix/effects_sync.json", report)
    jsonio.write_atomic(folder / "sound/mix/render_log.json", {
        "sample_rate": context.film.sr, "mix_sha256": report["mix_sha256"],
        "cue_sheet_sha256": report["cue_sheet_sha256"], "cues": [{"hit": "hit-1"}]})
    return folder


def test_generated_effects_require_fresh_complete_post_master_evidence(context):
    folder = mixed_report(context)
    assert verify.gate_cue_sync(context).status == "PASS"
    (folder / "sound/mix/final_mix.wav").write_bytes(b"different mix")
    assert verify.gate_cue_sync(context).status == "UNCHECKED"
    mixed_report(context, rows=False)
    assert verify.gate_cue_sync(context).status == "UNCHECKED"
    (folder / "sound/mix/effects_sync.json").unlink()
    assert verify.gate_cue_sync(context).status == "UNCHECKED"


def test_post_master_measurement_enforces_current_frame_limit_not_saved_pass_boolean(context):
    mixed_report(context, after=.66)
    result = verify.gate_cue_sync(context)
    assert result.status == "FAIL" and result.details["max_abs_delta_ms"] == 60


@pytest.mark.parametrize("alignment, t, first, last", [("start", .5, .58, .9), ("end", 1.5, .88, 1.2)])
def test_legacy_count_endpoints_use_film_clock_and_do_not_change_approved_cue(context, monkeypatch, alignment, t, first, last):
    from slate.sound.effects import rendering, voices

    value = sheet(context, type="countup3", kind="count", anchor="clicks", t=t, beat=first, beat_end=last,
                  align=alignment, params={"dur": .4, "from": 0, "to": 5, "hold_frames": 2,
                                         "scale": ["C4", "E4", "G4"], "kind": "block", "ease": "linear"})
    before = copy.deepcopy(value)
    def render(cue):
        return rendering.render_cue(cue, sample_rate=context.film.sr, clock=(25, 1))
    monkeypatch.setattr(verify, "_engine_renderer", lambda film=None: render)
    result = verify.gate_cue_sync(context)
    assert result.status == "PASS", result.details
    assert result.details["rows"][0]["anchor_s"] == pytest.approx(first)
    assert result.details["rows"][-1]["anchor_s"] == pytest.approx(last)
    assert value == before


def test_mixed_legacy_count_checks_last_endpoint_too(context):
    folder = mixed_report(context)
    value = sheet(context, anchor="clicks", beat_end=.9)
    sync = jsonio.read(folder / "sound/mix/effects_sync.json")
    sync["cue_sheet_sha256"] = jsonio.sha256(folder / "sound/sound_cues.json")
    sync["rows"][0].update(rule="clicks", end_anchor_s=1.0, signal="scheduled_clicks")
    jsonio.write_atomic(folder / "sound/mix/effects_sync.json", sync)
    log = jsonio.read(folder / "sound/mix/render_log.json")
    log["cue_sheet_sha256"] = sync["cue_sheet_sha256"]
    jsonio.write_atomic(folder / "sound/mix/render_log.json", log)
    result = verify.gate_cue_sync(context)
    assert result.status == "FAIL" and result.details["over_one_frame"][0]["id"] == "hit-1.last"
