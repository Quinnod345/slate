"""The studio polish: every stage keeps the length, loudness comes back, the de-esser and the edge cut act where they
should, strengths are data and order cleanly, the report says what changed, and the film stage pins, records and never
replaces the raw take. All on synthetic audio (numpy), offline."""

import json

import numpy as np
import pytest

from slate import jsonio, ledger
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.voice import dsp, polish
from slate.voice.film import VoiceFilm, get_pin

from voice_helpers import rich_take, rich_voice

SR = 48000
TEXT = "Slate hears each word, then fits it to the cut."


@pytest.fixture(scope="module")
def take():
    x, words = rich_voice(TEXT, seed=3)
    return x, words


def test_every_stage_keeps_the_length_sample_for_sample():
    rng = np.random.default_rng(0)
    for n in (4801, 48000 + 17):
        x = rng.standard_normal(n) * 0.1
        assert len(dsp.highpass(x, SR, 80)) == n
        assert len(dsp.peak(x, SR, 900, -1.5, 2.0)) == n
        assert len(dsp.shelf(x, SR, 350, 2.0, high=False)) == n
        assert len(dsp.shelf(x, SR, 8000, -3.5, high=True)) == n
        assert len(dsp.compressor(x, SR, -20, 2.5, 15, 150)) == n
        band, rest = dsp.band_split(x, SR, 5000, 9000)
        assert len(band) == len(rest) == n
        assert np.max(np.abs(band + rest - x)) < 1e-12  # the band and its exact complement
        stats: dict = {}
        y = dsp.dynamic_band(x, SR, polish.NEUTRAL_A["deess"], stats)
        assert len(y) == n and set(stats) == {"max_cut_db", "time_cutting_pct", "mean_cut_while_cutting_db"}
        assert len(dsp.room(x, SR, -20.0, 0.3)) == n


def test_the_whole_chain_keeps_length_at_any_rate_and_in_stereo(take):
    x, _ = take
    for sr in (48000, 44100):
        y, rep = polish.apply(x[: 3 * sr], sr, polish.NEUTRAL_A)
        assert len(y) == 3 * sr and rep["samples"] == 3 * sr
    st = np.stack([x, 0.8 * x], axis=1)
    y, rep = polish.apply(st, SR, polish.NEUTRAL_A)
    assert y.shape == st.shape
    assert abs(rep["lufs_out"] - rep["lufs_in"]) < 0.05


def test_loudness_returns_to_the_takes_own_and_the_peak_stays_under_the_ceiling(take):
    x, _ = take
    for gain_db in (-12.0, 0.0, 6.0):
        xg = x * dsp.db_to_gain(gain_db)
        y, rep = polish.apply(xg, SR, polish.NEUTRAL_B)
        assert abs(rep["lufs_out"] - rep["lufs_in"]) <= 0.05 or rep["peak_trim_db"] < 0
        assert rep["true_peak_out_dbtp"] <= max(-1.0, rep["true_peak_in_dbtp"]) + 0.05


def test_strength_zero_and_silence_leave_the_take_untouched(take):
    x, _ = take
    tuning = polish.tuning_for({})
    res = polish.resolve(tuning, "0")
    assert res["chain"] is None
    y, rep = polish.apply(x, SR, res["chain"])
    assert np.array_equal(y, x) and "strength 0" in rep["note"]
    z = np.zeros(SR)
    y, rep = polish.apply(z, SR, polish.NEUTRAL_A)
    assert np.array_equal(y, z) and "silent" in rep["note"]


def test_the_compressor_follows_its_static_curve():
    t = np.arange(SR) / SR
    x = 0.5 * np.sin(2 * np.pi * 220 * t)  # peak -6 dBFS
    y = dsp.compressor(x, SR, -20.0, 4.0, 1.0, 50.0)
    settled = np.max(np.abs(y[SR // 2:]))
    want = dsp.db_to_gain(-20.0 + (-6.02 + 20.0) / 4.0)  # 14 dB over the threshold comes out 3.5 dB over
    assert abs(20 * np.log10(settled / want)) < 0.5
    assert np.array_equal(dsp.compressor(x, SR, -20.0, 1.0, 1.0, 50.0), x)


def test_the_deesser_cuts_sibilance_and_leaves_vowels_alone():
    with_s, _ = rich_voice(TEXT, seed=3, sibilance=1.0)
    no_s, _ = rich_voice(TEXT, seed=3, sibilance=0.0)
    chain = polish.NEUTRAL_A
    y, rep = polish.apply(with_s, SR, chain)
    eff = polish.summarize_pairs([polish.pair(with_s, y, SR, chain)])
    assert rep["deess"]["time_cutting_pct"] > 1.0 and rep["deess"]["max_cut_db"] > 2.0
    assert eff["change"]["sibilance_peaks_db"] < -2.0
    assert eff["consonants_sibilance_change_db"] < eff["change"]["body_300_2k"]
    _, rep_vowels = polish.apply(no_s, SR, chain)
    assert rep_vowels["deess"]["time_cutting_pct"] < 0.5  # nothing dominates the S band, so nothing is cut


def test_strengths_are_data_and_order_cleanly(take):
    x, _ = take
    tuning = polish.tuning_for({})
    assert tuning["strengths"] == {"A": 0.6, "B": 1.0} and tuning["default_strength"] == "A"
    rows = []
    for s in ("0.3", "A", "0.8", "B"):
        res = polish.resolve(tuning, s)
        y, rep = polish.apply(x, SR, res["chain"])
        eff = polish.summarize_pairs([polish.pair(x, y, SR, res["chain"])])
        rows.append((s, eff["change"]["sibilance_peaks_db"], eff["spectral_distance_db"]))
    sib = [r[1] for r in rows]
    dist = [r[2] for r in rows]
    assert sib == sorted(sib, reverse=True), rows  # stronger, more S taken out
    assert dist == sorted(dist), rows              # stronger, a bigger overall change
    assert polish.resolve(tuning, 0.6)["chain"] == tuning["points"]["A"]  # A's own place is A exactly
    mid = polish.resolve(tuning, 0.8)
    assert mid["between"] == ["A", "B"] and mid["t"] == 0.5
    assert mid["chain"]["deess"]["max_cut_db"] == pytest.approx(7.0)
    third = polish.resolve(tuning, 0.3)["chain"]
    assert third["deess"]["max_cut_db"] == pytest.approx(3.0) and third["deess"]["threshold_db"] == -36.0


def test_bad_strengths_are_usage_errors():
    tuning = polish.tuning_for({})
    for bad in ("C", "1.5", "-0.1", "loud"):
        with pytest.raises(UsageError):
            polish.resolve(tuning, bad)


def test_a_voice_brings_its_own_tuning():
    own = {"points": {"gentle": {"deess": {"max_cut_db": 4.0}}, "warm": {"low_shelf": {"gain_db": 4.0}}},
           "strengths": {"gentle": 0.5, "warm": 1.0}, "default_strength": "warm", "verify": {"loudness_lu": 0.3},
           "source": "measured on the voice's casting reads"}
    tuning = polish.tuning_for({"polish": own})
    assert tuning["default_strength"] == "warm" and tuning["verify"]["loudness_lu"] == 0.3
    gentle = polish.resolve(tuning, "gentle")["chain"]
    assert gentle["deess"]["max_cut_db"] == 4.0 and gentle["deess"]["band_hz"] == [5000.0, 9000.0]  # filled in
    assert polish.resolve(tuning, None)["label"] == "warm"


def test_a_film_chain_in_its_tuple_form_carries_over(take):
    x, _ = take
    film_form = {"hpf_hz": 80, "eq_cut": [900, -1.5, 2.0], "comp": [-19.0, 2.0, 15.0, 150.0],
                 "edge": {"band": [2000, 5000], "thresh_db": -36.0, "rel_db": -12.0, "ratio": 2.5, "max_cut_db": 3.0,
                          "attack_ms": 1.5, "release_ms": 50.0},
                 "deess": {"band": [5500, 12500], "thresh_db": -36.0, "rel_db": -10.0, "ratio": 3.0,
                           "max_cut_db": 6.0, "attack_ms": 1.0, "release_ms": 60.0},
                 "presence": [3400, 1.0, 0.9], "low_shelf": [350, 2.0], "high_shelf": [8000, -3.5],
                 "room": {"wet_db": -21.0, "rt60_s": 0.28}}
    chain = polish.from_film_chain(film_form)
    assert chain["deess"]["band_hz"] == [5500.0, 12500.0] and chain["compressor"]["ratio"] == 2.0
    assert not jsonio.validate({"provider": "elevenlabs", "source": "library", "polish": {"points": {"A": chain}}},
                               "voice_profile")
    y, rep = polish.apply(x, SR, chain)
    assert len(y) == len(x)


def test_the_report_says_what_changed(tmp_path, take):
    x, _ = take
    from slate.voice import audio

    src = tmp_path / "t01.wav"
    audio.write_wav(src, x, SR, 24)
    rep = polish.polish_file(src, tmp_path / "t01.polished.wav", polish.NEUTRAL_A)
    st, eff = rep["stages"], rep["effect"]
    for key in ("samples", "samples_out", "lufs_in", "lufs_out", "true_peak_in_dbtp", "true_peak_out_dbtp",
                "peak_trim_db", "compressor_gr_db", "edge", "deess"):
        assert key in st, key
    for side in ("before", "after"):
        for key in ("sibilance", "sibilance_peaks_db", "edge", "edge_peaks_db", "tilt_db", "warmth_db", "low_mid",
                    "clarity_1k_5k"):
            assert eff[side].get(key) is not None, (side, key)
    assert eff["change"]["sibilance_peaks_db"] < 0 and eff["spectral_distance_db"] > 0
    assert set(eff["thirds_change_db"]) >= {"1000", "4000", "8000"}
    assert rep["verified"]["ok"], rep["verified"]
    assert st["samples"] == st["samples_out"]


def test_verify_catches_length_loudness_and_over_processing():
    chain = polish.NEUTRAL_A
    good_stages = {"samples": 100, "samples_out": 100, "lufs_in": -20.0, "lufs_out": -20.1, "true_peak_in_dbtp": -6,
                   "true_peak_out_dbtp": -5.5, "deess": {"time_cutting_pct": 5.0}}
    good_effect = {"change": {"sibilance_peaks_db": -4.0, "edge_peaks_db": -1.0}, "spectral_distance_db": 2.5,
                   "clarity_1k_5k_removed_db": 0.8}
    assert polish.verify(good_stages, good_effect, chain)["ok"]
    cases = [({"samples_out": 99}, {}, "length"), ({"lufs_out": -20.6}, {}, "loudness moved"),
             ({}, {"spectral_distance_db": 7.5}, "spectral change"), ({}, {"clarity_1k_5k_removed_db": 3.1}, "1-5 kHz"),
             ({}, {"change": {"sibilance_peaks_db": -0.1, "edge_peaks_db": -1.0}}, "de-esser acted"),
             ({}, {"change": {"sibilance_peaks_db": -20.0, "edge_peaks_db": -1.0}}, "more than this chain"),
             ({}, {"change": {"sibilance_peaks_db": -4.0, "edge_peaks_db": 2.0}}, "harsher"),
             ({"true_peak_out_dbtp": -0.2}, {}, "true peak")]
    for st, eff, words in cases:
        v = polish.verify({**good_stages, **st}, {**good_effect, **eff}, chain)
        assert not v["ok"] and any(words in p for p in v["problems"]), (words, v["problems"])


# ---------------------------------------------------------------------------------------------------------------
# The film stage


def _real_takes(vf, shots=("S01", "S02", "S04")):
    out = {}
    for i, shot in enumerate(shots):
        out[shot] = rich_take(vf, shot, seed=10 + i)
    return out


def test_polishing_a_film_pins_records_and_never_replaces_the_raw_take(voice_film):
    vf = VoiceFilm(voice_film)
    raws = _real_takes(vf)
    before = {s: jsonio.sha256(p) for s, (p, _) in raws.items()}
    res = polish.run(voice_film, strength="A")
    assert res["strength"] == "A" and len(res["polished"]) == 3 and not res["skipped"]
    assert all(r["verified"]["ok"] for r in res["polished"]), [r["verified"] for r in res["polished"]]
    vf = VoiceFilm(voice_film)
    for shot, (raw, _) in raws.items():
        assert jsonio.sha256(raw) == before[shot]  # perfect, don't replace
        final = vf.final_audio(shot)
        assert final.name == "t01.polished.wav" and vf.unpolished_audio(shot) == raw
        assert vf.final_stage(shot) == "polished" and vf.timing_stage(shot) == "raw"
        report = jsonio.read(vf.take_dir(shot) / "t01.polish.json")
        assert not jsonio.validate(report, "voice_polish_report")
        assert report["stages"]["samples"] == report["stages"]["samples_out"]
        lock = jsonio.read(voice_film / "film.lock.json")["files"][vf.rel(final)]
        assert lock["producer"] == "slate voice polish" and vf.rel(raw) in lock["inputs"]
        assert "the raw take is unchanged" in (lock.get("note") or "")
        assert (vf.take_json(shot, "t01")["stages"]["polish"]["chain_sha256"]
                == get_pin(voice_film, polish.PIN_KEY)["chain_sha256"])
    pinned = get_pin(voice_film, polish.PIN_KEY)
    assert pinned["strength"] == "A" and pinned["chain"] == polish.tuning_for(vf.profile)["points"]["A"]
    assert (voice_film / "voice" / "polish" / "summary.json").is_file()
    assert ledger.rows() == []  # the polish is local: nothing is spent


def test_the_pin_holds_until_a_person_repins_it(voice_film):
    vf = VoiceFilm(voice_film)
    _real_takes(vf, ("S01",))
    polish.run(voice_film, only="S01", strength="A")
    res = polish.run(voice_film, only="S01", strength="B")
    assert res["strength"] == "A" and "kept" in res["pin"]
    with pytest.raises(Refused):
        polish.run(voice_film, only="S01", strength="B", repin=True)
    res = polish.run(voice_film, only="S01", strength="B", repin=True, approved_by="the owner",
                     reason="the trial: B is warmer and still the same voice")
    assert res["strength"] == "B" and get_pin(voice_film, polish.PIN_KEY)["strength"] == "B"


def test_a_re_voiced_line_gets_the_films_pinned_polish(voice_film):
    vf = VoiceFilm(voice_film)
    _real_takes(vf, ("S01",))
    polish.run(voice_film, only="S01", strength="0.8")
    rich_take(vf, "S01", take="t02", seed=99)  # the line is voiced again later
    res = polish.run(voice_film, only="S01")  # no strength given: the film's pin applies
    assert res["strength"] == "0.8"
    tj = VoiceFilm(voice_film).take_json("S01", "t02")
    assert tj["stages"]["polish"]["strength"] == "0.8"


def test_cleaning_or_pacing_again_drops_the_old_polish(voice_film):
    vf = VoiceFilm(voice_film)
    _real_takes(vf, ("S01",))
    polish.run(voice_film, only="S01")
    vf.update_take("S01", "t01", polished=None)  # what clean and pace do to a take they redo
    assert VoiceFilm(voice_film).final_stage("S01") == "raw"
    import inspect

    from slate.voice import clean, pace

    assert "polished=None" in inspect.getsource(clean.clean_take)
    assert "polished=None" in inspect.getsource(pace.pace_take)


def test_approving_locks_the_polished_take_and_a_new_polish_needs_a_go(voice_film):
    vf = VoiceFilm(voice_film)
    _real_takes(vf, ("S01",))
    polish.run(voice_film, only="S01")
    code, out, err = run_cli(["voice", "approve", "S01", "--by", "the owner", "--film", str(voice_film), "--json"])
    assert code == 0, err
    assert "voice/takes/S01/t01.polished.wav" in json.loads(out)["locked"]
    with pytest.raises(Refused):
        polish.run(voice_film, only="S01")


def test_trials_write_listening_files_and_pin_nothing(voice_film):
    vf = VoiceFilm(voice_film)
    _real_takes(vf, ("S01", "S02"))
    res = polish.trial(voice_film, ["A", "B", "0.3"], sheet=True)
    assert set(res["strengths"]) == {"A", "B", "0.3"}
    assert (voice_film / "voice" / "polish" / "try" / "reel.wav").is_file()
    assert (voice_film / "voice" / "polish" / "try" / "s0p3" / "S01.wav").is_file()
    for png in res["sheets"]:
        assert (voice_film / png).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    a, b = res["strengths"]["A"]["effect"], res["strengths"]["B"]["effect"]
    assert b["change"]["sibilance_peaks_db"] < a["change"]["sibilance_peaks_db"]
    # the long-term spectrum of every set, pooled over the lines, charted against natural speech
    spec = res["spectrum"]
    assert set(spec["sets"]) == {"unpolished", "A", "B", "0.3"} and spec["sets"]["A"]["takes"] == 2
    assert (voice_film / spec["chart"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    sib = {k: v["bands_db"]["sib_5k_9k"] for k, v in spec["sets"].items()}
    assert sib["B"] < sib["unpolished"]  # the de-esser and the top shelf, seen in the long-term spectrum too
    # the harshest moments, each heard unpolished and then at every strength, level-matched
    harsh = res["harshest"]
    reel = jsonio.read((voice_film / harsh["reel"]).with_suffix(".json"))
    assert harsh["moments"] and len(reel["items"]) == 4 * len(harsh["moments"])
    assert [i["title"].rsplit(": ", 1)[1] for i in reel["items"][:4]] == ["unpolished", "A", "B", "0.3"]
    assert get_pin(voice_film, polish.PIN_KEY) is None
    assert VoiceFilm(voice_film).final_stage("S01") == "raw"


def test_the_command_line(voice_film):
    vf = VoiceFilm(voice_film)
    _real_takes(vf, ("S01", "S02"))
    code, out, err = run_cli(["voice", "polish", "--film", str(voice_film), "--dry-run", "--json"])
    assert code == 0, err
    assert json.loads(out)["would_polish"] == ["S01", "S02"] and get_pin(voice_film, polish.PIN_KEY) is None
    code, out, err = run_cli(["voice", "polish", "--film", str(voice_film), "--strength", "B", "--report"])
    assert code == 0, err
    assert "strength B" in out and "S peaks" in out and "verified" in out
    code, out, err = run_cli(["voice", "polish", "--film", str(voice_film), "--strength", "loud", "--json"])
    assert code == 2


def test_an_edge_rise_is_harsh_only_when_the_take_ends_brighter_than_the_film():
    from slate.voice.gates import EDGE_CONTEXT_LINES, _edge_rises_in_context

    after = {f"S{i:02d}": -27.0 - 0.1 * i for i in range(10)}  # the film's polished lines, median about -27.45 dB
    after.update({"DARK": -29.2, "BRIGHT": -25.0})
    rises = {"DARK": "the 2-5 kHz peaks rose +1.43 dB: harsher, not smoother",
             "BRIGHT": "the 2-5 kHz peaks rose +1.20 dB: harsher, not smoother"}
    excused, kept = _edge_rises_in_context(after, rises)
    assert set(excused) == {"DARK"} and "under the film's median" in excused["DARK"]
    assert kept == {"BRIGHT": rises["BRIGHT"]}
    few = {k: after[k] for k in list(after)[:EDGE_CONTEXT_LINES - 1]} | {"DARK": -29.2}
    assert _edge_rises_in_context({k: v for k, v in list(few.items())[:EDGE_CONTEXT_LINES - 1]},
                                  {"DARK": rises["DARK"]}) == ({}, {"DARK": rises["DARK"]})
